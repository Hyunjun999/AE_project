"""Naver Commerce API: access token and one helper to call any endpoint."""
import base64
import json
import os
import time
from pathlib import Path

import bcrypt
import requests
from dotenv import load_dotenv

BASE = "https://api.commerce.naver.com/external"
HERE = Path(__file__).parent
TOKEN_FILE = HERE / ".token.json"
load_dotenv(HERE / ".env")


def get_token():
    """Issue an access token. Reuse the cached one if more than 10 minutes remain."""
    if TOKEN_FILE.exists():
        cached = json.loads(TOKEN_FILE.read_text())
        if cached["expires_at"] - time.time() > 600:
            return cached["access_token"]

    client_id = os.environ["NAVER_COMMERCE_CLIENT_ID"]
    client_secret = os.environ["NAVER_COMMERCE_CLIENT_SECRET"]
    timestamp = str(int(time.time() * 1000))
    hashed = bcrypt.hashpw(f"{client_id}_{timestamp}".encode(), client_secret.encode())
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        data={
            "client_id": client_id,
            "timestamp": timestamp,
            "client_secret_sign": base64.b64encode(hashed).decode(),
            "grant_type": "client_credentials",
            "type": "SELF",
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    TOKEN_FILE.write_text(json.dumps({
        "access_token": body["access_token"],
        "expires_at": time.time() + body["expires_in"],
    }))
    return body["access_token"]


def api(method, path, **kwargs):
    """Call the Commerce API. Example: api("POST", "/v1/products/search", json={...})"""
    headers = {"Authorization": f"Bearer {get_token()}"}
    return requests.request(method, BASE + path, headers=headers, timeout=30, **kwargs)
