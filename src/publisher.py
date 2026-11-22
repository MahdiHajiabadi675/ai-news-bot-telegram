import re
import requests
from datetime import datetime, date, timedelta
from urllib.parse import quote

from .config import (
    BOT_TOKEN, CHANNEL_ID, ADMIN_ID, MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY,
    DISPLAY_TZ_OFFSET_MIN, PUBLISH_MIN_GAP_MIN, QUIET_HOURS, SHARE_BUTTON_ENABLED,
    TRANSLATE_RETRY_MAX,
)
from . import storage
from .bot import send_message, pin_message, edit_message_reply_markup
from . import bale
from . import bluesky
from . import storylines
from . import views
from .formatting import format_message, safe_link, clip  # noqa: F401  (re-export برای سازگاری)

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _item_key(item):
    """کلید یکتای خبر — سازگار با id جدید و hash قدیمی"""
    return item.get("id") or item.get("hash") or ""


def _local_now(now=None):
    return (now or datetime.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def _quiet_hours(now=None) -> bool:
    """آیا الان در بازهٔ سکوت است؟ قالب: "0-7" یا "23-7" (به وقت ایران)."""
    raw = str(QUIET_HOURS or "").strip()
    if not raw:
        return False
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})$", raw)
    if not m:
        return False
    start, end = int(m.group(1)) % 24, int(m.group(2)) % 24
    hour = _local_now(now).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _gap_ok(state, now=None) -> bool:
    """حداقل فاصلهٔ بین دو راند انتشار (داخل یک راند اعمال نمی‌شود)."""
    gap = max(0, int(PUBLISH_MIN_GAP_MIN or 0))
    if not gap:
        return True
    last = str((state or {}).get("last_publish_at") or "").replace("Z", "")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now or datetime.utcnow()) - last_dt >= timedelta(minutes=gap)


def _share_markup(item, message_id=None):
    """دکمهٔ «فرستادن به دوست» — لینک پست کانال، وگرنه لینک منبع."""
    if not SHARE_BUTTON_ENABLED:
        return None
    link = views.channel_post_link(message_id) or safe_link(item.get("link"))
    if not link:
        return None
    title = clip(item.get("title_fa") or item.get("title") or "خبر AI", 120)
    share = (
        "https://t.me/share/url?url=" + quote(link, safe="")
        + "&text=" + quote(title, safe="")
    )
    return {"inline_keyboard": [[{"text": "📤 فرستادن به دوست", "url": share}]]}


def publish_approved(state, approved_items=None):
    if state.get("paused"):
        print("[INFO] Bot is paused, skipping publish")
        return state

    now_utc = datetime.utcnow()
    if _quiet_hours(now_utc):
        print(f"[INFO] Quiet hours ({QUIET_HOURS}) — keeping queue for later")
        return state
    if not _gap_ok(state, now_utc):
        print(f"[INFO] Publish gap ({PUBLISH_MIN_GAP_MIN}m) not passed — keeping queue")
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
        0 if x.get("topic_hit") else 1,          # موضوعات مورد علاقهٔ ادمین
        0 if x.get("admin_action") == "auto_approved" else 1,
        importance_order.get(x.get("importance", "medium"), 2)
    ))

    bale.reset()
    to_mirror = []
    remaining = []
    dropped = []
    for item in approved:
        key = _item_key(item)

        if key and key in published_keys:
            print(f"[INFO] Already published, skipping: {key}")
            continue

        # ⏰ خبر زمان‌بندی‌شده تا رسیدن موعدش در صف می‌ماند
        sched = item.get("schedule_at")
        if sched:
            try:
                due = datetime.fromisoformat(str(sched).replace("Z", ""))
            except ValueError:
                due = None
            if due and due > datetime.utcnow():
                print(f"[INFO] Scheduled for {sched} — keeping in queue")
                remaining.append(item)
                continue

        if state["daily_post_count"] >= MAX_POSTS_PER_RUN:
            print(f"[INFO] Run limit reached ({MAX_POSTS_PER_RUN}) — keeping in queue")
            remaining.append(item)
            continue

        # 🔤 اگر ترجمه شکست خورده، یک راند دیگر صبر کن و دوباره تلاش کن
        if item.get("hold_translation"):
            tries = int(item.get("translate_tries") or 0)
            if tries < max(0, TRANSLATE_RETRY_MAX):
                try:
                    from .analyzer import localize
                    localize(item)
                except Exception as e:  # noqa: BLE001
                    print(f"[TRANSLATE] retry failed: {type(e).__name__}")
                item["translate_tries"] = tries + 1
                if item.get("translated") is not False:
                    item["hold_translation"] = False
                    print(f"[TRANSLATE] retry succeeded: {key}")
                elif item["translate_tries"] >= max(1, TRANSLATE_RETRY_MAX):
                    item["hold_translation"] = False
                    print(f"[TRANSLATE] giving up after {item['translate_tries']} retry: {key}")
                else:
                    # هنوز یک راند دیگر فرصت دارد؛ این دور منتشر نشود
                    remaining.append(item)
                    continue
            else:
                item["hold_translation"] = False

        try:
            text = format_message(item)
        except Exception as e:
            # یک آیتم خراب نباید کل انتشار را متوقف کند
            print(f"[ERROR] format_message failed ({key}): {e}")
            continue

        result = send_message(CHANNEL_ID, text)

        if result and result.get("ok"):
            state["daily_post_count"] += 1
            state["last_publish_at"] = datetime.utcnow().isoformat() + "Z"
            item["status"] = "published"
            item["published_at"] = datetime.utcnow().isoformat() + "Z"
            msg_id = (result.get("result") or {}).get("message_id")
            entry = {**item, "published": True}
            if msg_id:
                # برای /undo و /editlast لازم است
                entry["message_id"] = msg_id
                # 📤 دکمهٔ اشتراک‌گذاری با لینک همین پست کانال
                markup = _share_markup(item, msg_id)
                if markup:
                    try:
                        edit_message_reply_markup(msg_id, markup)
                    except Exception as e:  # noqa: BLE001
                        print(f"[WARN] share button failed: {type(e).__name__}")
                # 🧵 ثبت در تایم‌لاین موضوعی برای پست‌های بعدی
                try:
                    storylines.record(entry, msg_id)
                except Exception as e:  # noqa: BLE001
                    print(f"[WARN] storyline record failed: {type(e).__name__}")
            published_log.append(entry)
            to_mirror.append(dict(item))
            if key:
                published_keys.add(key)
            if item.get("pin") and msg_id:
                try:
                    pin_message(msg_id)
                    print(f"[INFO] Pinned: {key}")
                except Exception as e:  # noqa: BLE001
                    print(f"[WARN] pin failed: {e}")
            print(f"[INFO] Published: {str(item.get('title_fa', ''))[:50]}")
        else:
            err_code = (result or {}).get("error_code")
            print(f"[ERROR] Failed to publish ({key}): {result}")
            if err_code == 400:
                # پیام از نظر تلگرام نامعتبر است (HTML/URL)؛ با تکرار درست نمی‌شود.
                # اگر در صف بماند هر ساعت دوباره تلاش می‌شود و یک اسلات را می‌بلعد.
                print(f"[WARN] Dropping unpublishable item (400): {key}")
                dropped.append(item)
            else:
                remaining.append(item)

    storage.save_approved(remaining)
    storage.save_published(published_log)

    if dropped and ADMIN_ID:
        send_message(
            ADMIN_ID,
            f"⚠️ {len(dropped)} خبر به‌خاطر خطای قالب/لینک تلگرام (400) از صف حذف شد.\n"
            "اگر تکرار شد، لاگ Actions را ببین.",
        )

    # آینهٔ بله فقط بعد از ذخیرهٔ کامل وضعیت تلگرام؛ شکست = حذف، بدون صف
    for it in to_mirror:
        bale.mirror(it)
        try:
            bluesky.mirror(it)
        except Exception as e:  # noqa: BLE001
            print(f"[BSKY] mirror skipped: {type(e).__name__}")
    return state
