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


# ───────── سیگنال Hacker News ─────────
# برای چند خبر برتر هر اجرا، امتیاز/نظر HN خوانده می‌شود (API رایگان، بدون کلید)
HN_ENABLED = os.getenv("HN_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
HN_MAX_ITEMS = _int_env("HN_MAX_ITEMS", 5)

# ───────── مقالهٔ روز arXiv ─────────
# یک مقالهٔ تازهٔ cs.AI در روز، از این ساعت (به وقت ایران) به بعد
ARXIV_ENABLED = os.getenv("ARXIV_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
ARXIV_HOUR = _int_env("ARXIV_HOUR", 13)

# ───────── حالت تمرین (dry-run) ─────────
# با `python main.py run --dry-run` روشن می‌شود: هیچ پیامی به تلگرام/بله نمی‌رود و
# هیچ چیزی در KV نوشته نمی‌شود؛ فقط خواندن و چاپ قالب نهایی. برای تست محلی امن.
DRY_RUN = (os.getenv("DRY_RUN", "") or "").strip().lower() in ("1", "true", "yes", "on")

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
# هدف پست آموزشی در هر روز (۴ پست شبانه). انتشار تا رسیدن به این عدد ادامه دارد،
# حتی اگر اجرای ساعتی GitHub عقب افتاده باشد (جبران تأخیر).
EDU_POSTS_PER_DAY = _int_env("EDU_POSTS_PER_DAY", 4)
# وقتی عقب هستیم، حداکثر چند پست در یک اجرا جبران شود (۲ = یک پست معمول + یک جبرانی)
EDU_CATCHUP_MAX = _int_env("EDU_CATCHUP_MAX", 2)
# اگر مدل‌ها (Gemini/OpenRouter) خطا دادند و صف خالی بود، از پست‌های آمادهٔ محلی
# (بدون مدل) استفاده شود تا شب بدون پست نماند.
EDU_USE_BUILTIN_FALLBACK = os.getenv(
    "EDU_BUILTIN_FALLBACK", "true"
).strip().lower() not in ("0", "false", "no", "off")
# چند کوییز در هر شب (۰ = خاموش). کوییزها جدا از ۴ پست متنی‌اند؛ پیش‌فرض ۲ (اول و آخر بازه)
EDU_QUIZ_PER_DAY = _int_env("EDU_QUIZ_PER_DAY", 2)
# حداکثر پست ساخته‌شده و منتظر انتشار (اگر پر باشد ساخت متوقف می‌شود)
EDU_QUEUE_MAX = _int_env("EDU_QUEUE_MAX", 8)
# کنترل مصرف توکن: حداکثر خروجی «ردشده» (جواب ۲۰۰ که قبول نشد = توکن خرج‌شده) در هر تلاش،
# و حداکثر تلاش ساخت در هر روز (همهٔ اسلات‌ها)
EDU_MAX_REJECTS = _int_env("EDU_MAX_REJECTS", 2)
EDU_MAX_ATTEMPTS_PER_DAY = _int_env("EDU_MAX_ATTEMPTS_PER_DAY", 10)
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

# ───────── بازخورد بازدید (src/views.py) ─────────
# تلگرام Bot API بازدید نمی‌دهد؛ از پیش‌نمایش عمومی کانال (t.me/s/...) خوانده می‌شود.
# برای کانال با آیدی عددی، CHANNEL_HANDLE را دستی تنظیم کن: @your_channel
VIEWS_ENABLED = os.getenv("VIEWS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
CHANNEL_HANDLE = (os.getenv("CHANNEL_HANDLE", "") or "").strip()
# حداقل فاصلهٔ دو همگام‌سازی بازدید (دقیقه) — هر اجرای ساعتی، حداکثر یک‌بار می‌خواند
VIEWS_MIN_INTERVAL_MIN = _int_env("VIEWS_MIN_INTERVAL_MIN", 30)
# چند صفحهٔ آخر (هر صفحه ~۲۰ پست)
VIEWS_PAGES = _int_env("VIEWS_PAGES", 2)
# حداکثر امتیاز «جذابیت» که دستهٔ پرخوان به خبر اضافه می‌کند
VIEWS_BOOST_MAX = _int_env("VIEWS_BOOST_MAX", 8)

# ───────── واکنش‌های تلگرام (Reactions) ─────────
# واکنش‌ها را Worker (webhook) در KV ذخیره می‌کند؛ دقیق‌تر و سریع‌تر از بازدید است.
REACTIONS_ENABLED = os.getenv("REACTIONS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# هر واکنش معادل چند بازدید در محاسبهٔ محبوبیت دسته‌ها
REACTIONS_WEIGHT = _int_env("REACTIONS_WEIGHT", 10)
# حداکثر امتیاز جذابیتی که واکنش‌ها به دستهٔ محبوب می‌دهند
REACTIONS_BOOST_MAX = _int_env("REACTIONS_BOOST_MAX", 6)

# ───────── بهداشت انتشار ─────────
# ساعت‌های سکوت به وقت ایران، مثل "0-7" یا "23-7". خالی = خاموش.
QUIET_HOURS = (os.getenv("QUIET_HOURS", "") or "").strip()
# حداقل فاصلهٔ بین دو «راند» انتشار (دقیقه). 0 = خاموش. داخل یک راند اعمال نمی‌شود.
PUBLISH_MIN_GAP_MIN = _int_env("PUBLISH_MIN_GAP_MIN", 0)
# دکمهٔ «فرستادن به دوست» زیر پست‌های خبری
SHARE_BUTTON_ENABLED = os.getenv("SHARE_BUTTON_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")

# ───────── صبحانهٔ AI (src/brief.py) ─────────
BRIEF_ENABLED = os.getenv("BRIEF_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# ساعت انتشار به وقت ایران (۰ تا ۲۳)
BRIEF_HOUR = _int_env("BRIEF_HOUR", 8)
BRIEF_ITEMS = _int_env("BRIEF_ITEMS", 5)

# ───────── جمع‌بندی و نظرسنجی هفتگی (src/weekly.py) ─────────
WEEKLY_ENABLED = os.getenv("WEEKLY_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# روز هفته به وقت ایران: دوشنبه=0 ... شنبه=5 یکشنبه=6
WEEKLY_DAY = _int_env("WEEKLY_DAY", 5)
WEEKLY_HOUR = _int_env("WEEKLY_HOUR", 21)
WEEKLY_ITEMS = _int_env("WEEKLY_ITEMS", 3)
# نظرسنجی بعد از این‌همه ساعت بسته و نتیجه‌اش خودکار اعمال می‌شود
POLL_CLOSE_HOURS = _int_env("POLL_CLOSE_HOURS", 48)
# کمتر از این تعداد رأی → نتیجه اعمال نمی‌شود (نویز)
POLL_MIN_VOTES = _int_env("POLL_MIN_VOTES", 3)
# امتیاز جذابیتی که دستهٔ برندهٔ نظرسنجی می‌گیرد
POLL_BOOST = _int_env("POLL_BOOST", 6)
# چند روز این بوست فعال بماند
POLL_BOOST_DAYS = _int_env("POLL_BOOST_DAYS", 7)

# ───────── پیش‌زمینهٔ موضوعی / تایم‌لاین (src/storylines.py) ─────────
STORYLINES_ENABLED = os.getenv("STORYLINES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
STORY_WINDOW_DAYS = _int_env("STORY_WINDOW_DAYS", 45)
STORY_LINKS_MAX = _int_env("STORY_LINKS_MAX", 2)

# ───────── تلاش مجدد ترجمه ─────────
# اگر ترجمه شکست بخورد، خبر یک راند در صف می‌ماند و دوباره تلاش می‌شود.
TRANSLATE_RETRY_MAX = _int_env("TRANSLATE_RETRY_MAX", 2)

# ───────── پشتیبان‌گیری KV (src/backup.py) ─────────
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# یکشنبه=6
BACKUP_DAY = _int_env("BACKUP_DAY", 6)
BACKUP_HOUR = _int_env("BACKUP_HOUR", 3)

# ───────── خبرنگار مردمی (src/tips.py) ─────────
TIPS_ENABLED = os.getenv("TIPS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
TIPS_MAX_PER_HOUR = _int_env("TIPS_MAX_PER_HOUR", 1)

# ───────── خلاصهٔ خصوصی برای مشترکان (src/subscribers.py) ─────────
DM_DIGEST_ENABLED = os.getenv("DM_DIGEST_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
DM_DIGEST_MAX = _int_env("DM_DIGEST_MAX", 200)

# ───────── «چرا مهم است» (src/why.py) ─────────
# یک خط تحلیل محلی و بدون مدل زیر هر خبر
SHOW_WHY_IT_MATTERS = os.getenv(
    "SHOW_WHY_IT_MATTERS", "true"
).strip().lower() not in ("0", "false", "no", "off")

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

# امضای کوتاه انتهای هر پست (اختیاری) — برای رشد ارگانیک؛ متن ساده، مثلاً «@your_channel»
CHANNEL_SIGNATURE = (os.getenv("CHANNEL_SIGNATURE", "") or "").strip()

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
    # آزمایشگاه‌های تازه‌اضافه‌شده
    "Mistral AI": 5,
    "Qwen Blog": 5,
    # تحلیل و خبرنامهٔ تخصصی
    "AI News": 4,
    "Import AI": 4,
    "Interconnects": 4,
    "Simon Willison": 4,
    "The Gradient": 3,
    "Latent Space": 3,
    "One Useful Thing": 3,
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
    # رسانه‌های عمومی بین‌المللی
    "404 Media": 3,
    "Techmeme": 3,
    "BBC Tech": 3,
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
