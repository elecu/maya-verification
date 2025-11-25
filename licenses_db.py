import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Dict  # NEW: for type hint of scan_licenses_and_notify return

import json               # NEW: for JSON encoding in webhook helper
import urllib.request     # NEW: for sending HTTP POST without extra dependencies
import urllib.error       # NEW: to catch HTTP errors

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# Database URL comes from Render environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# SQLAlchemy setup
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(14), unique=True, index=True, nullable=False)  # XXXX-XXXX-XXXX
    email = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, nullable=False, default=True)

    devices = relationship(
        "LicenseDevice",
        back_populates="license",
        cascade="all, delete-orphan",
    )


class LicenseDevice(Base):
    __tablename__ = "license_devices"
    __table_args__ = (
        UniqueConstraint("license_id", "machine_id", name="uq_license_machine"),
    )

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    machine_id = Column(String(128), nullable=False, index=True)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)

    license = relationship("License", back_populates="devices")


ALPHABET = string.ascii_uppercase + string.digits


def generate_license_code() -> str:
    """
    Generate a code like XXXX-XXXX-XXXX using A–Z and 0–9.
    """
    def block() -> str:
        return "".join(secrets.choice(ALPHABET) for _ in range(4))

    return f"{block()}-{block()}-{block()}"


def init_db() -> None:
    """
    Create tables if they do not exist.
    """
    Base.metadata.create_all(bind=engine)


def create_license(db: Session, email: str) -> License:
    """
    Create a new 1-year license for this email.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=365)

    # Try a few times in case of a very rare collision
    for _ in range(10):
        code = generate_license_code()
        existing = db.query(License).filter(License.code == code).first()
        if existing is None:
            break
    else:
        raise RuntimeError("Could not generate a unique license code")

    lic = License(
        code=code,
        email=email,
        created_at=now,
        expires_at=expires_at,
        active=True,
    )

    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic


# ----------------------------------------------------------------------
# NEW: helpers for license-expiry webhooks (used by cron endpoint)
# ----------------------------------------------------------------------

# Make / webhook URL for license events (expires_soon / expired)
LICENSE_WEBHOOK_URL = os.getenv("LICENSE_WEBHOOK_URL")


def _post_json(url: str, payload: Dict) -> None:
    """
    Small helper to POST JSON without adding new dependencies.
    This is intentionally 'best effort': errors are just printed.
    """
    if not url:
        return

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # We do not need the body; just ensure the request succeeds.
            _ = resp.read()
    except urllib.error.URLError as exc:
        print(f"[license-webhook] Failed to POST to {url}: {exc}")


def notify_license_to_webhook(lic: License, event: str) -> None:
    """
    Send a license-event notification to Make via LICENSE_WEBHOOK_URL.

    The payload shape is:
      {
        "event": "expires_soon" | "expired",
        "email": "...",
        "code": "...",
        "expires_at": "ISO-8601 string"
      }
    """
    if not LICENSE_WEBHOOK_URL:
        # If the env var is not configured, just skip silently.
        return

    payload = {
        "event": event,
        "email": lic.email,
        "code": lic.code,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
    }
    _post_json(LICENSE_WEBHOOK_URL, payload)


def scan_licenses_and_notify(db: Session, now: datetime) -> Dict[str, int]:
    """
    Scan the database for licenses that are:
      - active AND already expired  -> mark inactive + send 'expired'
      - active AND exactly 7 days before expiration -> send 'expires_soon'

    This is called from the cron endpoint in server.py.

    Returns a dict with counters, e.g.:
      { "expires_soon": 3, "expired": 7 }
    """
    # Normalise 'now' to UTC (aware) and derive today's UTC date
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    today_utc = now.date()
    seven_days_later = today_utc + timedelta(days=7)
    cutoff = datetime.combine(
        seven_days_later, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(days=1)

    # We only care about licenses that are still marked as active
    # and that expire within the next 7 days (including already expired).
    candidates = (
        db.query(License)
        .filter(
            License.active.is_(True),
            License.expires_at <= cutoff,
        )
        .all()
    )

    expires_soon_count = 0
    expired_count = 0

    for lic in candidates:
        exp = lic.expires_at.astimezone(timezone.utc)

        if exp <= now:
            # Already expired: mark inactive and notify 'expired'
            lic.active = False
            notify_license_to_webhook(lic, event="expired")
            expired_count += 1
        else:
            # Not yet expired but within <= 7 days.
            # Only send the reminder exactly 7 days before the expiry date.
            if exp.date() == seven_days_later:
                notify_license_to_webhook(lic, event="expires_soon")
                expires_soon_count += 1

    if expires_soon_count or expired_count:
        db.commit()

    return {
        "expires_soon": expires_soon_count,
        "expired": expired_count,
    }
