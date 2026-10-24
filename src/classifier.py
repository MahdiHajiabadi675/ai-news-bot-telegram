"""
طبقه‌بندی محلی خبر (بدون LLM) — جایگزین تحلیل Gemini

سه خروجی: category، importance، confidence. همه با قواعد ساده و شفاف:
  • category: از کلیدواژه‌های عنوان/خلاصه
  • confidence: شایعه‌نما بودن متن + اعتبار پویای منبع + تایید چندمنبعی
  • importance: دسته + اعتبار منبع + تایید چندمنبعی
"""
import re

from .ai_terms import EN_TERMS, FA_TERMS, PASS_SCORE

_PERSIAN_LETTER = "\u0621-\u06CC"


def normalize_fa(text: str) -> str:
    """یکسان‌سازی متن فارسی: نیم‌فاصله→فاصله، ي/ك عربی→ی/ک، فاصله‌های تکراری حذف"""
    text = (text or "").replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", text).strip()


def _compile_en(term: str):
    return re.compile(r"(?<![A-Za-z0-9])(?:" + term + r")(?![A-Za-z0-9])", re.IGNORECASE)


def _compile_fa(term: str):
    words = normalize_fa(term).split(" ")
    body = r"\s?".join(re.escape(w) for w in words)
    # فقط مرز ابتدای کلمه؛ برای واژهٔ کوتاه (≤ ۴ حرف) مرز انتهای کلمه هم لازم است
    tail = rf"(?![{_PERSIAN_LETTER}])" if len(term.replace(" ", "")) <= 4 else ""
    return re.compile(rf"(?<![{_PERSIAN_LETTER}]){body}{tail}")


_EN_COMPILED = [(t, _compile_en(t), w) for t, w in EN_TERMS]
_FA_COMPILED = [(t, _compile_fa(t), w) for t, w in FA_TERMS]


def _matches(text: str) -> dict:
    """واژه‌های تطبیق‌خورده در متن: {الگو: وزن}"""
    if not text:
        return {}
    norm = normalize_fa(text)
    found = {}
    for term, rx, w in _EN_COMPILED:
        if rx.search(text):
            found[term] = w
    for term, rx, w in _FA_COMPILED:
        if rx.search(norm):
            found[term] = w
    return found


def ai_relevance(title: str, summary: str = "", tags: str = "") -> float:
    """
    امتیاز ارتباط با AI: هر واژه در «عنوان+تگ‌ها» ×۲ و در «خلاصه» ×۱ (هر واژه یک بار).
    خبر وقتی مرتبط است که امتیاز ≥ PASS_SCORE باشد (src/ai_terms.py).
    """
    head = _matches(f"{title or ''} {tags or ''}")
    body = _matches(summary or "")
    score = sum(w * 2 for w in head.values())
    score += sum(w for t, w in body.items() if t not in head)
    return score


def is_ai_related(text: str) -> bool:
    """نسخهٔ ساده برای یک متن واحد (مثلاً fast-track)"""
    return ai_relevance("", text) >= PASS_SCORE


def is_english(text: str) -> bool:
    """آیا متن انگلیسیه؟ (بیش از ۵۰٪ حروف لاتین)"""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isascii()) / len(letters) > 0.5


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
