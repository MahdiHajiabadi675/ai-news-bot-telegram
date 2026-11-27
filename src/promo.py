"""📣 تبلیغ دوره‌ای پلتفرم در کانال (سایت آرشیو + کوییز/جست‌وجو در ربات).

هر PROMO_EVERY_DAYS روز یک پست کوتاه، در ساعت PROMO_HOUR (وقت ایران)، با دکمه‌های شیشه‌ای:
  🌐 ورود به سایت      → SITE_URL
  🧠 کوییز و جست‌وجو   → t.me/<ربات>
چند قالب متن به‌ترتیب می‌چرخد تا تکراری به نظر نرسد. لینک خام در متن نیست (پیش‌نمایش بزرگ نمی‌سازد).

ایمنی:
  • بدون SITE_URL معتبر هیچ پستی نمی‌رود.
  • با /pause و /promo off خاموش می‌شود؛ در dry-run نمی‌فرستد.
  • در ساعت‌های پست آموزشی (EDU_PUBLISH_HOURS) نمی‌آید تا با آن‌ها قاطی نشود.
  • روی شمارندهٔ پست‌های روزانه اثر ندارد و بدون مدل/هزینه است (فقط یک نوشتن KV در هر پست).
  • /promo now در ربات، در اجرای بعدی pipeline فوراً می‌فرستد (state.promo_force).
"""
from datetime import datetime, timedelta

import requests

from . import clock, cloudflare_kv
from .bot import BASE, send_message
from .config import (
    BOT_USERNAME, CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN, EDU_PUBLISH_HOURS,
    PROMO_ENABLED, PROMO_EVERY_DAYS, PROMO_HOUR, SITE_URL,
)
from .formatting import safe_link

KEY = "promo_state"
SLACK_HOURS = 6  # بازهٔ ۳ روزه یعنی «حداقل ۶۶ ساعت»؛ تأخیر Actions باعث جاماندن ساعت نشود

TEMPLATES = [
    "🌐 <b>آرشیو اخبار هوش مصنوعی، یک‌جا و قابل جست‌وجو</b>\n\n"
    "هر خبری که اینجا منتشر شده در سایت ما هست؛ با جست‌وجوی فارسی، دسته‌بندی و تاریخ شمسی.\n"
    "خبر یک هفته پیش را دیگر لازم نیست در کانال بگردی.",

    "🧠 <b>هوش مصنوعی را فقط نخوان؛ امتحان کن!</b>\n\n"
    "در ربات کوییز امتیازی هست: هر پاسخ درست ۱۰ امتیاز، جدول رتبهٔ هفتگی و توضیح بعد از هر سؤال.\n"
    "ببین تو چندمی؟ 🏆",

    "🔎 <b>دنبال یک خبر قدیمی می‌گردی؟</b>\n\n"
    "در ربات بنویس <code>/ask OpenAI</code> یا هر کلمهٔ دیگر؛ پست‌های مرتبط را با لینک مستقیم می‌گیری.\n"
    "یا در سایت آرشیو بگرد.",

    "📚 <b>این کانال حالا یک پلتفرم کامل است</b>\n\n"
    "• 🌐 سایت آرشیو و جست‌وجو\n• 🧠 کوییز امتیازی\n• 🔎 جست‌وجو در آرشیو با <code>/ask</code>\n• 🎓 پست آموزشی هر شب\n\n"
    "همه رایگان. اگر مفید بود برای دوستانت بفرست.",

    "📚 <b>مسیر یادگیری ۳۰روزهٔ هوش مصنوعی</b>\n\n"
    "هر روز یک درس یک‌دقیقه‌ای با تمرین، از مبتدی تا پیشرفته. در ربات بنویس <code>/learn</code> و سطحت را انتخاب کن.\n"
    "پیشرفتت ذخیره می‌شود؛ فقط هر روز سر بزن. 🚀",
]


def _bot_username() -> str:
    """یوزرنیم ربات برای t.me؛ از env یا یک‌بار getMe (شکست → رشتهٔ خالی و دکمهٔ دوم حذف می‌شود)."""
    if BOT_USERNAME:
        return BOT_USERNAME
    try:
        data = requests.get(f"{BASE}/getMe", timeout=10).json()
        name = (data.get("result") or {}).get("username") or ""
        return str(name)
    except Exception as e:  # noqa: BLE001
        print(f"[PROMO] getMe failed: {type(e).__name__}")
        return ""


def build_markup(site_url: str, bot_username: str):
    rows = [[{"text": "🌐 ورود به سایت", "url": site_url}]]
    if bot_username:
        rows.append([{"text": "🧠 کوییز و جست‌وجو در ربات", "url": f"https://t.me/{bot_username}"}])
    return {"inline_keyboard": rows}


def pick_template(idx: int) -> str:
    return TEMPLATES[int(idx or 0) % len(TEMPLATES)]


def allowed_hours():
    """ساعت‌های مجاز انتشار: PROMO_HOUR و یک ساعت بعد (جبران تأخیر)، به‌جز ساعت‌های پست آموزشی."""
    return [h for h in (PROMO_HOUR % 24, (PROMO_HOUR + 1) % 24) if h not in EDU_PUBLISH_HOURS]


def is_due(st: dict, now_utc, force: bool = False) -> bool:
    if force:
        return True
    loc = now_utc + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)
    if loc.hour not in allowed_hours():
        return False
    last = st.get("last_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", ""))
    except ValueError:
        return True
    return now_utc - last_dt >= timedelta(days=PROMO_EVERY_DAYS) - timedelta(hours=SLACK_HOURS)


def maybe_post(state: dict, now=None, force: bool = False) -> dict:
    """اگر نوبت تبلیغ است بفرست. اجرای دستی pipeline (force) تبلیغ نمی‌فرستد؛ فقط `/promo now` (state.promo_force) فوری می‌فرستد."""
    if not PROMO_ENABLED or state.get("promo_enabled") is False or state.get("paused"):
        return state
    site = safe_link(SITE_URL)
    if not site or not CHANNEL_ID:
        return state
    if DRY_RUN:
        print("[DRY] promo skipped (dry-run)")
        return state

    now_utc = now or clock.utcnow()
    forced = bool(state.get("promo_force"))
    st = cloudflare_kv.get(KEY, {})
    st = st if isinstance(st, dict) else {}
    if not is_due(st, now_utc, force=forced):
        return state

    idx = int(st.get("idx", 0) or 0)
    res = send_message(CHANNEL_ID, pick_template(idx), reply_markup=build_markup(site, _bot_username()))
    if not (res and res.get("ok")):
        print(f"[PROMO] send failed: {res}")
        return state

    st.update({"last_at": now_utc.isoformat() + "Z", "idx": (idx + 1) % len(TEMPLATES), "count": int(st.get("count", 0) or 0) + 1})
    cloudflare_kv.put(KEY, st)
    if forced:
        state["promo_force"] = False
    print(f"[PROMO] posted (#{st['count']}, template {idx})")
    return state
