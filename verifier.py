import requests

def check_access():
    try:
        response = requests.get("https://maya-verification.onrender.com/check")
        return response.text.strip() == "ALLOW"
    except Exception:
        return False
