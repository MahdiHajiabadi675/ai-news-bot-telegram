"""
آینهٔ Bluesky — کراس‌پست رایگان خبرهای منتشرشده.

مثل آینهٔ بله: هر خبری که در تلگرام منتشر می‌شود، یک‌بار در Bluesky هم می‌رود.
اگر Bluesky خطا بدهد، هیچ چیزی ذخیره یا تکرار نمی‌شود و انتشار تلگرام هم دست
نمی‌خورد (همهٔ خطاها بلعیده می‌شوند).

نیازمندی: `BLUESKY_HANDLE` (مثل name.bsky.social) و `BLUESKY_APP_PASSWORD`
(App Password از Settings → Privacy and Security). با خالی بودن این‌ها خاموش است.
متن بلندتر از سقف Bluesky (۳۰۰ کاراکتر) کوتاه می‌شود.
"""
import os
from datetime import datetime, timezone

import requests

API = "https://bsky.social/xrpc"
TIMEOUT = 8
MAX_TEXT = 280


def _creds():
    return (os.getenv("BLUESKY_HANDLE") or "").strip(), (os.getenv("BLUESKY_APP_PASSWORD") or "").strip()


def enabled() -> bool:
    handle, password = _creds()
    return bool(handle and password)


def format_post(item: dict) -> str:
    title = (item.get("title_fa") or item.get("title") or "").strip()
    summary = (item.get("summary_fa") or "").strip()
    link = str(item.get("link") or "").strip()
    parts = [p for p in (title, summary, link) if p]
    return "\n\n".join(parts)[:MAX_TEXT]


def _session():
    handle, password = _creds()
    if not handle or not password:
        return None
    try:
        resp = requests.post(
            f"{API}/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[BSKY] login failed: HTTP {resp.status_code}")
            return None
        data = resp.json() or {}
        if not data.get("accessJwt") or not data.get("did"):
            return None
        return {"jwt": data["accessJwt"], "did": data["did"]}
    except Exception as e:  # noqa: BLE001
        print(f"[BSKY] login error: {type(e).__name__}")
        return None


def post(text: str) -> bool:
    """ارسال یک متن کوتاه؛ فقط True/False و هرگز استثنا."""
    if not enabled():
        return False
    text = (text or "").strip()
    if not text:
        return False
    session = _session()
    if not session:
        return False
    try:
        resp = requests.post(
            f"{API}/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {session['jwt']}"},
            json={
                "repo": session["did"],
                "collection": "app.bsky.feed.post",
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": text[:MAX_TEXT],
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
            },
            timeout=TIMEOUT,
        )
        ok = resp.status_code == 200
        if not ok:
            print(f"[BSKY] post failed: HTTP {resp.status_code}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"[BSKY] post error: {type(e).__name__}")
        return False


def mirror(item: dict) -> bool:
    """خبر منتشرشده را به Bluesky می‌فرستد؛ شکست = نادیده گرفتن."""
    if not enabled():
        return False
    try:
        return post(format_post(item))
    except Exception as e:  # noqa: BLE001
        print(f"[BSKY] mirror error: {type(e).__name__}")
        return False
