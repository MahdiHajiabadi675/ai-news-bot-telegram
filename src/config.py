import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    """خواندن عدد از env — مقدار خالی/نامعتبر → default (بدون کرش)"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
ADMIN_ID = _int_env("TELEGRAM_ADMIN_ID", 0)

# بنر منوی /start — داخل مخزن GitHub میزبانی می‌شود (قابل تغییر با START_BANNER_URL)
START_BANNER_URL = os.getenv(
    "START_BANNER_URL",
    "https://raw.githubusercontent.com/MahdiHajiabadi675/ai-news-bot-telegram/main/assets/banner.jpg?v=1",
)

# Gemini — فعلاً کنار گذاشته شده (در پایپ‌لاین استفاده نمی‌شود).
# ترجمه کاملاً با src/translator.py انجام می‌شود. این تنظیمات فقط برای
# src/gemini_parked.py و src/gemini_budget.py نگه داشته شده‌اند تا ایدهٔ بعدی
# رویشان پیاده شود.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MIN_INTERVAL_MIN = _int_env("GEMINI_MIN_INTERVAL_MIN", 55)
GEMINI_MAX_CALLS_PER_DAY = _int_env("GEMINI_MAX_CALLS_PER_DAY", 24)
GEMINI_BATCH_SIZE = _int_env("GEMINI_BATCH_SIZE", 10)

# ───────── پست آموزشی هوش مصنوعی (src/edu_post.py) — فقط متن ─────────
# Gemini اول امتحان می‌شود؛ اگر خطا داد (مثلاً 503) مدل‌های رایگان OpenRouter.
EDU_ENABLED = os.getenv("EDU_POST_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# ساخت پست‌ها هر EDU_EVERY_HOURS ساعت؛ «انتشار» در ساعت‌های پربازدید (EDU_PUBLISH_HOURS) انجام می‌شود
EDU_EVERY_HOURS = _int_env("EDU_EVERY_HOURS", 6)  # هر چند ساعت یک پست «ساخته» شود (۶ → ۴ پست در روز)
# تعداد تلاش در هر اسلات و حداقل فاصلهٔ بین دو تلاش (دقیقه). فاصله باعث می‌شود تلاش‌ها در
# چند ساعت پخش شوند و اجرای دستی پشت‌سرهم همهٔ تلاش‌های اسلات را نسوزاند.
def _hours_env(name: str, default: str) -> tuple:
    raw = (os.getenv(name) or default).strip()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.append(int(part))
    return tuple(out) or tuple(int(x) for x in default.split(","))


# ساعت‌های انتشار (به وقت نمایش/ایران، ۰ تا ۲۳)؛ در هر ساعت حداکثر یک پست آموزشی
EDU_PUBLISH_HOURS = _hours_env("EDU_PUBLISH_HOURS", "19,20,21,22")
# حداکثر پست ساخته‌شده و منتظر انتشار (اگر پر باشد ساخت متوقف می‌شود)
EDU_QUEUE_MAX = _int_env("EDU_QUEUE_MAX", 8)
EDU_MAX_TRIES_PER_SLOT = _int_env("EDU_MAX_TRIES_PER_SLOT", 6)
EDU_RETRY_GAP_MIN = _int_env("EDU_RETRY_GAP_MIN", 56)
EDU_RECENT_KEEP = _int_env("EDU_RECENT_KEEP", 40)
# سقف توکن خروجی مدل (JSON کوتاه است؛ کوچک نگه داشته می‌شود)
EDU_MAX_OUTPUT_TOKENS = _int_env("EDU_MAX_OUTPUT_TOKENS", 800)
# چند عنوان اخیر در پرامپت فرستاده شود (کاهش مصرف توکن)
EDU_RECENT_PROMPT = _int_env("EDU_RECENT_PROMPT", 6)

# OpenRouter — فقط مدل‌های رایگان (fallback بعد از Gemini)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
# فهرست دستی مدل‌ها (با کاما جدا شود). خالی = کشف خودکار مدل‌های :free
OPENROUTER_MODELS = [
    m.strip() for m in (os.getenv("OPENROUTER_MODELS") or "").split(",") if m.strip()
]
# حداکثر چند مدل رایگان در هر تلاش امتحان شود
OPENROUTER_MAX_MODELS = max(1, _int_env("OPENROUTER_MAX_MODELS", 5))
# سرصفحه‌های معرفی‌کننده (اختیاری، برای رتبه‌بندی بهتر OpenRouter)
OPENROUTER_SITE_URL = os.getenv(
    "OPENROUTER_SITE_URL",
    "https://github.com/MahdiHajiabadi675/ai-news-bot-telegram",
)
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "AI News Bot")

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Behavior
# سقف پست در **هر راند اجرا** (نه هر روز) — هر اجرا (مثلاً هر ساعت) تا این تعداد پست می‌رود
MAX_POSTS_PER_RUN = _int_env("MAX_POSTS_PER_RUN", 15)
# سقف امنیتی اختیاری در روز؛ 0 یعنی خاموش (روزانه محدودیتی نیست)
MAX_POSTS_PER_DAY = _int_env("MAX_POSTS_PER_DAY", 0)
COLLECT_INTERVAL_MIN = _int_env("COLLECT_INTERVAL_MIN", 45)

# از هر فید، حداکثر این‌قدر از «جدیدترین» آیتم‌ها (بر اساس تاریخ انتشار، نه ترتیب فید) بررسی می‌شود
MAX_PER_FEED = _int_env("MAX_PER_FEED", 15)
# خبری که از این‌قدر ساعت قدیمی‌تر باشد نادیده گرفته می‌شود (جلوگیری از سیل خبر کهنه هنگام افزودن فید جدید)
MAX_ITEM_AGE_HOURS = _int_env("MAX_ITEM_AGE_HOURS", 72)
# فقط برای نمایش ساعت در گزارش‌ها (ایران = ۲۱۰ دقیقه)
DISPLAY_TZ_OFFSET_MIN = _int_env("DISPLAY_TZ_OFFSET_MIN", 210)

# سقف پردازش در هر اجرا — جلوگیری از تایم‌اوت و اسپم تلگرام
MAX_ARTICLES_PER_RUN = _int_env("MAX_ARTICLES_PER_RUN", 30)
# حداکثر پیام «در انتظار تایید» که در هر اجرا به ادمین می‌رود
MAX_REVIEWS_PER_RUN = _int_env("MAX_REVIEWS_PER_RUN", 5)

# Scoring weights
SCORE_SOURCE_WEIGHT = 20
SCORE_IMPORTANCE_WEIGHT = 30
SCORE_CONFIDENCE_WEIGHT = 30
SCORE_CATEGORY_WEIGHT = 20

# Thresholds
AUTO_PUBLISH_THRESHOLD = 70
AUTO_REJECT_THRESHOLD = 30

# نمایش متن اصلی (انگلیسی) زیر ترجمه فارسی در کانال
SHOW_ORIGINAL_TEXT = os.getenv("SHOW_ORIGINAL_TEXT", "true").lower() == "true"

# ───────── اعتبار پویای منابع ─────────
# اعتبار پایهٔ هر منبع (TRUST_SCORES پایین) فقط «پیش‌فرض اولیه» است.
# با هر تایید/رد ادمین، اعتبار واقعی منبع به‌روز می‌شود (src/source_trust.py).
# وزن پیش‌فرض اولیه: معادل چند «مشاهدهٔ فرضی». هرچه بزرگ‌تر، دیرتر عوض می‌شود.
TRUST_PRIOR_WEIGHT = _int_env("TRUST_PRIOR_WEIGHT", 8)
# نیمه‌عمر شواهد (روز): تصمیم‌های قدیمی‌تر کم‌اثرتر می‌شوند
TRUST_HALF_LIFE_DAYS = _int_env("TRUST_HALF_LIFE_DAYS", 30)
# تایید «ویژه» چند برابر یک تایید معمولی ارزش دارد
TRUST_FEATURE_WEIGHT = 1.5
# حداقل اعتبار پویا برای انتشار خودکار (از 5)
AUTO_TRUST_THRESHOLD = 4.0
# هر منبع مستقل دیگری که همان خبر را داده باشد، چند امتیاز به اعتبار اضافه می‌کند
CORROBORATION_BONUS = 0.5
CORROBORATION_MAX_BONUS = 1.0

# ───────── انتشار خودکار خبرهای «جذاب» از منابع غیررسمی (src/newsworthiness.py) ─────────
# آستانهٔ جذابیت برای منبعی با اعتبار ۳؛ هر یک واحد اعتبار بیشتر، HOT_TRUST_STEP امتیاز از آستانه کم می‌کند
HOT_BASE_THRESHOLD = _int_env("HOT_BASE_THRESHOLD", 60)
HOT_TRUST_STEP = _int_env("HOT_TRUST_STEP", 8)
# منبع رسمی هم اگر جذابیتش زیر این حد باشد (آموزش/لیست/نقد) خودکار منتشر نمی‌شود
HOT_OFFICIAL_MIN = _int_env("HOT_OFFICIAL_MIN", 20)

# Auto-publish settings
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "true").lower() == "true"

# Fast-track settings
FAST_TRACK_ENABLED = os.getenv("FAST_TRACK_ENABLED", "true").lower() == "true"

# واژه‌های تشخیص «خبر دربارهٔ AI» (وزن‌دار): src/ai_terms.py

# Auto-reject keywords
REJECT_KEYWORDS = [
    "crypto", "cryptocurrency", "bitcoin", "blockchain", "nft", "token sale",
    "casino", "betting", "gambling", "forex", "trading signals",
    "giveaway", "free money", "click here", "limited offer",
]

# Source trust scores (1-5) — فقط «پیش‌فرض اولیه»؛ اعتبار واقعی پویاست (src/source_trust.py)
TRUST_SCORES = {
    # آزمایشگاه‌ها و شرکت‌های AI (سایت رسمی)
    "OpenAI Blog": 5,
    "Google AI": 5,
    "Google Research": 5,
    "DeepMind": 5,
    "Anthropic": 5,
    "Meta AI": 5,
    "HuggingFace": 5,
    "Microsoft Research": 4,
    "NVIDIA Blog": 4,
    "AWS ML Blog": 4,
    "Apple ML Research": 4,
    # رسانه‌های معتبر
    "MIT Tech Review": 4,
    "Ars Technica AI": 4,
    "TechCrunch AI": 4,
    "The Verge AI": 3,
    "Wired AI": 3,
    "VentureBeat AI": 3,
    "The Decoder": 3,
    "MarkTechPost": 3,
    "AI News": 3,
    "TechCrunch": 3,
    "The Verge": 3,
    "Ars Technica": 3,
    "دیجیاتو": 3,
    "زومگ": 3,
    "تک‌راسا": 3,
}

# Category weights for scoring
CATEGORY_WEIGHTS = {
    "model_release": 1.0,
    "benchmark": 0.9,
    "funding": 0.8,
    "research": 0.7,
    "policy": 0.6,
    "tool_release": 0.8,
    "industry": 0.5,
    "other": 0.3,
}
