"""
آینهٔ کانال در «بله» — فقط ارسال پیام‌های منتشرشده، بدون هیچ مدیریتی.

قواعد:
  • بعد از هر انتشار موفق در تلگرام، همان خبر «یک بار» به کانال بله هم فرستاده می‌شود.
  • اگر ارسال شکست بخورد، خبر در هیچ صف یا فایلی ذخیره نمی‌شود و دوباره تلاش نمی‌شود (حذف).
  • خطای بله هرگز انتشار تلگرام را مختل نمی‌کند (هیچ استثنایی بیرون نمی‌آید).
  • اگر BALE_BOT_TOKEN یا BALE_CHANNEL_ID تنظیم نباشد، کل قابلیت خاموش است.
  • اگر در یک اجرا ۲ خطای پشت‌سرهم رخ دهد (مثلاً دسترسی بسته است)، بقیهٔ ارسال‌های
    همان اجرا رد می‌شوند تا پایپ‌لاین معطل نماند.

متن بدون parse_mode (متن ساده) فرستاده می‌شود؛ لینک‌ها را بله خودش تشخیص می‌دهد.
نسخهٔ معادل این منطق در worker/index.js هم هست (sendBale / formatBaleMessage).

تست دستی دسترسی:  python -m src.bale   (یک پیام آزمایشی می‌فرستد)
"""
import os

import requests

from .formatting import clip, is_untranslated

BALE_BASE = "https://tapi.bale.ai/bot"
BALE_LIMIT = 4000
TIMEOUT = 8
MAX_CONSECUTIVE_FAILS = 2

_fails = 0


def _creds():
    return (os.getenv("BALE_BOT_TOKEN") or "").strip(), (os.getenv("BALE_CHANNEL_ID") or "").strip()


def enabled() -> bool:
    token, chat = _creds()
    return bool(token and chat)


def reset():
    """شمارندهٔ خطا را برای یک اجرای تازه صفر می‌کند"""
    global _fails
    _fails = 0


def format_bale_message(item: dict) -> str:
    title = (item.get("title_fa") or item.get("title") or "").strip()
    summary = clip(item.get("summary_fa"), 700)
    emoji = "⭐️" if item.get("admin_action") == "featured" else "🔥" if item.get("importance") == "high" else "🤖"
    if item.get("category") == "fast_track":
        emoji = "📡"

    lines = [f"{emoji} {title}", ""]
    if summary:
        lines += [summary, ""]
    if item.get("lang") not in ("fa", None) and not is_untranslated(item) and item.get("title"):
        lines += [f"🌐 {clip(item['title'], 150)}", ""]
    lines.append(f"📰 منبع: {item.get('source', '')}")
    if item.get("link"):
        lines.append(f"🔗 {item['link']}")
    tags = " ".join(f"#{h}" for h in (item.get("hashtags") or []))
    if tags:
        lines.append(tags)
    if is_untranslated(item):
        lines += ["", "🔤 ترجمه نشده — متن اصلی"]
    if item.get("confidence") == "rumor":
        lines += ["", "⚠️ این خبر تایید رسمی نشده است"]
    text = "\n".join(lines)
    return text[:BALE_LIMIT]


def send_text(text: str) -> bool:
    """ارسال متن به کانال بله. فقط True/False برمی‌گرداند و هرگز استثنا نمی‌اندازد."""
    global _fails
    token, chat = _creds()
    if not token or not chat:
        return False
    if _fails >= MAX_CONSECUTIVE_FAILS:
        return False
    try:
        resp = requests.post(
            f"{BALE_BASE}{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=TIMEOUT,
        )
        ok = bool(resp.json().get("ok"))
        if not ok:
            # توکن هرگز چاپ نمی‌شود
            print(f"[BALE] rejected: {resp.text[:200]}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[BALE] error: {type(e).__name__}")
    _fails = 0 if ok else _fails + 1
    return ok


def mirror(item: dict) -> bool:
    """خبر منتشرشده را به بله می‌فرستد؛ شکست = حذف بی‌سروصدا (بدون صف و بدون تلاش مجدد)"""
    if not enabled():
        return False
    try:
        return send_text(format_bale_message(item))
    except Exception as e:  # noqa: BLE001
        print(f"[BALE] error: {type(e).__name__}")
        return False


if __name__ == "__main__":
    if not enabled():
        raise SystemExit("BALE_BOT_TOKEN / BALE_CHANNEL_ID تنظیم نشده")
    ok = send_text("✅ تست اتصال ربات اخبار به کانال بله")
    print("OK ✅" if ok else "FAILED ❌ (جزئیات بالا)")
    raise SystemExit(0 if ok else 1)
