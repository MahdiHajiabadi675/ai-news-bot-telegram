"""
مشترکان خلاصهٔ خصوصی — `/subscribe` و `/unsubscribe`.

فقط کسانی که خودشان خواسته‌اند، هر صبح «صبحانهٔ AI» را در چت خصوصی می‌گیرند.
ارسال با Bot API و کاملاً رایگان است؛ محدودیت نرخ با فاصلهٔ کوتاه رعایت می‌شود.
"""
from . import clock

from . import cloudflare_kv
from .config import DM_DIGEST_MAX
from .formatting import clip

KEY = "subscribers"
MAX_SUBSCRIBERS = 500


def load() -> list:
    data = cloudflare_kv.get(KEY, [])
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("chat_id")]


def save(items):
    cloudflare_kv.put(KEY, (items or [])[-MAX_SUBSCRIBERS:])


def add(chat_id, user=None) -> bool:
    """True یعنی تازه اضافه شد؛ False یعنی از قبل مشترک بود/ظرفیت پر است."""
    rows = load()
    for row in rows:
        if str(row.get("chat_id")) == str(chat_id):
            return False
    if len(rows) >= min(MAX_SUBSCRIBERS, max(1, DM_DIGEST_MAX)):
        return False
    rows.append({
        "chat_id": chat_id,
        "user": clip(user or "", 60),
        "at": clock.utcnow().isoformat() + "Z",
    })
    save(rows)
    return True


def remove(chat_id) -> bool:
    rows = load()
    kept = [r for r in rows if str(r.get("chat_id")) != str(chat_id)]
    if len(kept) == len(rows):
        return False
    save(kept)
    return True


def count() -> int:
    return len(load())
