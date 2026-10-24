"""
طبقه‌بندی محلی خبر (بدون LLM) — جایگزین تحلیل Gemini

سه خروجی: category، importance، confidence. همه با قواعد ساده و شفاف:
  • category: از کلیدواژه‌های عنوان/خلاصه
  • confidence: شایعه‌نما بودن متن + اعتبار پویای منبع + تایید چندمنبعی
  • importance: دسته + اعتبار منبع + تایید چندمنبعی
"""
import re

from .config import AI_KEYWORDS

_ASCII_KW = [k for k in AI_KEYWORDS if k.isascii()]
_FA_KW = [k for k in AI_KEYWORDS if not k.isascii()]
_AI_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(k) for k in _ASCII_KW) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def normalize_fa(text: str) -> str:
    """یکسان‌سازی متن فارسی: نیم‌فاصله→فاصله، ي/ك عربی→ی/ک، فاصله‌های تکراری حذف"""
    text = (text or "").replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", text).strip()


_FA_NORM = [normalize_fa(k) for k in _FA_KW]
_FA_COMPACT = [k.replace(" ", "") for k in _FA_NORM]


def is_english(text: str) -> bool:
    """آیا متن انگلیسیه؟ (بیش از ۵۰٪ حروف لاتین)"""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isascii()) / len(letters) > 0.5


def is_ai_related(text: str) -> bool:
    """
    مرتبط با AI؟ انگلیسی با مرز کلمه؛ فارسی زیررشته روی متن نرمال‌شده
    (پس «هوش‌مصنوعی»، «هوش مصنوعی» و «هوشمصنوعی» هر سه پیدا می‌شوند)
    """
    if not text:
        return False
    if _AI_RE.search(text):
        return True
    norm = normalize_fa(text)
    compact = norm.replace(" ", "")
    return any(k in norm for k in _FA_NORM) or any(k in compact for k in _FA_COMPACT)


_RUMOR_RE = re.compile(
    r"\b(rumou?rs?|reportedly|allegedly|leak(?:s|ed)?|according to (?:people|sources)|"
    r"sources say|said to|claims?)\b|شایعه|ادعا|گزارش‌ها حاکی|به گفتهٔ منابع",
    re.IGNORECASE,
)

# ترتیب مهم است: اولین دستهٔ منطبق برنده می‌شود
_CATEGORY_RULES = [
    ("benchmark", re.compile(r"\b(benchmarks?|leaderboard|sota|state-of-the-art|outperforms?)\b|بنچمارک|محک", re.I)),
    ("funding", re.compile(r"\b(raises?|funding|series [a-e]|valuation|acquires?|acquisition|invests?|ipo|billion)\b|سرمایه|جذب|خرید شرکت", re.I)),
    ("policy", re.compile(r"\b(regulat\w*|lawsuit|sues?|court|copyright|senate|congress|ban(?:s|ned)?|eu ai act|legislation)\b|قانون|مقررات|دادگاه|ممنوع", re.I)),
    ("model_release", re.compile(
        r"\b(releas\w+|launch\w*|introduc\w+|unveil\w*|announc\w+|debut\w*|open-?sourc\w+)\b.*"
        r"\b(model|gpt|llama|gemini|claude|mistral|qwen|deepseek|sora|agent)\b|"
        r"\b(model|gpt|llama|gemini|claude|mistral|qwen|deepseek|sora)\b.*\b(releas\w+|launch\w*|unveil\w*|debut\w*)\b|"
        r"معرفی مدل|انتشار مدل|عرضه|رونمایی", re.I | re.S)),
    ("research", re.compile(r"\b(paper|study|researchers?|arxiv|breakthrough|findings)\b|پژوهش|مقاله|تحقیق", re.I)),
    ("tool_release", re.compile(r"\b(app|feature|plugin|api|sdk|copilot|extension|update|tool)\b|ابزار|قابلیت|افزونه|به‌روزرسانی", re.I)),
]


def classify(item: dict, trust: float, corroborated_by: int = 0) -> dict:
    """
    trust: اعتبار پویای منبع (۱ تا ۵)، corroborated_by: تعداد منبع‌های مستقل دیگر
    برمی‌گرداند: {category, importance, confidence, corroborated_by}
    """
    text = f"{item.get('title', '')} {item.get('summary', '')[:600]}"

    category = "other"
    for name, rx in _CATEGORY_RULES:
        if rx.search(text):
            category = name
            break

    # اطمینان: شایعه‌نما → rumor (مگر منبع در بالاترین سطح اعتبار باشد)
    if _RUMOR_RE.search(text) and trust < 4.8 and corroborated_by < 2:
        confidence = "rumor"
    elif trust >= 4.0 or corroborated_by >= 2:
        confidence = "confirmed"
    else:
        confidence = "likely"

    # اهمیت
    big = category in ("model_release", "benchmark", "policy") or (
        category == "funding" and re.search(r"\bbillion\b|میلیارد", text, re.I)
    )
    if big and (trust >= 4.5 or corroborated_by >= 2):
        importance = "high"
    elif category == "other" and trust < 3.5 and corroborated_by == 0:
        importance = "low"
    else:
        importance = "medium"

    return {
        "category": category,
        "importance": importance,
        "confidence": confidence,
        "corroborated_by": corroborated_by,
    }
