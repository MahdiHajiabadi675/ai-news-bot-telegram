"""
Storage — از Cloudflare KV استفاده می‌کنه

نکتهٔ مهم: هر نوشتن با `_put` انجام می‌شود که در صورت شکست `KVError` می‌دهد.
اگر شکست را نادیده بگیریم، خبر در تلگرام منتشر می‌شود ولی «منتشر شد» ثبت نمی‌شود
و اجرای بعدی دوباره همان خبر را می‌فرستد (پست تکراری).
"""
from . import cloudflare_kv
from .config import DRY_RUN

# سقف نگهداری داده در KV (جلوگیری از رشد بی‌نهایت)
MAX_PENDING_ITEMS = 300
MAX_PUBLISHED_LOG = 1500


class KVError(RuntimeError):
    """نوشتن در Cloudflare KV شکست خورد؛ اجرا باید متوقف شود."""


def _put(key, value):
    """نوشتن در KV؛ شکست به‌جای نادیده گرفتن، استثنا می‌دهد."""
    if DRY_RUN:
        print(f"[DRY] KV put «{key}» skipped (dry-run)")
        return
    if cloudflare_kv.put(key, value) is False:
        raise KVError(f"Cloudflare KV write failed: {key}")


def ensure_writable():
    """آزمون کوچک نوشتن قبل از انتشار — تا «منتشر شد ولی ثبت نشد» پیش نیاید."""
    from datetime import datetime
    _put("health_ping", {"at": datetime.utcnow().isoformat() + "Z"})


def heartbeat(extra=None):
    """ثبت زمان آخرین اجرا برای /health — شکست این نوشتن اجرا را متوقف نمی‌کند."""
    from datetime import datetime
    data = {"at": datetime.utcnow().isoformat() + "Z"}
    if isinstance(extra, dict):
        data.update(extra)
    if DRY_RUN:
        print("[DRY] heartbeat skipped (dry-run)")
        return
    cloudflare_kv.put("heartbeat", data)


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
        "last_error": None
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
    _put("state", state)

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
    _put("seen", list(seen))

def load_pending():
    return cloudflare_kv.get("pending", [])

def save_pending(items):
    if len(items) > MAX_PENDING_ITEMS:
        # قدیمی‌ترین‌ها حذف شوند (خبر کهنه ارزش انتشار ندارد)
        items = items[-MAX_PENDING_ITEMS:]
    _put("pending", items)

def load_approved():
    return cloudflare_kv.get("approved", [])

def save_approved(items):
    _put("approved", items)

def load_published():
    return cloudflare_kv.get("published", [])

def save_published(items):
    if len(items) > MAX_PUBLISHED_LOG:
        items = items[-MAX_PUBLISHED_LOG:]
    _put("published", items)

def load_source_stats():
    from . import source_trust
    return source_trust.load_stats()

def save_source_stats(stats):
    from . import source_trust
    source_trust.save_stats(stats)

RUN_LOG_KEEP = 48  # آخرین ۴۸ اجرا (حدود دو روز)


def append_run_log(entry: dict):
    """آمار «قیف» هر اجرا (چند خبر پیدا شد، کجا کم شد) برای دستور /runs"""
    log = cloudflare_kv.get("run_log", [])
    log = log if isinstance(log, list) else []
    log.append(entry)
    _put("run_log", log[-RUN_LOG_KEEP:])

def load_run_log():
    log = cloudflare_kv.get("run_log", [])
    return log if isinstance(log, list) else []

def load_feed_health():
    h = cloudflare_kv.get("feed_health", {})
    return h if isinstance(h, dict) else {}

FEED_FAIL_ALERT_AT = 3  # بعد از این‌همه خطای پیاپی به ادمین هشدار بده


def update_feed_health(current: dict) -> list:
    """
    وضعیت فیدها را با سابقهٔ قبلی ادغام و در KV ذخیره می‌کند.
    برمی‌گرداند: نام فیدهایی که همین الان به آستانهٔ هشدار رسیده‌اند.
    """
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"
    old = cloudflare_kv.get("feed_health", {})
    old = old if isinstance(old, dict) else {}
    merged, newly_failing = {}, []
    for name, h in current.items():
        prev = old.get(name) or {}
        fails = 0 if h["ok"] else int(prev.get("fails", 0)) + 1
        if fails == FEED_FAIL_ALERT_AT:
            newly_failing.append(name)
        merged[name] = {
            **h,
            "fails": fails,
            "checked": now,
            "last_ok": now if h["ok"] else prev.get("last_ok"),
        }
    _put("feed_health", merged)
    return newly_failing

def is_duplicate(news_id: str) -> bool:
    """آیا خبر تکراریه؟"""
    return news_id in load_seen()

def mark_as_seen(news_id: str):
    """علامت زدن خبر به عنوان دیده شده"""
    seen = load_seen()
    seen.add(news_id)
    save_seen(seen)
