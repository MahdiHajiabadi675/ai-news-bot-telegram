"""
Gemini Budget — محدودیت سخت روی تعداد درخواست‌ها به Gemini
سیاست: حداکثر یک درخواست در هر بازه (پیش‌فرض ۵۵ دقیقه) و سقف روزانه.

این ماژول در Cloudflare KV ذخیره می‌شود تا بین اجراهای مختلف هم رعایت شود.
"""
from datetime import datetime
from . import cloudflare_kv
from .config import GEMINI_MIN_INTERVAL_MIN, GEMINI_MAX_CALLS_PER_DAY

KEY = "gemini_budget"

# گارد درون‌فرآیندی (برای جلوگیری از خواندن کهنه KV در همان اجرا)
_called_this_process = False


def _load() -> dict:
    data = cloudflare_kv.get(KEY, {})
    if not isinstance(data, dict):
        data = {}
    today = datetime.utcnow().date().isoformat()
    if data.get("date") != today:
        # روز جدید → شمارنده صفر
        data = {"date": today, "count": 0, "last_call_at": data.get("last_call_at")}
    return data


def status() -> dict:
    """وضعیت فعلی بودجه (برای نمایش در /status)"""
    data = _load()
    return {
        "date": data.get("date"),
        "count": data.get("count", 0),
        "max_per_day": GEMINI_MAX_CALLS_PER_DAY,
        "last_call_at": data.get("last_call_at"),
        "min_interval_min": GEMINI_MIN_INTERVAL_MIN,
    }


def can_call() -> bool:
    """آیا اجازه داریم الان به Gemini درخواست بزنیم؟"""
    if _called_this_process:
        return False

    data = _load()

    if data.get("count", 0) >= GEMINI_MAX_CALLS_PER_DAY:
        print(f"[BUDGET] Daily Gemini limit reached ({GEMINI_MAX_CALLS_PER_DAY})")
        return False

    last = data.get("last_call_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", ""))
            elapsed_min = (datetime.utcnow() - last_dt).total_seconds() / 60
            if elapsed_min < GEMINI_MIN_INTERVAL_MIN:
                print(f"[BUDGET] Only {elapsed_min:.0f}m since last Gemini call "
                      f"(min {GEMINI_MIN_INTERVAL_MIN}m) — skipping")
                return False
        except Exception:
            pass

    return True


def mark_called():
    """ثبت یک درخواست موفق به Gemini"""
    global _called_this_process
    _called_this_process = True
    data = _load()
    data["count"] = data.get("count", 0) + 1
    data["last_call_at"] = datetime.utcnow().isoformat() + "Z"
    cloudflare_kv.put(KEY, data)
