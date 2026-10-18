"""
Fast-track processor — بدون Gemini، فقط ترجمه سریع + انتشار
منابع فارسی: مستقیم فرمت + انتشار (بدون Gemini)
منابع انگلیسی: ترجمه سریع با Gemini + fallback به Google Translate
"""
import re
import requests
from datetime import datetime
from .config import (
    GEMINI_API_KEY, GEMINI_MODEL, AI_KEYWORDS,
    CHANNEL_ID, BOT_TOKEN, MAX_POSTS_PER_DAY, DATA_DIR
)
from . import storage
from .fallback_translator import translate_with_fallback

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

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

def quick_translate(title: str, summary: str) -> dict:
    """ترجمه سریع — فقط یک درخواست به Gemini. اگه fail بشه None برمی‌گرده"""
    import json as json_mod
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Translate to Persian. Keep it natural and concise.

Title: {title}
Summary: {summary[:500]}

Return JSON: {{"title_fa": "...", "summary_fa": "..."}}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    try:
        resp = requests.post(
            url,
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
        result = json_mod.loads(text)
        if not result.get("title_fa") or not result.get("summary_fa"):
            print(f"[WARN] Empty translation result")
            return None
        print(f"[INFO] Quick translate succeeded")
        return result
    except Exception as e:
        print(f"[WARN] Quick translate failed: {e}")
        return None

def process_fast_track(articles: list) -> list:
    if not articles:
        return []

    published = []
    for article in articles:
        combined = article["title"] + " " + article["summary"]
        if not is_ai_related(combined):
            print(f"[FAST] Skipped (not AI): {article['title'][:60]}")
            continue

        state = storage.load_state()
        today = datetime.utcnow().date().isoformat()
        if state.get("daily_post_date") != today:
            state["daily_post_date"] = today
            state["daily_post_count"] = 0
        if state["daily_post_count"] >= MAX_POSTS_PER_DAY:
            print(f"[FAST] Daily limit reached")
            break

        # اگه عنوان انگلیسی باشه (حتی از منبع فارسی) → ترجمه کن
        if article["lang"] == "fa" and not is_english(article["title"]):
            # عنوان فارسیه → مستقیم استفاده کن
            item = {
                "title_fa": article["title"],
                "summary_fa": article["summary"][:400],
                "hashtags": ["هوش_مصنوعی", "اخبار_tech"],
                "confidence": "confirmed",
                "importance": "medium",
                "category": "fast_track",
                "source": article["source"],
                "link": article["link"],
                "id": article.get("id", article.get("hash", "")),
            }
        else:
            # عنوان انگلیسیه → ترجمه کن
            translated = quick_translate(article["title"], article["summary"])
            if translated is None:
                print(f"[FAST] Gemini failed, trying fallback...")
                translated = translate_with_fallback(article["title"], article["summary"])
            if translated is None:
                print(f"[FAST] Skipped (all translation failed): {article['title'][:60]}")
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
                "id": article.get("id", article.get("hash", "")),
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
