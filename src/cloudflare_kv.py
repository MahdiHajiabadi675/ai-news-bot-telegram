"""
Cloudflare KV Client — برای GitHub Actions
ذخیره/خواندن داده‌ها در Cloudflare KV
"""
import os
import json
import requests

CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE = os.getenv("CF_KV_NAMESPACE", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")

BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE}"

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

def get(key: str, default=None):
    """خواندن از KV"""
    try:
        resp = requests.get(f"{BASE_URL}/values/{key}", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return json.loads(resp.text)
        return default
    except Exception as e:
        print(f"[WARN] KV get {key}: {e}")
        return default

def put(key: str, value):
    """نوشتن در KV"""
    try:
        resp = requests.put(
            f"{BASE_URL}/values/{key}",
            headers=HEADERS,
            data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[WARN] KV put {key}: {e}")
        return False
