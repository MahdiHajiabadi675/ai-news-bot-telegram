"""
هشتگ‌های هدفمند — به‌جای سه هشتگ ثابت، هشتگ‌هایی که واقعاً کسی دنبالشان می‌گردد.

ترکیب:
  • موجودیت خبر (OpenAI، Anthropic، Google، Qwen، ...) → جذاب‌ترین هشتگ
  • دستهٔ خبر (مدل_جدید، بنچمارک، سرمایه_گذاری، ...)
  • موضوع (ایمنی_AI، متن_باز، ایجنت، کدنویسی_با_AI، رباتیک، ...)
  • هشتگ پایه (هوش_مصنوعی)

سقف ۴ هشتگ، بدون تکرار، فقط حروف/عدد/زیرخط. همین لیست در بله هم می‌آید
(قالب بله هشتگ‌ها را از همان فیلد می‌خواند).
"""
import re

MAX_TAGS = 4
BASE_TAGS = ["هوش_مصنوعی"]

CATEGORY_TAGS = {
    "model_release": "مدل_جدید",
    "benchmark": "بنچمارک",
    "funding": "سرمایه_گذاری",
    "research": "پژوهش_AI",
    "policy": "قانون_AI",
    "tool_release": "ابزار_AI",
    "industry": "صنعت_AI",
    "other": "",
}

# (کلیدواژه‌ها، هشتگ) — به ترتیب اولویت؛ اولین تطابق برنده است
ENTITY_TAGS = [
    (("openai", "chatgpt", "gpt-"), "OpenAI"),
    (("anthropic", "claude"), "Anthropic"),
    (("deepmind", "google", "gemini", "bard"), "Google_AI"),
    (("meta ai", "llama", "meta-"), "Meta_AI"),
    (("microsoft", "copilot"), "Microsoft"),
    (("nvidia",), "NVIDIA"),
    (("apple",), "Apple"),
    (("amazon", "aws", "bedrock"), "AWS"),
    (("huggingface", "hugging face"), "HuggingFace"),
    (("mistral",), "Mistral"),
    (("qwen", "alibaba"), "Qwen"),
    (("deepseek",), "DeepSeek"),
    (("grok", "xai", "x.ai"), "xAI"),
    (("stability", "stable diffusion"), "Stability_AI"),
    (("perplexity",), "Perplexity"),
]

TOPIC_TAGS = [
    (("alignment", "safety", "ایمنی", "امنیت", "harm"), "ایمنی_AI"),
    (("open source", "open-source", "open weights", "متن‌باز", "متن باز"), "متن_باز"),
    (("agent", "agents", "agentic", "عامل خودکار"), "ایجنت"),
    (("image", "video", "تصویر", "ویدیو", "midjourney", "sora"), "تصویرسازی_AI"),
    (("coding", "code", "developer", "programming", "کدنویسی", "برنامه‌نویسی"), "کدنویسی_با_AI"),
    (("regulation", "law", "policy", "قانون", "مقررات"), "قانون_گذاری_AI"),
    (("benchmark", "leaderboard", "بنچمارک"), "بنچمارک"),
    (("robot", "robotics", "ربات"), "رباتیک"),
    (("health", "medical", "پزشکی", "سلامت"), "AI_پزشکی"),
    (("education", "school", "آموزش", "مدرسه"), "AI_آموزشی"),
]

_BAD = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)


def _clean(tag: str) -> str:
    tag = _BAD.sub("_", str(tag or "")).strip("_")
    return tag[:40]


def build(item: dict) -> list:
    """لیست هشتگ‌ها برای یک خبر (حداکثر ۴، بدون تکرار)"""
    text = " ".join(str(item.get(k) or "") for k in ("title", "summary"))
    low = text.lower()

    tags = []
    for keywords, tag in ENTITY_TAGS:
        if any(k in low for k in keywords):
            tags.append(tag)
            break

    category = CATEGORY_TAGS.get(str(item.get("category") or ""), "")
    if category:
        tags.append(category)

    for keywords, tag in TOPIC_TAGS:
        if any(k in low for k in keywords) and tag not in tags:
            tags.append(tag)
            break

    tags.extend(BASE_TAGS)
    # فقط برای اینکه ترتیب را با تغییرات آینده از دست ندهیم
    seen, out = set(), []
    for tag in tags:
        clean = _clean(tag)
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out[:MAX_TAGS]
