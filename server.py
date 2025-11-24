# server.py
# Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import ALLOWED_TOKENS
from licenses_db import (
    SessionLocal,
    init_db,
    License,
    LicenseDevice,
    create_license,
)

app = FastAPI(title="māyā Verification — licensing + devices")

# Maximum number of devices per license
MAX_DEVICES = 2


# ----------------------------------------------------------------------
# Database dependency and startup
# ----------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class IssueRequest(BaseModel):
    admin_key: str
    email: str


class IssueResponse(BaseModel):
    code: str
    expires_at: str


class RenewRequest(BaseModel):
    admin_key: str
    license_code: str


class RenewResponse(BaseModel):
    code: str
    old_expires_at: str
    new_expires_at: str


class ResetDevicesRequest(BaseModel):
    admin_key: str
    license_code: str


# ----------------------------------------------------------------------
# Utility: admin check
# ----------------------------------------------------------------------
def require_admin_key(key_from_request: str) -> None:
    admin_key_env = os.getenv("ADMIN_API_KEY") or ""
    if (not admin_key_env) or (key_from_request != admin_key_env):
        raise HTTPException(status_code=403, detail="Forbidden")


# ----------------------------------------------------------------------
# Health endpoint (Render uses this too)
# ----------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


# ----------------------------------------------------------------------
# Licensing admin endpoints
# ----------------------------------------------------------------------
@app.post("/issue", response_model=IssueResponse)
def issue_license(payload: IssueRequest, db: Session = Depends(get_db)):
    """
    Create a new 1-year license for a given email.
    Protected with ADMIN_API_KEY so only you (or your payment backend) can call it.
    """
    require_admin_key(payload.admin_key)

    lic = create_license(db, payload.email)

    return IssueResponse(
        code=lic.code,
        expires_at=lic.expires_at.isoformat(),
    )


@app.post("/renew", response_model=RenewResponse)
def renew_license(payload: RenewRequest, db: Session = Depends(get_db)):
    """
    Renew an existing license for another year and automatically reset its devices.
    """
    require_admin_key(payload.admin_key)

    code = payload.license_code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="license_code is required")

    lic: Optional[License] = db.query(License).filter(License.code == code).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found")

    now = datetime.now(timezone.utc)
    old_expires = lic.expires_at

    # Extend from the later of "now" or "old_expires"
    baseline = old_expires if old_expires > now else now
    new_expires = baseline + timedelta(days=365)

    lic.expires_at = new_expires
    lic.active = True

    # Reset devices on renewal
    devices = (
        db.query(LicenseDevice)
        .filter(LicenseDevice.license_id == lic.id)
        .all()
    )
    removed = len(devices)
    for dev in devices:
        db.delete(dev)

    db.commit()

    return RenewResponse(
        code=lic.code,
        old_expires_at=old_expires.isoformat(),
        new_expires_at=new_expires.isoformat(),
    )


@app.post("/reset_devices")
def reset_devices(payload: ResetDevicesRequest, db: Session = Depends(get_db)):
    """
    Remove all registered devices for a given license code.
    Use this when a user changes laptop/PC in the middle of the year
    and you want to let them start again without renewing.
    """
    require_admin_key(payload.admin_key)

    code = payload.license_code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="license_code is required")

    lic: Optional[License] = db.query(License).filter(License.code == code).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found")

    devices = (
        db.query(LicenseDevice)
        .filter(LicenseDevice.license_id == lic.id)
        .all()
    )

    removed = len(devices)
    for dev in devices:
        db.delete(dev)
    db.commit()

    return {
        "ok": True,
        "license_code": lic.code,
        "removed_devices": removed,
    }


# ----------------------------------------------------------------------
# Main /check endpoint used by the māyā launcher
# ----------------------------------------------------------------------
@app.post("/check")
async def check(request: Request, db: Session = Depends(get_db)):
    """
    Main entry point called by the māyā launcher.

    It validates:
      - global kill switch
      - blocked machines
      - required app version
      - static workshop tokens (ALLOWED_TOKENS)
      - per-license validity in PostgreSQL
      - automatic device registration for the first MAX_DEVICES machines
      - rejects extra machines beyond MAX_DEVICES
      - soon-to-expire warning flag (<= 7 days)
    """
    try:
        data = await request.json()
    except Exception as e:
        return {
            "allow": False,
            "reason": f"BAD_JSON: {e}",
            "ttl_seconds": 5,
        }

    token = str(data.get("token", "")).strip()
    machine_id = str(data.get("machine_id", "")).strip()
    version = str(data.get("version", "")).strip()

    app_version = (os.getenv("APP_VERSION") or "").strip()
    kill_switch = (os.getenv("KILL_SWITCH") or "0").strip()

    raw_blocked = (os.getenv("BLOCKED_MACHINES") or "").split(",")
    blocked = [m.strip() for m in raw_blocked if m.strip()]

    debug = {
        "received": data,
        "env": {
            "APP_VERSION": app_version,
            "KILL_SWITCH": kill_switch,
            "BLOCKED_MACHINES": blocked,
        },
    }

    # ------------------------------------------------------------------
    # 0) Global kill switch
    # ------------------------------------------------------------------
    if kill_switch == "1":
        return {
            "allow": False,
            "reason": "Temporarily disabled by admin.",
            "ttl_seconds": 30,
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # 1) Per-machine blocking
    # ------------------------------------------------------------------
    if machine_id and (machine_id in blocked):
        return {
            "allow": False,
            "reason": "Machine blocked. Please contact the māyā team.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # 2) Required version
    # ------------------------------------------------------------------
    if app_version and version and (version != app_version):
        return {
            "allow": False,
            "reason": "Update required.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # 3) Static workshop tokens (no DB, no device limit)
    # ------------------------------------------------------------------
    if token and (token in ALLOWED_TOKENS):
        return {
            "allow": True,
            "reason": "OK",
            "ttl_seconds": 60,
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # 4) Dynamic licenses stored in PostgreSQL
    # ------------------------------------------------------------------
    if not token:
        return {
            "allow": False,
            "reason": "Missing token.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    now = datetime.now(timezone.utc)

    lic: Optional[License] = db.query(License).filter(License.code == token).first()
    if not lic:
        return {
            "allow": False,
            "reason": "Invalid token. Please check your license key or contact the māyā team.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    if (not lic.active) or (now >= lic.expires_at):
        if lic.active:
            lic.active = False
            db.commit()
        return {
            "allow": False,
            "reason": "License expired. Please renew your license key.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # 5) Automatic device registration (max 2 devices)
    # ------------------------------------------------------------------
    if not machine_id:
        return {
            "allow": False,
            "reason": "Missing machine_id. Please contact the māyā team.",
            "ttl_seconds": 3600,
            "debug": debug,
        }

    device: Optional[LicenseDevice] = (
        db.query(LicenseDevice)
        .filter(
            LicenseDevice.license_id == lic.id,
            LicenseDevice.machine_id == machine_id,
        )
        .first()
    )

    if device:
        # Known machine: just update last_seen
        device.last_seen = now
        db.commit()
    else:
        # New machine: check how many devices are already registered
        current_devices = (
            db.query(LicenseDevice)
            .filter(LicenseDevice.license_id == lic.id)
            .count()
        )

        if current_devices >= MAX_DEVICES:
            return {
                "allow": False,
                "reason": (
                    "This license is already in use on the maximum number of devices (2). "
                    "If you changed computer, please contact the māyā team at mayahep.team@gmail.com."
                ),
                "ttl_seconds": 3600,
                "debug": debug,
            }

        # Register this new device
        new_dev = LicenseDevice(
            license_id=lic.id,
            machine_id=machine_id,
            first_seen=now,
            last_seen=now,
        )
        db.add(new_dev)
        db.commit()

    # ------------------------------------------------------------------
    # 6) Expiry warning (<= 7 days)
    # ------------------------------------------------------------------
    days_left = (lic.expires_at - now).days
    debug["license"] = {
        "email": lic.email,
        "expires_at": lic.expires_at.isoformat(),
        "days_left": days_left,
    }

    if days_left <= 7:
        return {
            "allow": True,
            "reason": "LICENSE_EXPIRES_SOON",
            "ttl_seconds": 60,
            "debug": debug,
        }

    # All checks passed
    return {
        "allow": True,
        "reason": "OK",
        "ttl_seconds": 60,
        "debug": debug,
    }
