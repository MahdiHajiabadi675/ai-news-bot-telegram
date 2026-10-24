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

# Gemini — فعلاً کنار گذاشته شده (در پایپ‌لاین استفاده نمی‌شود).
# ترجمه کاملاً با src/translator.py انجام می‌شود. این تنظیمات فقط برای
# src/gemini_parked.py و src/gemini_budget.py نگه داشته شده‌اند تا ایدهٔ بعدی
# رویشان پیاده شود.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MIN_INTERVAL_MIN = _int_env("GEMINI_MIN_INTERVAL_MIN", 55)
GEMINI_MAX_CALLS_PER_DAY = _int_env("GEMINI_MAX_CALLS_PER_DAY", 24)
GEMINI_BATCH_SIZE = _int_env("GEMINI_BATCH_SIZE", 10)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Behavior
# سقف پست در **هر راند اجرا** (نه هر روز) — هر اجرا (مثلاً هر ساعت) تا این تعداد پست می‌رود
MAX_POSTS_PER_RUN = _int_env("MAX_POSTS_PER_RUN", 15)
# سقف امنیتی اختیاری در روز؛ 0 یعنی خاموش (روزانه محدودیتی نیست)
MAX_POSTS_PER_DAY = _int_env("MAX_POSTS_PER_DAY", 0)
COLLECT_INTERVAL_MIN = _int_env("COLLECT_INTERVAL_MIN", 45)

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

# Auto-publish settings
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "true").lower() == "true"

# Fast-track settings
FAST_TRACK_ENABLED = os.getenv("FAST_TRACK_ENABLED", "true").lower() == "true"

# کلیدواژه‌های مرتبط با AI (فیلتر فیدهای عمومی و fast-track)
# انگلیسی با مرز کلمه تطبیق داده می‌شود (تا "ai" داخل "said" پیدا نشود)،
# فارسی به‌صورت زیررشته. کلمات خیلی عمومی (مثل "مدل"، "ماشین"، "training") حذف شده‌اند.
AI_KEYWORDS = [
    # فارسی
    "هوش مصنوعی", "یادگیری ماشین", "یادگیری عمیق", "شبکه عصبی", "مدل زبانی",
    "چت‌بات", "چت جی‌پی‌تی", "جمینای", "دیپ‌سیک", "اوپن‌ای‌آی", "آنتروپیک",
    # انگلیسی
    "AI", "A.I.", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "LLM", "LLMs", "large language model", "GPT", "ChatGPT",
    "OpenAI", "Anthropic", "Claude", "Gemini", "DeepSeek", "Llama", "Mistral",
    "Copilot", "transformer", "diffusion model", "generative", "fine-tuning",
    "chatbot", "agentic", "Sora", "Midjourney", "Hugging Face", "AI chip", "GPU", "CUDA",
]

# Auto-reject keywords
REJECT_KEYWORDS = [
    "crypto", "cryptocurrency", "bitcoin", "blockchain", "nft", "token sale",
    "casino", "betting", "gambling", "forex", "trading signals",
    "giveaway", "free money", "click here", "limited offer",
]

# Source trust scores (1-5)
TRUST_SCORES = {
    "OpenAI Blog": 5,
    "Google AI": 5,
    "DeepMind": 5,
    "Anthropic": 5,
    "Meta AI": 5,
    "HuggingFace": 5,
    "Microsoft AI": 5,
    "MIT Tech Review": 4,
    "Ars Technica AI": 4,
    "TechCrunch AI": 4,
    "Papers with Code": 4,
    "The Verge AI": 3,
    "Wired AI": 3,
    "VentureBeat AI": 3,
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
