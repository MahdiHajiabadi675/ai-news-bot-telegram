"""
Analyzer — تحلیل محلی اخبار (بدون Gemini)

مراحل:
  analyze_articles(): اعتبار پویای منبع + تایید چندمنبعی + طبقه‌بندی (src/classifier.py)
  localize():         ترجمهٔ فارسی با src/translator.py (برای همهٔ منابع، بدون استثنا)

ترجمه عمداً جدا از تحلیل است تا فقط برای خبرهای «زنده‌مانده» (رد نشده با امتیاز پایین)
درخواست ترجمه زده شود.
"""
from . import source_trust
from .classifier import classify, is_english
from . import translator
from .newsworthiness import newsworthiness

_CATEGORY_TAG = {
    "model_release": "مدل_جدید",
    "benchmark": "بنچمارک",
    "funding": "سرمایه_گذاری",
    "research": "پژوهش",
    "policy": "قوانین_AI",
    "tool_release": "ابزار_AI",
}

_QUESTIONS = {
    "rumor": "این خبر شایعه‌نماست و تایید رسمی ندارد. منتشر شود؟",
    "likely": "منبع این خبر هنوز اعتبار کافی برای انتشار خودکار ندارد. منتشر شود؟",
}


def analyze_articles(articles: list, stats: dict) -> list:
    """طبقه‌بندی و اعتبارسنجی پویا. ورودی: خبرهای خام collect()"""
    if not articles:
        return []
    corroboration = source_trust.corroboration_counts(articles)
    analyzed = []
    for a in articles:
        key = a.get("id") or a.get("hash") or ""
        base_trust = source_trust.trust_of(a["source"], stats)
        others = corroboration.get(key or id(a), 0)
        trust = source_trust.with_corroboration(base_trust, others)
        cls = classify(a, trust, others)

        tags = ["هوش_مصنوعی", "اخبار_AI"]
        if cls["category"] in _CATEGORY_TAG:
            tags.append(_CATEGORY_TAG[cls["category"]])

        item_for_hot = {**a, **cls}
        hot = newsworthiness(item_for_hot, trust)

        analyzed.append({
            "id": key,
            "source": a["source"],
            "lang": a.get("lang", "en"),
            "trusted": a.get("trusted", False),
            "title": a["title"],
            "link": a["link"],
            "summary": a.get("summary", ""),
            "source_trust": trust,
            "source_trust_base": base_trust,
            "source_prior": source_trust.prior_of(a["source"]),
            "official": bool(a.get("official", False)),
            "hot": hot["score"],
            "hot_threshold": hot["threshold"],
            "hot_reasons": hot["reasons"],
            **cls,
            "hashtags": tags,
            "admin_question": _QUESTIONS.get(cls["confidence"], ""),
            "status": "analyzed",
        })
    return analyzed


def localize(item: dict) -> dict:
    """فیلدهای فارسی (title_fa, summary_fa) و پرچم translated را روی item می‌نویسد"""
    title = item.get("title", "")
    summary = item.get("summary", "") or ""

    if item.get("lang") == "fa" and not is_english(title):
        # منبع فارسی: نیازی به ترجمه نیست
        item["title_fa"] = title
        item["summary_fa"] = summary[:400]
        item["translated"] = True
        return item

    result = translator.translate_article(title, summary, "en")
    item.update(result)
    return item
