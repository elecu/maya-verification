# server.py
# Start Command (Render): uvicorn server:app --host 0.0.0.0 --port $PORT
import os, time
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="MAYA Verification")

def read_csv_env(name: str):
    raw = os.getenv(name, "") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]

# Read env (very permissive; no typing trickery)
ALLOWED_TOKENS   = read_csv_env("ALLOWED_TOKENS")        # e.g. "WORKSHOP_2025,VIP_1"
BLOCKED_MACHINES = read_csv_env("BLOCKED_MACHINES")      # machine_id hashes
APP_VERSION      = (os.getenv("APP_VERSION") or "").strip()
KILL_SWITCH      = (os.getenv("KILL_SWITCH") or "0").strip()

class CheckRequest(BaseModel):
    token: str
    machine_id: str
    version: Optional[str] = None

class CheckResponse(BaseModel):
    allow: bool
    reason: str
    ttl_seconds: int = 60

@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}

@app.post("/check", response_model=CheckResponse)
def check(req: CheckRequest):
    try:
        # Global kill switch
        if KILL_SWITCH == "1":
            return CheckResponse(allow=False, reason="Temporarily disabled by admin.", ttl_seconds=30)

        # Block specific machines
        if req.machine_id in BLOCKED_MACHINES:
            return CheckResponse(allow=False, reason="Machine blocked.", ttl_seconds=3600)

        # Token gate (if list provided)
        if ALLOWED_TOKENS and req.token not in ALLOWED_TOKENS:
            return CheckResponse(allow=False, reason="Invalid token.", ttl_seconds=3600)

        # Optional version gate
        if APP_VERSION and req.version and req.version != APP_VERSION:
            return CheckResponse(allow=False, reason="Update required.", ttl_seconds=3600)

        return CheckResponse(allow=True, reason="OK", ttl_seconds=60)

    except Exception as e:
        # Return the reason so we see what's breaking (only for debugging)
        return CheckResponse(allow=False, reason=f"SERVER_EXCEPTION: {type(e).__name__}: {e}", ttl_seconds=10)

