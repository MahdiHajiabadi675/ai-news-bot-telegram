"""
Cloudflare KV Client — برای GitHub Actions
ذخیره/خواندن داده‌ها در Cloudflare KV

هر نوشتن شکست‌خورده با retry کوتاه دوباره تلاش می‌شود و در نهایت False برمی‌گرداند
تا لایهٔ بالاتر (src/storage.py) بتواند اجرا را متوقف کند. قبلاً شکست بی‌صدا
نادیده گرفته می‌شد و نتیجه‌اش «پست منتشر شد ولی ثبت نشد» → پست تکراری بود.
"""
import json
import os
import time
from datetime import date, datetime

import requests

CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE = os.getenv("CF_KV_NAMESPACE", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")

BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE}"

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

# شمارندهٔ درخواست‌های همین اجرا (برای دیدن مصرف سقف روزانهٔ KV در /runs و /dashboard)
STATS = {"get": 0, "put": 0}

TIMEOUT_S = 10
RETRIES = 3
BACKOFF_S = (0.5, 1.0, 2.0)


def _retryable(status: int) -> bool:
    """خطاهای گذرا: محدودیت نرخ و خطای سمت سرور."""
    return status == 429 or 500 <= status < 600


def get(key: str, default=None):
    """خواندن از KV — کلید ناموجود (404) خطا نیست؛ خطای گذرا retry می‌شود."""
    STATS["get"] += 1
    last_err = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(f"{BASE_URL}/values/{key}", headers=HEADERS, timeout=TIMEOUT_S)
        except Exception as e:
            last_err = e
            print(f"[WARN] KV get {key} (try {attempt + 1}/{RETRIES}): {e}")
        else:
            if resp.status_code == 200:
                try:
                    return json.loads(resp.text)
                except ValueError:
                    print(f"[ERROR] KV get {key}: invalid JSON ({len(resp.text)} bytes)")
                    return default
            if resp.status_code == 404:
                return default
            last_err = f"HTTP {resp.status_code}"
            print(f"[WARN] KV get {key}: HTTP {resp.status_code} {str(resp.text)[:200]}")
            if not _retryable(resp.status_code):
                return default
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_S[attempt])
    print(f"[ERROR] KV get {key} gave up after {RETRIES} tries: {last_err}")
    return default


def _json_fallback(obj):
    """شبکهٔ ایمنی: تاریخ/مجموعه به‌جای شکستن کل pipeline به شکل قابل‌ذخیره درمی‌آید (و هشدار چاپ می‌شود)."""
    if isinstance(obj, (datetime, date, set, frozenset)):
        print(f"[WARN] KV: مقدار {type(obj).__name__} به شکل JSON-امن تبدیل شد (منبع را اصلاح کن)")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat() + ("Z" if isinstance(obj, datetime) and obj.tzinfo is None else "")
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def put(key: str, value):
    """نوشتن در KV — True یعنی ذخیره شد، False یعنی شکست (لایهٔ بالاتر باید تصمیم بگیرد)."""
    try:
        body = json.dumps(value, ensure_ascii=False, default=_json_fallback).encode("utf-8")
    except (TypeError, ValueError) as e:
        print(f"[ERROR] KV put {key}: not JSON-serializable: {e}")
        return False

    STATS["put"] += 1
    last_err = None
    for attempt in range(RETRIES):
        try:
            resp = requests.put(
                f"{BASE_URL}/values/{key}",
                headers=HEADERS,
                data=body,
                timeout=TIMEOUT_S,
            )
        except Exception as e:
            last_err = e
            print(f"[WARN] KV put {key} (try {attempt + 1}/{RETRIES}): {e}")
        else:
            if resp.status_code in (200, 201):
                return True
            last_err = f"HTTP {resp.status_code}"
            print(f"[WARN] KV put {key}: HTTP {resp.status_code} {str(resp.text)[:200]}")
            if not _retryable(resp.status_code):
                return False
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_S[attempt])
    print(f"[ERROR] KV put {key} failed after {RETRIES} tries: {last_err}")
    return False
