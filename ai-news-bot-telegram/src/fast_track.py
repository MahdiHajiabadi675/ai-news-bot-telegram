"""
Fast-track processor — بدون هیچ درخواستی به Gemini
منابع فارسی: مستقیم فرمت + انتشار
منابع انگلیسی: ترجمه ساده با Google Translate
"""
from datetime import datetime
from .config import (
    AI_KEYWORDS, CHANNEL_ID, BOT_TOKEN, MAX_POSTS_PER_RUN
)
from . import storage
from .fallback_translator import translate_with_fallback

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def is_ai_related(text: str) -> bool:
    text_lower = text.lower()
    for kw in AI_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def is_english(text: str) -> bool:
    """آیا متن انگلیسیه؟ (بیش از ۵۰٪ حروف لاتین)"""
    if not text:
        return False
    latin_count = sum(1 for c in text if c.isascii() and c.isalpha())
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return False
    return (latin_count / total_alpha) > 0.5


def process_fast_track(articles: list) -> list:
    if not articles:
        return []

    published = []
    for article in articles:
        combined = article["title"] + " " + article.get("summary", "")
        if not is_ai_related(combined):
            print(f"[FAST] Skipped (not AI): {article['title'][:60]}")
            continue

        state = storage.load_state()
        today = datetime.utcnow().date().isoformat()
        if state.get("daily_post_date") != today:
            state["daily_post_date"] = today
            state["daily_post_count"] = 0
        if state["daily_post_count"] >= MAX_POSTS_PER_RUN:
            print(f"[FAST] Run limit reached ({MAX_POSTS_PER_RUN})")
            break

        item_id = article.get("id", article.get("hash", ""))

        # عنوان فارسی → مستقیم، عنوان انگلیسی → ترجمه ساده
        if article["lang"] == "fa" and not is_english(article["title"]):
            item = {
                "title_fa": article["title"],
                "summary_fa": article["summary"][:400],
                "hashtags": ["هوش_مصنوعی", "اخبار_tech"],
                "confidence": "confirmed",
                "importance": "medium",
                "category": "fast_track",
                "source": article["source"],
                "link": article["link"],
                "id": item_id,
            }
        else:
            translated = translate_with_fallback(article["title"], article.get("summary", ""))
            if translated is None:
                print(f"[FAST] Skipped (translation failed): {article['title'][:60]}")
                continue
            item = {
                "title_fa": translated.get("title_fa", article["title"]),
                "summary_fa": translated.get("summary_fa", article["summary"][:400]),
                "hashtags": ["AI", "اخبار_مصنوعی"],
                "confidence": "confirmed",
                "importance": "medium",
                "category": "fast_track",
                "source": article["source"],
                "link": article["link"],
                "id": item_id,
            }

        text = format_message(item)
        result = _send(text)

        if result and result.get("ok"):
            state["daily_post_count"] += 1
            storage.save_state(state)
            item["status"] = "published"
            item["published_at"] = datetime.utcnow().isoformat() + "Z"
            published.append(item)
            print(f"[FAST] Published: {item['title_fa'][:50]}")
        else:
            print(f"[FAST] Failed: {result}")

    if published:
        log = storage.load_published()
        for item in published:
            log.append({**item, "published": True})
        storage.save_published(log)

    return published


def format_message(item):
    title = item["title_fa"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    summary = item["summary_fa"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    source = item["source"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    hashtags = " ".join(f"#{h}" for h in item["hashtags"])

    text = f"📡 <b>{title}</b>\n\n"
    text += f"{summary}\n\n"
    text += f"📰 منبع: <a href=\"{item['link']}\">{source}</a>\n"
    text += f"🏷️ {hashtags}"
    return text


def _send(text):
    import requests
    try:
        resp = requests.post(f"{BASE}/sendMessage", json={
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"[ERROR] send: {e}")
        return None
