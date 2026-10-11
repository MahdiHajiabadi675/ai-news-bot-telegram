"""
Fast-track processor — بدون Gemini، فقط ترجمه سریع + انتشار
منابع فارسی: مستقیم فرمت + انتشار
منابع انگلیسی: ترجمه سریع با Gemini (minimal prompt)
"""
import re
import requests
from datetime import datetime
from .config import (
    GEMINI_API_KEY, GEMINI_MODEL, AI_KEYWORDS,
    CHANNEL_ID, BOT_TOKEN, MAX_POSTS_PER_DAY, DATA_DIR
)
from . import storage

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def is_ai_related(text: str) -> bool:
    """بررسی سریع با کلمات کلیدی"""
    text_lower = text.lower()
    for kw in AI_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False

def quick_translate(title: str, summary: str) -> dict:
    """ترجمه سریع با Gemini (فقط عنوان + خلاصه کوتاه)"""
    prompt = f"""Translate to Persian. Keep it natural and concise.

Title: {title}
Summary: {summary[:500]}

Return JSON: {{"title_fa": "...", "summary_fa": "..."}}"""

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 512,
                    "responseMimeType": "application/json"
                }
            },
            timeout=30
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"[WARN] Quick translate failed: {e}")
        return {"title_fa": title, "summary_fa": summary[:300]}

def process_fast_track(articles: list) -> list:
    """پردازش fast-track: فیلتر AI + ترجمه + انتشار"""
    if not articles:
        return []

    published = []
    for article in articles:
        # فیلتر سریع: آیا مرتبط با AI هست؟
        combined = article["title"] + " " + article["summary"]
        if not is_ai_related(combined):
            print(f"[FAST] Skipped (not AI): {article['title'][:60]}")
            continue

        # بررسی سقف روزانه
        state = storage.load_state()
        today = datetime.utcnow().date().isoformat()
        if state.get("daily_post_date") != today:
            state["daily_post_date"] = today
            state["daily_post_count"] = 0
        if state["daily_post_count"] >= MAX_POSTS_PER_DAY:
            print(f"[FAST] Daily limit reached")
            break

        # ترجمه
        if article["lang"] == "fa":
            item = {
                "title_fa": article["title"],
                "summary_fa": article["summary"][:400],
                "hashtags": ["هوش_مصنوعی", "اخبار_tech"],
                "confidence": "confirmed",
                "importance": "medium",
                "category": "fast_track",
                "source": article["source"],
                "link": article["link"],
                "hash": article["hash"],
            }
        else:
            translated = quick_translate(article["title"], article["summary"])
            item = {
                "title_fa": translated.get("title_fa", article["title"]),
                "summary_fa": translated.get("summary_fa", article["summary"][:400]),
                "hashtags": ["AI", "اخبار_مصنوعی"],
                "confidence": "confirmed",
                "importance": "medium",
                "category": "fast_track",
                "source": article["source"],
                "link": article["link"],
                "hash": article["hash"],
            }

        # انتشار
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

    # لاگ
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
