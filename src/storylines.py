"""
پیش‌زمینهٔ موضوعی (تایم‌لاین) — بدون مدل و بدون هزینه.

ایده: خبرهای AI تکرارشونده‌اند (OpenAI، Gemini، Nvidia...). وقتی خبر جدیدی
دربارهٔ همان موجودیت منتشر می‌شود، یک خط «پیش‌زمینه» با لینک ۱–۲ پست قبلی
زیرش می‌آید. این هم به خواننده زمینه می‌دهد و هم بازدید پست‌های قدیمی را
بالا می‌برد.

ذخیره‌سازی: KV با کلید `stories`:
    {"openai": [{id, title, message_id, published_at}, ...], ...}
"""
import re
from datetime import datetime, timedelta

from . import cloudflare_kv
from .config import CHANNEL_HANDLE, CHANNEL_ID, DRY_RUN, STORYLINES_ENABLED, STORY_LINKS_MAX, STORY_WINDOW_DAYS
from .formatting import clip

KEY = "stories"
MAX_PER_ENTITY = 40
MAX_ENTITIES = 60

# (کلید یکتا، برچسب فارسی، الگوهای انگلیسی/فارسی)
ENTITIES = [
    ("openai", "OpenAI", (r"\bopenai\b", r"اوپن\s*ای\s*آی")),
    ("chatgpt", "ChatGPT", (r"\bchat\s*gpt\b", r"چت\s*جی\s*پی\s*تی")),
    ("gpt", "GPT", (r"\bgpt[-\s]?\d", r"\bgpt\b")),
    ("anthropic", "Anthropic", (r"\banthropic\b",)),
    ("claude", "Claude", (r"\bclaude\b", r"کلود")),
    ("google", "Google", (r"\bgoogle\b", r"\bdeepmind\b", r"گوگل")),
    ("gemini", "Gemini", (r"\bgemini\b", r"جمینای")),
    ("meta", "Meta", (r"\bmeta\b", r"\bfacebook\b", r"متا")),
    ("llama", "Llama", (r"\bllama\b", r"لاما")),
    ("microsoft", "Microsoft", (r"\bmicrosoft\b", r"\bcopilot\b", r"مایکروسافت")),
    ("nvidia", "Nvidia", (r"\bnvidia\b", r"انویدیا")),
    ("apple", "Apple", (r"\bapple\b", r"اپل")),
    ("amazon", "Amazon", (r"\bamazon\b", r"\baws\b", r"آمازون")),
    ("xai", "xAI", (r"\bxai\b", r"\bgrok\b")),
    ("mistral", "Mistral", (r"\bmistral\b", r"میسترال")),
    ("deepseek", "DeepSeek", (r"\bdeepseek\b", r"دیپ\s*سیک")),
    ("qwen", "Qwen", (r"\bqwen\b", r"\balibaba\b")),
    ("sora", "Sora", (r"\bsora\b",)),
    ("midjourney", "Midjourney", (r"\bmidjourney\b",)),
    ("runway", "Runway", (r"\brunway\b",)),
    ("stability", "Stability AI", (r"\bstability\s*ai\b",)),
    ("huggingface", "Hugging Face", (r"\bhugging\s*face\b",)),
    ("perplexity", "Perplexity", (r"\bperplexity\b",)),
    ("cohere", "Cohere", (r"\bcohere\b",)),
    ("figure", "Figure", (r"\bfigure\b",)),
    ("tesla", "Tesla", (r"\btesla\b", r"تسلا")),
    ("tsmc", "TSMC", (r"\btsmc\b",)),
    ("samsung", "Samsung", (r"\bsamsung\b",)),
    ("baidu", "Baidu", (r"\bbaidu\b",)),
    ("deepmind", "DeepMind", (r"\bdeepmind\b",)),
]

_COMPILED = [
    (key, label, tuple(re.compile(p, re.I) for p in patterns))
    for key, label, patterns in ENTITIES
]


def _parse(raw):
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", ""))
    except (TypeError, ValueError):
        return None


def handle() -> str:
    raw = (CHANNEL_HANDLE or "").strip() or str(CHANNEL_ID or "").strip()
    if raw.startswith("https://t.me/"):
        raw = raw[len("https://t.me/"):]
    raw = raw.lstrip("@").strip("/")
    return raw if re.fullmatch(r"[A-Za-z0-9_]{4,64}", raw or "") else ""


def post_link(message_id) -> str:
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return ""
    h = handle()
    return f"https://t.me/{h}/{mid}" if h and mid > 0 else ""


def extract(item) -> list:
    """کلید موجودیت‌های موجود در یک خبر."""
    if not isinstance(item, dict):
        return []
    text = " ".join(str(item.get(k) or "") for k in
                    ("title", "summary", "title_fa", "summary_fa"))
    found = []
    for key, _label, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            found.append(key)
    return found[:8]


def load_index() -> dict:
    data = cloudflare_kv.get(KEY, {})
    return data if isinstance(data, dict) else {}


def enrich(items, now=None) -> int:
    """به خبرهای مرتبط، لینک ۱–۲ پست قبلی همان موضوع را اضافه می‌کند."""
    if not STORYLINES_ENABLED or not items or not handle():
        return 0
    now = now or datetime.utcnow()
    index = load_index()
    cutoff = now - timedelta(days=max(1, STORY_WINDOW_DAYS))
    linked = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        entities = extract(item)
        item["_story_entities"] = entities
        if not entities:
            continue
        item_id = item.get("id") or item.get("hash") or ""
        seen, candidates = set(), []
        for ent in entities:
            for row in index.get(ent) or []:
                if not isinstance(row, dict):
                    continue
                if item_id and row.get("id") == item_id:
                    continue
                link = post_link(row.get("message_id"))
                if not link or link in seen:
                    continue
                when = _parse(row.get("published_at"))
                if when and when < cutoff:
                    continue
                seen.add(link)
                candidates.append({
                    "title": clip(row.get("title") or "", 80),
                    "link": link,
                    "at": when or datetime.min,
                })
        if not candidates:
            continue
        candidates.sort(key=lambda c: c["at"], reverse=True)
        item["background"] = candidates[:max(1, STORY_LINKS_MAX)]
        linked += 1

    if linked:
        print(f"[STORY] added background to {linked} item(s)")
    return linked


def record(item, message_id, now=None) -> bool:
    """بعد از انتشار موفق: خبر را در تایم‌لاین موجودیت‌هایش ثبت کن."""
    if not STORYLINES_ENABLED or DRY_RUN or not message_id:
        return False
    if not isinstance(item, dict):
        return False
    entities = item.get("_story_entities") or extract(item)
    if not entities:
        return False
    now = now or datetime.utcnow()
    row = {
        "id": item.get("id") or item.get("hash") or "",
        "title": clip(item.get("title_fa") or item.get("title") or "", 120),
        "message_id": message_id,
        "published_at": now.isoformat() + "Z",
    }
    try:
        index = load_index()
        for ent in entities:
            rows = index.get(ent) if isinstance(index.get(ent), list) else []
            rows = [r for r in rows if isinstance(r, dict) and r.get("message_id") != message_id]
            rows.append(row)
            index[ent] = rows[-MAX_PER_ENTITY:]
        if len(index) > MAX_ENTITIES:
            order = sorted(
                index.items(),
                key=lambda kv: (_parse((kv[1] or [{}])[-1].get("published_at")) or datetime.min),
                reverse=True,
            )[:MAX_ENTITIES]
            index = dict(order)
        cloudflare_kv.put(KEY, index)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[STORY] record failed: {type(e).__name__}")
        return False
