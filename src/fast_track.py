"""
Fast-track processor — انتشار فوری بدون تایید
منابع فارسی: مستقیم فرمت + انتشار
منابع انگلیسی: ترجمه با src/translator.py (متن اصلی زیر ترجمه)
(فعلاً هیچ فیدی fast_track=True ندارد؛ فقط وقتی در collectors.py فعال شود کار می‌کند.)
"""
from datetime import datetime
from .config import CHANNEL_ID, BOT_TOKEN, MAX_POSTS_PER_RUN
from . import storage
from .classifier import is_ai_related
from .formatting import format_message
from .analyzer import localize

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


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

        item = localize({
            "id": item_id,
            "source": article["source"],
            "lang": article.get("lang", "en"),
            "title": article["title"],
            "summary": article.get("summary", ""),
            "link": article["link"],
            "hashtags": ["هوش_مصنوعی", "اخبار_AI"],
            "confidence": "confirmed",
            "importance": "medium",
            "category": "fast_track",
        })

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
