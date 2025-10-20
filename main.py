# main.py
import os
import sys
import shutil
import site
import pathlib
from importlib.machinery import SourceFileLoader

SERVER_URL    = os.getenv("MAYA_SERVER_URL", "https://maya-verification.onrender.com")
APP_VERSION   = "1.0.0"
LICENSE_TOKEN = os.getenv("MAYA_LICENSE_TOKEN", "WORKSHOP_2025")
MAYA_FILE     = "MAYA_12-10-25.py"  # keep your original filename

# ---------- Runtime bootstrap so MAYA can use `-m pip` inside a PyInstaller binary ----------
# Create a per-user site dir where pip can install without admin rights
RUNTIME_DIR = os.path.join(pathlib.Path.home(), ".maya_runtime", "site")
os.makedirs(RUNTIME_DIR, exist_ok=True)

# Make sure Python searches this directory for imports (now and after restart)
site.addsitedir(RUNTIME_DIR)
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

# Tell pip to install into RUNTIME_DIR when MAYA runs `-m pip install ...`
os.environ["PIP_TARGET"] = RUNTIME_DIR
os.environ.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

# Point sys.executable to a real Python interpreter, so `-m pip` works from MAYA
_real_py = shutil.which("python3") or shutil.which("python")
if _real_py:
    sys.executable = _real_py
# If no system Python is available, MAYA's own pip calls will fail on that machine.

# (Optional) Allow fast/slow verification modes without rebuild (FAST|BALANCED|RENDER_FRIENDLY)
os.environ.setdefault("MAYA_VERIFY_MODE", "FAST")
# ------------------------------------------------------------------------------------------------

def resource_path(rel_path: str) -> str:
    # Works both in PyInstaller onefile and normal Python
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base, rel_path)

def run_maya():
    path = resource_path(MAYA_FILE)
    maya_module = SourceFileLoader("maya_app", path).load_module()
    if hasattr(maya_module, "main") and callable(maya_module.main):
        maya_module.main()
    else:
        # If there is no main(), just executing top-level code may be enough
        pass

# ----------------------- OPTIONAL: token pickup helper (non-breaking) -----------------------
# This block does NOT alter existing behaviour if MAYA_LICENSE_TOKEN is already set.
# It only helps when you want to feed multiple tokens via:
#   a) Secret File: TOKENS_FILE=/etc/secrets/tokens.txt   (one token per line)
#   b) Env var:     ALLOWED_TOKENS="TOKEN1,TOKEN2\nTOKEN3"
# We simply pick the FIRST token found and use it as LICENSE_TOKEN.
def _maybe_override_license_token_from_allowed_list():
    """If MAYA_LICENSE_TOKEN is not explicitly provided, read the first token
    from TOKENS_FILE or ALLOWED_TOKENS (newline/comma/semicolon/whitespace separated)."""
    global LICENSE_TOKEN
    # Respect explicit per-user token if provided.
    if os.getenv("MAYA_LICENSE_TOKEN"):
        return

    tokens_file = os.getenv("TOKENS_FILE")  # e.g., /etc/secrets/tokens.txt
    raw = ""
    try:
        if tokens_file and os.path.exists(tokens_file):
            with open(tokens_file, "r", encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = os.getenv("ALLOWED_TOKENS", "")
    except Exception:
        # Silent fallback; keep default LICENSE_TOKEN.
        raw = ""

    # Split on common separators: newline, comma, semicolon, tabs, spaces
    try:
        import re
        parts = [p.strip() for p in re.split(r"[,\n;\r\t ]+", raw) if p.strip()]
        if parts:
            LICENSE_TOKEN = parts[0]  # first valid token
    except Exception:
        # If anything goes wrong, keep the original LICENSE_TOKEN as-is.
        pass

# Call the helper once at import-time (safe and idempotent).
_maybe_override_license_token_from_allowed_list()
# -------------------------------------------------------------------------------------------

def main():
    from verifier import require_permission_or_exit
    require_permission_or_exit(SERVER_URL, LICENSE_TOKEN, APP_VERSION)
    run_maya()

if __name__ == "__main__":
    main()
