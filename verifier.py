# verifier.py — fast-fail version with total deadline
import json, time, uuid, hashlib, getpass, platform, os
from typing import Tuple
import requests, certifi

# ---------- MODOS (elige uno) ----------
# FAST: falla en ~4-6s si no hay red/servidor
FAST = dict(CONNECT=2, READ=3, WARMUP_TRIES=1, CHECK_TRIES=1, BACKOFF=0.4, TOTAL_DEADLINE=6)
# BALANCED: un poco más paciente (~10-12s)
BALANCED = dict(CONNECT=3, READ=5, WARMUP_TRIES=2, CHECK_TRIES=2, BACKOFF=0.8, TOTAL_DEADLINE=12)
# RENDER_FRIENDLY: tolera cold-start (~18-22s)
RENDER_FRIENDLY = dict(CONNECT=4, READ=8, WARMUP_TRIES=3, CHECK_TRIES=2, BACKOFF=1.1, TOTAL_DEADLINE=20)

# Selección del modo (por defecto: FAST). Puedes cambiar a BALANCED o RENDER_FRIENDLY.
MODE = os.getenv("MAYA_VERIFY_MODE", "FAST").upper()
CFG = {"FAST": FAST, "BALANCED": BALANCED, "RENDER_FRIENDLY": RENDER_FRIENDLY}.get(MODE, FAST)

CONNECT_TIMEOUT   = CFG["CONNECT"]
READ_TIMEOUT      = CFG["READ"]
WARMUP_TRIES      = CFG["WARMUP_TRIES"]
CHECK_TRIES       = CFG["CHECK_TRIES"]
BACKOFF           = CFG["BACKOFF"]
TOTAL_DEADLINE    = CFG["TOTAL_DEADLINE"]

def _deadline_remaining(start: float) -> float:
    return max(0.0, TOTAL_DEADLINE - (time.monotonic() - start))

def build_machine_id() -> str:
    mac  = uuid.getnode()
    user = getpass.getuser()
    sysid = platform.platform()
    raw = f"{mac}-{user}-{sysid}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _session() -> requests.Session:
    s = requests.Session()
    s.verify = certifi.where()
    # Sin reintentos automáticos: controlamos nosotros el timing
    return s

def _warmup(base_url: str, start: float) -> None:
    """Ping /health pocas veces para 'despertar' el server. No bloquea si se acaba el deadline."""
    url = base_url.rstrip("/") + "/health"
    sess = _session()
    for i in range(WARMUP_TRIES):
        if _deadline_remaining(start) <= 0:
            return
        try:
            sess.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            return  # con un 200 o incluso 404 ya sabemos que respondió algo
        except Exception:
            # breve backoff pero respetando el deadline
            sleep_s = min(BACKOFF * (2 ** i), _deadline_remaining(start))
            if sleep_s > 0:
                time.sleep(sleep_s)

def _post_check(base_url: str, payload: dict, start: float) -> Tuple[bool, str, int]:
    url = base_url.rstrip("/") + "/check"
    sess = _session()
    try:
        r = sess.post(url, json=payload, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except Exception as e:
        return (False, f"Network error: {e}", 5)

    if r.status_code != 200:
        return (False, f"Server error: {r.status_code}", 5)

    try:
        data = r.json()
    except json.JSONDecodeError:
        return (False, "Bad JSON from server.", 5)

    allow = bool(data.get("allow"))
    reason = str(data.get("reason", ""))
    ttl = int(data.get("ttl_seconds", 5))
    return (allow, reason, ttl)

def check_online(server_url: str, token: str, version: str) -> Tuple[bool, str, int]:
    start = time.monotonic()
    payload = {"token": token, "machine_id": build_machine_id(), "version": version}

    # 1) Warm-up muy corto (o nulo según el modo)
    _warmup(server_url, start)

    # 2) Intentos de /check hasta alcanzar deadline
    last_reason, last_ttl = "Timeout", 5
    for i in range(CHECK_TRIES):
        if _deadline_remaining(start) <= 0:
            break
        ok, reason, ttl = _post_check(server_url, payload, start)
        if ok:
            return ok, reason, ttl
        last_reason, last_ttl = reason, ttl
        sleep_s = min(BACKOFF * (2 ** i), _deadline_remaining(start))
        if sleep_s > 0:
            time.sleep(sleep_s)

    # 3) Si rebasó el deadline, falla rápido con mensaje claro
    if _deadline_remaining(start) <= 0:
        return False, f"Timed out after ~{TOTAL_DEADLINE}s", 5
    return False, last_reason, last_ttl

def require_permission_or_exit(server_url: str, token: str, version: str) -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw()
    allowed, reason, _ = check_online(server_url, token, version)
    if allowed:
        root.destroy()
        return
    messagebox.showerror("MĀYĀ", f"No permission: {reason}")
    root.destroy()
    raise SystemExit(1)
