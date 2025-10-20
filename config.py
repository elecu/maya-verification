# config.py
import os
import re

def load_allowed_tokens():
    """
    Load tokens from a Secret File if provided (TOKENS_FILE=/etc/secrets/tokens.txt),
    otherwise fall back to ALLOWED_TOKENS env var.
    Tokens can be separated by newlines, commas, semicolons or spaces.
    """
    tokens_file = os.getenv("TOKENS_FILE")
    raw = ""
    if tokens_file and os.path.exists(tokens_file):
        with open(tokens_file, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = os.getenv("ALLOWED_TOKENS", "")

    parts = re.split(r"[,\n;\r\t ]+", raw)
    tokens = [p.strip() for p in parts if p.strip()]
    return set(tokens)

ALLOWED_TOKENS = load_allowed_tokens()
