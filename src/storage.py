"""
Storage — از Cloudflare KV استفاده می‌کنه
"""
from . import cloudflare_kv

# سقف نگهداری داده در KV (جلوگیری از رشد بی‌نهایت)
MAX_PENDING_ITEMS = 300
MAX_PUBLISHED_LOG = 1500

def load_state():
    state = cloudflare_kv.get("state", {
        "last_update_id": 0,
        "last_collect_time": None,
        "daily_post_count": 0,
        "daily_post_date": None,
        "paused": False,
        "auto_publish_enabled": True,
        "current_stage": "idle",
        "stage_updated_at": None,
        "last_error": None,
        "gemini_fail_count": 0
    })
    if not isinstance(state, dict):
        state = {}
    # مقدار قدیمی «auto_publish» نادیده گرفته می‌شود (ممکن است از تست‌های
    # قدیمی false مانده باشد). پیش‌فرض همیشه روشن است.
    state.setdefault("auto_publish_enabled", True)
    # اصلاح یک‌بارهٔ مقدار falseِ قدیمی — فقط اگر ادمین خودش خاموش نکرده باشد.
    if state.get("auto_publish_enabled") is False and not state.get("auto_publish_set_by_admin"):
        state["auto_publish_enabled"] = True
    return state

def save_state(state):
    cloudflare_kv.put("state", state)

def set_stage(state, stage, error=None):
    state["current_stage"] = stage
    from datetime import datetime
    state["stage_updated_at"] = datetime.utcnow().isoformat() + "Z"
    if error:
        state["last_error"] = str(error)
    return state

def load_seen():
    return set(cloudflare_kv.get("seen", []))

def save_seen(seen):
    cloudflare_kv.put("seen", list(seen))

def load_pending():
    return cloudflare_kv.get("pending", [])

def save_pending(items):
    if len(items) > MAX_PENDING_ITEMS:
        # قدیمی‌ترین‌ها حذف شوند (خبر کهنه ارزش انتشار ندارد)
        items = items[-MAX_PENDING_ITEMS:]
    cloudflare_kv.put("pending", items)

def load_approved():
    return cloudflare_kv.get("approved", [])

def save_approved(items):
    cloudflare_kv.put("approved", items)

def load_published():
    return cloudflare_kv.get("published", [])

def save_published(items):
    if len(items) > MAX_PUBLISHED_LOG:
        items = items[-MAX_PUBLISHED_LOG:]
    cloudflare_kv.put("published", items)

def is_duplicate(news_id: str) -> bool:
    """آیا خبر تکراریه؟"""
    return news_id in load_seen()

def mark_as_seen(news_id: str):
    """علامت زدن خبر به عنوان دیده شده"""
    seen = load_seen()
    seen.add(news_id)
    save_seen(seen)
