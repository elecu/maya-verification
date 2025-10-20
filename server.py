# server.py
# FastAPI verification server for MĀYĀ.
# Deploy on Render with:
#   Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT

import os
import time
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="MAYA Verification")

# ----- Helpers to read env -----
def _read_csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "") or ""
    return set(x.strip() for x in raw.split(",") if x.strip())

# Environment variables (set these in Render)
ALLOWED_TOKENS   = _read_csv_env("ALLOWED_TOKENS")      # e.g. "WORKSHOP_2025,VIP_1"
BLOCKED_MACHINES = _read_csv_env("BLOCKED_MACHINES")    # optional: hashed machine_ids
APP_VERSION      = os.getenv("APP_VERSION", "").strip() # e.g. "1.0.0" (empty = ignore)
KILL_SWITCH      = os.getenv("KILL_SWITCH", "0").strip()# "1" = deny everyone

# ----- Models -----
class CheckRequest(BaseModel):
    token: str
    machine_id: str
    version: Optional[str] = None

class CheckResponse(BaseModel):
    allow: bool
    reason: str
    ttl_seconds: int = 60  # client may cache decision for up to N seconds

# ----- Routes -----
@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}

@app.post("/check", response_model=CheckResponse)
def check(req: CheckRequest):
    # Global kill switch
    if KILL_SWITCH == "1":
        return Check
