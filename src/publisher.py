import requests
from datetime import datetime, date
from .config import BOT_TOKEN, CHANNEL_ID, MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY
from . import storage
from .bot import send_message
from . import bale
from .formatting import format_message  # noqa: F401  (re-export برای سازگاری)

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

    # سقف امنیتی روزانه (اختیاری — پیش‌فرض خاموش)
    if MAX_POSTS_PER_DAY and MAX_POSTS_PER_DAY > 0:
        today_published = sum(
            1 for p in published_log
            if p.get("published") and str(p.get("published_at", ""))[:10] == today
        )
        if today_published >= MAX_POSTS_PER_DAY:
            print(f"[INFO] Daily safety cap reached ({MAX_POSTS_PER_DAY}) — keeping in queue")
            storage.save_approved(approved)
            return state

    # ترتیب انتشار: ویژه ← خبرهای خودکار ← تاییدی‌های ادمین (سپس اهمیت)
    # یعنی اول ۱۵ خودکار می‌رود و هر جا بجای خالی ماند، تاییدی ادمین پر می‌کند
    importance_order = {"high": 0, "medium": 1, "low": 2}
    approved.sort(key=lambda x: (
        0 if x.get("admin_action") == "featured" else 1,
        0 if x.get("admin_action") == "auto_approved" else 1,
        importance_order.get(x.get("importance", "medium"), 2)
    ))

    bale.reset()
    to_mirror = []
    remaining = []
    for item in approved:
        key = _item_key(item)

        if key and key in published_keys:
            print(f"[INFO] Already published, skipping: {key}")
            continue

        if state["daily_post_count"] >= MAX_POSTS_PER_RUN:
            print(f"[INFO] Run limit reached ({MAX_POSTS_PER_RUN}) — keeping in queue")
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
            to_mirror.append(dict(item))
            if key:
                published_keys.add(key)
            print(f"[INFO] Published: {str(item.get('title_fa', ''))[:50]}")
        else:
            print(f"[ERROR] Failed to publish ({key}): {result}")
            remaining.append(item)

    storage.save_approved(remaining)
    storage.save_published(published_log)

    # آینهٔ بله فقط بعد از ذخیرهٔ کامل وضعیت تلگرام؛ شکست = حذف، بدون صف
    for it in to_mirror:
        bale.mirror(it)
    return state
