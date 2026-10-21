import requests
from datetime import datetime, date
from .config import BOT_TOKEN, CHANNEL_ID, MAX_POSTS_PER_DAY
from . import storage
from .bot import send_message

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _item_key(item):
    """کلید یکتای خبر — سازگار با id جدید و hash قدیمی"""
    return item.get("id") or item.get("hash") or ""


def publish_approved(state, approved_items=None):
    if state.get("paused"):
        print("[INFO] Bot is paused, skipping publish")
        return state

    today = date.today().isoformat()
    if state.get("daily_post_date") != today:
        state["daily_post_date"] = today
        state["daily_post_count"] = 0

    # اگه لیست آماده در حافظه هست، همون رو استفاده کن (جلوگیری از خواندن کهنه KV)
    approved = list(approved_items) if approved_items is not None else storage.load_approved()
    if not approved:
        return state

    published_log = storage.load_published()
    # ضدتکرار: هیچ خبری دو بار در کانال منتشر نمی‌شود
    published_keys = {_item_key(p) for p in published_log if p.get("published")}

    # Sort by score (highest first)
    importance_order = {"high": 0, "medium": 1, "low": 2}
    approved.sort(key=lambda x: (
        0 if x.get("admin_action") == "featured" else 1,
        importance_order.get(x.get("importance", "medium"), 2)
    ))

    remaining = []
    for item in approved:
        key = _item_key(item)

        if key and key in published_keys:
            print(f"[INFO] Already published, skipping: {key}")
            continue

        if state["daily_post_count"] >= MAX_POSTS_PER_DAY:
            print(f"[INFO] Daily limit reached ({MAX_POSTS_PER_DAY})")
            remaining.append(item)
            continue

        try:
            text = format_message(item)
        except Exception as e:
            # یک آیتم خراب نباید کل انتشار را متوقف کند
            print(f"[ERROR] format_message failed ({key}): {e}")
            continue

        result = send_message(CHANNEL_ID, text)

        if result and result.get("ok"):
            state["daily_post_count"] += 1
            item["status"] = "published"
            item["published_at"] = datetime.utcnow().isoformat() + "Z"
            published_log.append({**item, "published": True})
            if key:
                published_keys.add(key)
            print(f"[INFO] Published: {str(item.get('title_fa', ''))[:50]}")
        else:
            print(f"[ERROR] Failed to publish ({key}): {result}")
            remaining.append(item)

    storage.save_approved(remaining)
    storage.save_published(published_log)
    return state

def format_message(item):
    title = _escape_html(item.get("title_fa") or item.get("title") or "")
    summary = _escape_html(item.get("summary_fa") or "")
    link = _escape_html(item.get("link") or "")
    source = _escape_html(item.get("source") or "")
    hashtags = " ".join(f"#{_escape_html(h)}" for h in (item.get("hashtags") or []))

    # Creative formatting based on category and importance
    if item.get("category") == "fast_track":
        emoji = "📡"
        divider = "─" * 20
    elif item.get("admin_action") == "featured":
        emoji = "⭐️"
        divider = "═" * 20
    elif item.get("importance") == "high":
        emoji = "🔥"
        divider = "─" * 20
    elif item.get("confidence") == "rumor":
        emoji = "⚠️"
        divider = "─" * 20
    else:
        emoji = "🤖"
        divider = "─" * 20

    text = f"{emoji} <b>{title}</b>\n"
    text += f"{divider}\n\n"
    text += f"{summary}\n\n"
    text += f"{divider}\n"
    text += f"📰 منبع: <a href=\"{link}\">{source}</a>"
    if hashtags:
        text += f"\n🏷️ {hashtags}"

    if item.get("confidence") == "rumor":
        text += f"\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>"

    return text

def _escape_html(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
