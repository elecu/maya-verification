# server.py
# Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
import os, time
from fastapi import FastAPI, Request

app = FastAPI(title="māyā Verification — minimal")

@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}

@app.post("/check")
async def check(request: Request):
    # Read raw JSON without pydantic (to avoid any validation/type issues)
    try:
        data = await request.json()
    except Exception as e:
        return {"allow": False, "reason": f"BAD_JSON: {e}", "ttl_seconds": 10}

    # Read env safely
    allowed_tokens = (os.getenv("ALLOWED_TOKENS") or "").split(",")
    allowed_tokens = [t.strip() for t in allowed_tokens if t.strip()]
    app_version = (os.getenv("APP_VERSION") or "").strip()
    kill_switch = (os.getenv("KILL_SWITCH") or "0").strip()
    blocked = (os.getenv("BLOCKED_MACHINES") or "").split(",")
    blocked = [m.strip() for m in blocked if m.strip()]

    token = str(data.get("token", ""))
    machine_id = str(data.get("machine_id", ""))
    version = str(data.get("version", ""))

    # Debug echo (temporal para diagnóstico)
    # Elimina "debug" cuando todo funcione.
    debug = {"received": data, "env": {
        "ALLOWED_TOKENS": allowed_tokens,
        "APP_VERSION": app_version,
        "KILL_SWITCH": kill_switch,
        "BLOCKED_MACHINES": blocked
    }}

    if kill_switch == "1":
        return {"allow": False, "reason": "Temporarily disabled by admin.", "ttl_seconds": 30, "debug": debug}

    if machine_id in blocked:
        return {"allow": False, "reason": "Machine blocked. Please contact māyā team.", "ttl_seconds": 3600, "debug": debug}

    if allowed_tokens and token not in allowed_tokens:
        return {"allow": False, "reason": "Invalid token. Please get a new token on https://mayahep.netlify.app/ or contact māyā team.", "ttl_seconds": 3600, "debug": debug}

    if app_version and version and version != app_version:
        return {"allow": False, "reason": "Update required.", "ttl_seconds": 3600, "debug": debug}

    return {"allow": True, "reason": "OK", "ttl_seconds": 60, "debug": debug}
