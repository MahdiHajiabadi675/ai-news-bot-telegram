"""
جذابی خبر («هنر جداکردن خبر مهم از خبر معمولی») — بدون LLM

قانون انتشار خودکار:
  • منبع «رسمی» (آزمایشگاه‌ها: OpenAI، Anthropic، DeepMind، Google AI، Meta AI، HuggingFace)
    → خودکار، به شرط اینکه اعتبار پویای منبع ≥ ۴ و خبر شایعه‌نما نباشد.
  • بقیه (رسانه‌ها و بلاگ‌های عمومی) → فقط اگر «جذابیت» خبر از آستانه بیشتر باشد.
    آستانه به اعتبار پویای منبع بستگی دارد: منبعی که تاییدهای بیشتری گرفته، آستانهٔ پایین‌تری دارد.
  • غیر از این‌ها → دکمه‌های تایید برای ادمین.

امتیاز جذابیت (۰ تا ۱۰۰) از این سیگنال‌ها ساخته می‌شود:
  دستهٔ خبر (انتشار مدل، بنچمارک، سرمایه، قانون، …)  · نام بازیگر بزرگ در عنوان
  · فعل رویداد (معرفی، عرضه، خرید، …)  · شمارهٔ نسخهٔ مدل  · رقم‌های مالی بزرگ
  · «چند منبع مستقل همان خبر را داده‌اند» (قوی‌ترین سیگنال)
  · جریمه: لیست‌های «بهترین‌ها»، آموزش، نقد و بررسی، مصاحبه، تخفیف، عنوان پرسشی، شایعه
"""
import re

from .classifier import _compile_en, _compile_fa, normalize_fa
from .config import HOT_BASE_THRESHOLD, HOT_TRUST_STEP

_CATEGORY_POINTS = {
    "model_release": 35, "benchmark": 30, "funding": 25, "policy": 25,
    "tool_release": 20, "research": 15, "industry": 10, "other": 5,
}

_ENTITIES_EN = [
    "openai", "anthropic", "google", "gemini", "deepmind", "meta", "llama", "microsoft",
    "nvidia", "apple", "x\\.?ai", "grok", "deepseek", "mistral", "qwen", "alibaba", "amazon",
    "tesla", "perplexity", "midjourney", "hugging\\s?face", "stability\\s?ai", "baidu", "tencent",
    "sam\\s+altman", "elon\\s+musk", "demis\\s+hassabis", "dario\\s+amodei", "jensen\\s+huang",
]
_ENTITIES_FA = [
    "اوپن ای آی", "اپن ای آی", "آنتروپیک", "گوگل", "جمینای", "دیپ مایند", "متا", "مایکروسافت",
    "انویدیا", "اپل", "گراک", "دیپ سیک", "میسترال", "علی بابا", "آمازون", "تسلا",
    "ایلان ماسک", "سم آلتمن", "هاگینگ فیس",
]
_VERBS_EN = (
    r"releas\w*|launch\w*|unveil\w*|introduc\w*|announc\w*|debut\w*|open-?sourc\w*|acquir\w*|"
    r"acquisition|partner\w*|raises?|bans?|banned|sues?|lawsuit|record|beats?|outperform\w*|"
    r"rolls?\s+out|shuts?\s+down|layoffs?|breakthrough|surpass\w*|ships?"
)
_VERBS_FA = ["معرفی", "رونمایی", "عرضه", "انتشار", "راه اندازی", "خرید", "جذب سرمایه", "ممنوع", "رکورد", "شکست داد", "پیشی گرفت"]

_ENT_EN = [_compile_en(e) for e in _ENTITIES_EN]
_ENT_FA = [_compile_fa(e) for e in _ENTITIES_FA]
_VERB_EN = _compile_en(_VERBS_EN)
_VERB_FA = [_compile_fa(v) for v in _VERBS_FA]

_VERSION = _compile_en(
    r"(?:gpt|llama|gemini|claude|qwen|grok|deepseek|mistral|sora|o)[\s\-]?v?\d+(?:\.\d+)?|opus|sonnet|haiku"
)
_BIG_MONEY = re.compile(r"(?:\$|€)\s?\d[\d.,]*\s?(?:b|bn|billion|t|trillion)\b|\b\d[\d.,]*\s?(?:billion|trillion)\b"
                        r"|\d[\d.,٫]*\s?(?:میلیارد|تریلیون)", re.IGNORECASE)
_SMALL_MONEY = re.compile(r"(?:\$|€)\s?\d[\d.,]*\s?(?:m|mn|million)\b|\b\d[\d.,]*\s?million\b|\d[\d.,٫]*\s?میلیون", re.IGNORECASE)

_BORING = re.compile(
    r"\b(best|top\s+\d+|\d+\s+(?:ways|things|tips|tools)|how\s+to|guide|review|deals?|discounts?|opinion|"
    r"podcast|newsletter|weekly|roundup|sponsored|giveaway|explained|what\s+is|tips|tutorial|interview|"
    r"webinar|event\s+recap)\b|بهترین|آموزش|راهنمای|بررسی|تخفیف|مصاحبه|پادکست|خبرنامه",
    re.IGNORECASE,
)


def hot_threshold(trust: float) -> float:
    """آستانهٔ جذابیت برای انتشار خودکار؛ اعتبار بالاتر → آستانهٔ پایین‌تر (بین ۳۵ و ۸۰)"""
    return max(35.0, min(80.0, HOT_BASE_THRESHOLD - HOT_TRUST_STEP * (trust - 3)))


def newsworthiness(item: dict, trust: float) -> dict:
    """
    برمی‌گرداند: {"score": 0..100, "reasons": [..دلیل‌های فارسی..], "threshold": آستانه}
    item باید title، summary، category، confidence و corroborated_by داشته باشد.
    """
    from .classifier import ai_relevance  # import دیرهنگام: classifier خودش این ماژول را نمی‌شناسد

    title = item.get("title", "") or ""
    text = f"{title} {(item.get('summary') or '')[:300]}"
    norm_title = normalize_fa(title)
    score, reasons = 0.0, []

    cat = item.get("category", "other")
    pts = _CATEGORY_POINTS.get(cat, 5)
    score += pts
    if pts >= 25:
        reasons.append({"model_release": "انتشار مدل", "benchmark": "بنچمارک", "funding": "سرمایه‌گذاری",
                        "policy": "قانون/سیاست"}.get(cat, cat))

    has_entity = any(rx.search(title) for rx in _ENT_EN) or any(rx.search(norm_title) for rx in _ENT_FA)
    has_verb = bool(_VERB_EN.search(title)) or any(rx.search(norm_title) for rx in _VERB_FA)
    if has_entity:
        score += 20
        reasons.append("بازیگر بزرگ")
    if has_verb:
        score += 15
        reasons.append("رویداد مشخص")
    if has_entity and has_verb:
        score += 10

    if _VERSION.search(title):
        score += 10
        reasons.append("نسخهٔ مدل")
    if _BIG_MONEY.search(text):
        score += 15
        reasons.append("رقم بزرگ")
    elif _SMALL_MONEY.search(text):
        score += 5

    others = int(item.get("corroborated_by") or 0)
    if others:
        score += min(50, 25 * others)
        reasons.append(f"{others} منبع دیگر هم داده")

    rel = ai_relevance(title)
    if rel >= 6:
        score += 10
    elif rel < 3:
        score -= 20
        reasons.append("ربط کم با AI در عنوان")

    if _BORING.search(title):
        score -= 35
        reasons.append("قالب غیرخبری (لیست/آموزش/نقد)")
    if title.rstrip().endswith(("?", "؟")):
        score -= 10
    if item.get("confidence") == "rumor":
        score -= 30
        reasons.append("شایعه")

    return {"score": int(max(0, min(100, score))), "reasons": reasons, "threshold": hot_threshold(trust)}
