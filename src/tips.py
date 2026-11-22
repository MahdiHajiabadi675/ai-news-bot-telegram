"""
خبرنگار مردمی — `/tip` در چت خصوصی ربات.

مخاطب می‌تواند سوژه/لینک بفرستد؛ ادمین با یک دکمه تایید یا رد می‌کند.
بدون سرویس پولی، با محدودیت نرخ ساده در KV.
"""
from datetime import datetime, timedelta

from . import cloudflare_kv
from .config import DRY_RUN, TIPS_ENABLED, TIPS_MAX_PER_HOUR
from .formatting import clip, escape_html, safe_link

KEY = "tips"
RATE_KEY = "tip_rate"
MAX_TIPS = 200
MAX_LEN = 600
MIN_LEN = 12


def load() -> list:
    data = cloudflare_kv.get(KEY, [])
    return data if isinstance(data, list) else []


def save(items):
    cloudflare_kv.put(KEY, (items or [])[-MAX_TIPS:])


def _rates() -> dict:
    data = cloudflare_kv.get(RATE_KEY, {})
    return data if isinstance(data, dict) else {}


def _rate_count(chat_id, now):
    """(تعداد سوژه در ساعت اخیر, زمان آخرین سوژه) — سازگار با مقدار قدیمی رشته‌ای."""
    raw = _rates().get(str(chat_id))
    if not raw:
        return 0, None
    if isinstance(raw, dict):
        at_raw, count = raw.get("at"), int(raw.get("count") or 0)
    else:
        at_raw, count = raw, 1
    try:
        when = datetime.fromisoformat(str(at_raw).replace("Z", ""))
    except (TypeError, ValueError):
        return 0, None
    return (count if now - when < timedelta(hours=1) else 0), when


def _too_soon(chat_id, now) -> bool:
    count, _when = _rate_count(chat_id, now)
    return count >= max(1, TIPS_MAX_PER_HOUR)


def add(chat_id, user, text, now=None):
    """(ok, message, tip) — پیام فارسی برای نمایش به کاربر."""
    if not TIPS_ENABLED:
        return False, "این قابلیت فعلاً خاموش است.", None
    body = " ".join(str(text or "").split()).strip()
    if len(body) < MIN_LEN:
        return False, f"متن کوتاه است (حداقل {MIN_LEN} کاراکتر).", None
    if len(body) > MAX_LEN:
        return False, f"متن بلند است (حداکثر {MAX_LEN} کاراکتر).", None
    now = now or datetime.utcnow()
    if _too_soon(chat_id, now):
        return False, f"سهمیهٔ این ساعت پر شده (حداکثر {TIPS_MAX_PER_HOUR} سوژه در ساعت).", None
    if DRY_RUN:
        print(f"[DRY] tip stored: {body[:80]}")
        return True, "ثبت شد (dry-run).", None

    tips = load()
    tip = {
        "id": f"tip-{int(now.timestamp() * 1000)}-{chat_id}",
        "chat_id": chat_id,
        "user": clip(user or "", 60),
        "text": body,
        "at": now.isoformat() + "Z",
        "status": "new",
    }
    tips.append(tip)
    save(tips)

    rates = _rates()
    used, _last = _rate_count(chat_id, now)
    rates[str(chat_id)] = {"at": now.isoformat() + "Z", "count": used + 1}
    cutoff = now - timedelta(hours=24)
    clean = {}
    for key, value in rates.items():
        raw_at = value.get("at") if isinstance(value, dict) else value
        try:
            when = datetime.fromisoformat(str(raw_at).replace("Z", ""))
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            clean[key] = value
    cloudflare_kv.put(RATE_KEY, clean)
    return True, "✅ سوژه ثبت شد. اگر تایید شود، با نام «از مخاطبان» منتشر می‌شود.", tip


def find(tip_id):
    for tip in load():
        if isinstance(tip, dict) and tip.get("id") == tip_id:
            return tip
    return None


def mark(tip_id, status) -> bool:
    tips = load()
    changed = False
    for tip in tips:
        if isinstance(tip, dict) and tip.get("id") == tip_id:
            tip["status"] = status
            tip["reviewed_at"] = datetime.utcnow().isoformat() + "Z"
            changed = True
            break
    if changed:
        save(tips)
    return changed


def pending(limit=5) -> list:
    rows = [t for t in load() if isinstance(t, dict) and t.get("status") == "new"]
    return rows[-max(1, limit):]


def format_post(tip) -> str:
    """پست کانال برای سوژهٔ تاییدشده — متن کاربر escape می‌شود."""
    who = escape_html(clip((tip or {}).get("user") or "مخاطب ناشناس", 40))
    body = escape_html(clip((tip or {}).get("text") or "", 600))
    return (
        "📣 <b>از مخاطبان کانال</b>\n"
        "─" * 20 + "\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"— {who}\n"
        "🏷️ #از_مخاطبان"
    )


def format_admin(tip) -> str:
    tip = tip or {}
    who = escape_html(clip(tip.get("user") or str(tip.get("chat_id") or "?"), 50))
    body = escape_html(clip(tip.get("text") or "", 600))
    return f"📣 <b>سوژهٔ تازه</b>\nاز: {who}\n\n{body}"


def has_link(text) -> bool:
    return bool(safe_link(str(text or "").split()[0] if str(text or "").split() else ""))
