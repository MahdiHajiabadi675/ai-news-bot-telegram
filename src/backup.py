"""
پشتیبان‌گیری KV — بدون سرویس پولی.

چرا؟ اگر namespace کلاودفلر پاک/بازنشانی شود، `seen` و `published` هم می‌روند
و کانال پر از پست تکراری می‌شود. یک فایل JSON هفتگی در چت ادمین، ارزان‌ترین
بیمهٔ ممکن است.

  • هر هفته (پیش‌فرض یکشنبه ۳ بامداد ایران) یک فایل JSON به ادمین می‌رود.
  • دستور /backup همین کار را فوری انجام می‌دهد.
  • بازیابی دستی: python -m src.backup --restore backup.json
"""
from . import clock
import json
import sys
from datetime import timedelta

from . import bot, cloudflare_kv
from .config import (
    ADMIN_ID, BACKUP_DAY, BACKUP_ENABLED, BACKUP_HOUR, DISPLAY_TZ_OFFSET_MIN,
    DRY_RUN,
)

KEY = "backup_state"
KEYS = (
    "state", "seen", "pending", "approved", "published", "source_stats",
    "feed_health", "run_log", "brief_state", "views_state", "weekly_state",
    "stories", "reactions", "edu_state", "digest_state", "arxiv_state",
    "subscribers", "tips", "tip_rate",
)


def _local_now(now=None):
    return (now or clock.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def snapshot() -> dict:
    """همهٔ کلیدهای مهم KV را در یک دیکشنری جمع می‌کند."""
    data = {}
    for key in KEYS:
        value = cloudflare_kv.get(key)
        if value is not None:
            data[key] = value
    return {
        "format": "ai-news-bot-kv",
        "version": 1,
        "generated_at": clock.utcnow().isoformat() + "Z",
        "keys": data,
    }


def payload_bytes() -> bytes:
    return json.dumps(snapshot(), ensure_ascii=False, indent=1).encode("utf-8")


def send_now(chat_id=None) -> bool:
    """پشتیبان را همین حالا به ادمین بفرست."""
    chat_id = chat_id or ADMIN_ID
    if not chat_id:
        print("[BACKUP] no admin id")
        return False
    if DRY_RUN:
        print("[DRY] backup skipped (dry-run)")
        return False
    try:
        body = payload_bytes()
    except Exception as e:  # noqa: BLE001
        print(f"[BACKUP] snapshot failed: {type(e).__name__}: {e}")
        return False
    name = "ai-news-bot-backup-" + clock.utcnow().strftime("%Y%m%d-%H%M") + ".json"
    res = bot.send_document(
        chat_id, name, body,
        caption=f"💾 پشتیبان KV — {len(body) // 1024}KB\nبازیابی: python -m src.backup --restore <file>",
    )
    ok = bool(res and res.get("ok"))
    print(f"[BACKUP] {'sent' if ok else 'failed'} ({len(body)} bytes)")
    return ok


def maybe_send(state, now=None) -> bool:
    """یک‌بار در هفته، در روز/ساعت مقرر."""
    try:
        if not BACKUP_ENABLED or not ADMIN_ID or DRY_RUN:
            return False
        if (state or {}).get("paused"):
            return False
        loc = _local_now(now)
        if loc.weekday() != BACKUP_DAY:
            return False
        if loc.hour not in (BACKUP_HOUR, (BACKUP_HOUR + 1) % 24, (BACKUP_HOUR + 2) % 24):
            return False
        st = cloudflare_kv.get(KEY, {})
        st = st if isinstance(st, dict) else {}
        today = loc.date().isoformat()
        if st.get("last_date") == today:
            return False
        if not send_now():
            return False
        st.update({"last_date": today, "at": clock.utcnow().isoformat() + "Z"})
        cloudflare_kv.put(KEY, st)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[BACKUP] skipped: {type(e).__name__}: {e}")
        return False


def restore(path: str) -> int:
    """بازیابی دستی از فایل JSON. برمی‌گرداند: تعداد کلیدهای نوشته‌شده."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict):
        raise ValueError("فایل پشتیبان معتبر نیست (کلید keys پیدا نشد)")
    written = 0
    for key, value in keys.items():
        if key not in KEYS:
            print(f"[RESTORE] skip unknown key: {key}")
            continue
        if cloudflare_kv.put(key, value):
            written += 1
            print(f"[RESTORE] ok: {key}")
        else:
            print(f"[RESTORE] FAILED: {key}")
    print(f"[RESTORE] {written}/{len(keys)} keys restored")
    return written


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--restore":
        restore(sys.argv[2])
    else:
        print("Usage: python -m src.backup --restore backup.json")
