# verifier.py — fast-fail version with total deadline
import json, time, uuid, hashlib, getpass, platform, os
from typing import Tuple
import requests, certifi

# ---------- MODES (pick one) ----------
# FAST: fails in ~4–6s if no network/server
FAST = dict(CONNECT=2, READ=3, WARMUP_TRIES=1, CHECK_TRIES=1, BACKOFF=0.4, TOTAL_DEADLINE=6)
# BALANCED: a bit more patient (~10–12s)
BALANCED = dict(CONNECT=3, READ=5, WARMUP_TRIES=2, CHECK_TRIES=2, BACKOFF=0.8, TOTAL_DEADLINE=12)
# RENDER_FRIENDLY: tolerates cold starts (~18–22s)
RENDER_FRIENDLY = dict(CONNECT=4, READ=8, WARMUP_TRIES=3, CHECK_TRIES=2, BACKOFF=1.1, TOTAL_DEADLINE=20)

# Mode selection (default: FAST). You can switch to BALANCED or RENDER_FRIENDLY.
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
    """
    Build a stable machine identifier that does not change
    across normal updates.

    Priority:
      - Windows: MachineGuid
      - Linux: /etc/machine-id or /var/lib/dbus/machine-id
      - macOS: IOPlatformUUID
    Fallback:
      - Persistent random ID stored in ~/.maya_runtime/machine_id
    """
    parts = []
    os_name = platform.system().lower()

    # -------------------------------
    # Windows-specific identifier
    # -------------------------------
    if os_name == "windows":
        try:
            import winreg  # type: ignore[attr-defined]

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            mg, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if mg:
                parts.append(f"WINGUID:{mg}")
        except Exception:
            pass

    # -------------------------------
    # Linux-specific identifier
    # -------------------------------
    elif os_name == "linux":
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        mid = f.read().strip()
                        if mid:
                            parts.append(f"LXID:{mid}")
                            break
            except Exception:
                pass

    # -------------------------------
    # macOS-specific identifier
    # -------------------------------
    elif os_name == "darwin":
        try:
            import subprocess

            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    # line example: "  \"IOPlatformUUID\" = \"XXXX-XXXX-...\""
                    uuid_str = line.split("=")[-1].strip().strip('"')
                    if uuid_str:
                        parts.append(f"MACUUID:{uuid_str}")
                        break
        except Exception:
            pass

    # -------------------------------
    # Fallback: persistent local ID in ~/.maya_runtime/machine_id
    # -------------------------------
    if not parts:
        try:
            home = os.path.expanduser("~")
            runtime_dir = os.path.join(home, ".maya_runtime")
            os.makedirs(runtime_dir, exist_ok=True)

            path = os.path.join(runtime_dir, "machine_id")
            local_id = None

            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    local_id = f.read().strip()

            if not local_id:
                local_id = uuid.uuid4().hex
                with open(path, "w", encoding="utf-8") as f:
                    f.write(local_id)

            parts.append(f"LOCAL:{local_id}")
        except Exception:
            # Ultimate fallback: in-memory UUID (not stable across runs,
            # but this should be extremely rare)
            parts.append(f"FALLBACK:{uuid.uuid4().hex}")

    raw = "|".join(parts).encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = certifi.where()
    # No automatic retries: we control timing ourselves
    return s


def _warmup(base_url: str, start: float) -> None:
    """Ping /health a few times to 'wake' the server. Doesn't block if the deadline runs out."""
    url = base_url.rstrip("/") + "/health"
    sess = _session()
    for i in range(WARMUP_TRIES):
        if _deadline_remaining(start) <= 0:
            return
        try:
            sess.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            return  # with a 200 or even 404 we know it responded
        except Exception:
            # short backoff, still respecting the deadline
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

    # 1) Very short warm-up (or none, depending on mode)
    _warmup(server_url, start)

    # 2) /check attempts until we hit the deadline
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

    # 3) If the deadline is exceeded, fail fast with a clear message
    if _deadline_remaining(start) <= 0:
        return False, f"Timed out after ~{TOTAL_DEADLINE}s", 5
    return False, last_reason, last_ttl


def require_permission_or_exit(server_url: str, token: str, version: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()

    allowed, reason, _ = check_online(server_url, token, version)

    if allowed:
        # License valid; if it is close to expiry, warn the user.
        if reason == "LICENSE_EXPIRES_SOON":
            messagebox.showwarning(
                "māyā",
                "Your license will expire in less than 7 days.\n"
                "Please renew your license key.",
            )
        root.destroy()
        return

    # Not allowed: show error and exit
    messagebox.showerror("māyā", f"No permission: {reason}")
    root.destroy()
    raise SystemExit(1)
