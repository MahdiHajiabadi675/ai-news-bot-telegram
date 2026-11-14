"""
«مقالهٔ روز» arXiv — یک مقالهٔ تازهٔ cs.AI با خلاصهٔ فارسی، روزی یک‌بار.

چرا؟ کانال فقط خبر می‌دهد؛ یک مقالهٔ تازه در روز، محتوای متفاوت و ارزشمند اضافه
می‌کند و هزینه‌اش صفر است (فید عمومی arXiv + زنجیرهٔ مترجم موجود).

قواعد:
  • یک‌بار در روز، از ساعت `ARXIV_HOUR` (پیش‌فرض ۱۳ به وقت ایران) به بعد.
  • جدیدترین مقالات cs.AI خوانده می‌شود و اولین مقاله‌ای که قبلاً پست نشده انتخاب می‌شود.
  • عنوان و چکیده با `src/translator.py` ترجمه می‌شوند؛ اگر ترجمه شکست خورد،
    متن انگلیسی منتشر می‌شود (کانال نمی‌خوابد).
  • شناسه‌ها در `arxiv_state.used` نگه داشته می‌شوند تا تکرار نشود.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

from . import bale, cloudflare_kv, storage, translator
from .bot import send_message
from .config import ARXIV_ENABLED, ARXIV_HOUR, CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN
from .formatting import clip, escape_html

KEY = "arxiv_state"
FEED_URL = "http://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}
TIMEOUT = 12
MAX_RESULTS = 10
MAX_ABSTRACT = 700
TAG = "#مقاله_روز #هوش_مصنوعی"


def local_now(now=None):
    return (now or datetime.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def _text(entry, tag):
    return " ".join((entry.findtext(f"a:{tag}", default="", namespaces=NS) or "").split())


def fetch_entries(max_results=MAX_RESULTS):
    """جدیدترین مقالات cs.AI از فید رسمی arXiv"""
    resp = requests.get(
        FEED_URL,
        params={
            "search_query": "cat:cs.AI",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    out = []
    for entry in root.findall("a:entry", NS):
        link = _text(entry, "id")
        title = _text(entry, "title")
        if not link or not title:
            continue
        out.append({
            "id": link.rsplit("/", 1)[-1],
            "title": title,
            "summary": _text(entry, "summary"),
            "link": link,
            "published": _text(entry, "published"),
        })
    return out


def pick(entries, used=()):
    """اولین مقاله‌ای که قبلاً پست نشده"""
    used = set(used or ())
    for entry in entries:
        if entry["id"] not in used:
            return entry
    return entries[0] if entries else None


def _translate(text, fallback):
    try:
        out = translator.translate(text, "en")
        return out if isinstance(out, str) and out.strip() else fallback
    except Exception as e:  # noqa: BLE001
        print(f"[ARXIV] translate failed: {type(e).__name__}")
        return fallback


def format_post(entry, title_fa, summary_fa, plain=False) -> str:
    if plain:
        return (f"📄 مقالهٔ روز | {clip(title_fa, 120)}\n\n{clip(summary_fa, MAX_ABSTRACT)}\n\n"
                f"🔗 {entry['link']}\n\n{TAG}")[:4000]
    e = escape_html
    text = (f"📄 <b>مقالهٔ روز | {e(clip(title_fa, 120))}</b>\n{'─' * 20}\n\n"
            f"{e(clip(summary_fa, MAX_ABSTRACT))}\n\n"
            f'🔗 <a href="{e(entry["link"])}">arXiv:{e(entry["id"])}</a>\n\n🏷️ {TAG}')
    return text[:4000]


def maybe_post(state: dict, now=None) -> dict:
    """اگر امروز مقاله پست نشده و ساعت رسیده، جدیدترین مقالهٔ ترجمه‌شده را بفرست."""
    try:
        if not ARXIV_ENABLED or not CHANNEL_ID or state.get("paused"):
            return state
        if DRY_RUN:
            print("[DRY] arxiv skipped (dry-run)")
            return state

        now_utc = now or datetime.utcnow()
        loc = local_now(now_utc)
        today = loc.date().isoformat()

        st = cloudflare_kv.get(KEY, {})
        st = st if isinstance(st, dict) else {}
        if st.get("last_date") == today:
            return state
        if loc.hour < ARXIV_HOUR:
            return state  # هنوز به ساعت مقرر نرسیده‌ایم

        entries = fetch_entries()
        entry = pick(entries, st.get("used") or [])
        if not entry:
            print("[ARXIV] no entries")
            return state

        title_fa = _translate(entry["title"], entry["title"])
        summary_fa = _translate(clip(entry["summary"], 1200), entry["summary"])
        text = format_post(entry, title_fa, summary_fa)

        res = send_message(CHANNEL_ID, text)
        if not (res and res.get("ok")):
            print(f"[ARXIV] send failed: {res}")
            return state

        st.update({
            "last_date": today,
            "last_id": entry["id"],
            "at": now_utc.isoformat() + "Z",
            "used": (list(st.get("used") or []) + [entry["id"]])[-30:],
        })
        cloudflare_kv.put(KEY, st)

        try:
            mid = (res.get("result") or {}).get("message_id")
            log = storage.load_published()
            item = {"id": f"arxiv-{entry['id']}", "title_fa": clip(title_fa, 120),
                    "source": "arXiv", "category": "paper", "published": True,
                    "published_at": datetime.utcnow().isoformat() + "Z"}
            if mid:
                item["message_id"] = mid
            log.append(item)
            storage.save_published(log)
        except Exception as e:  # noqa: BLE001
            print(f"[ARXIV] log failed: {type(e).__name__}")

        print(f"[ARXIV] published {entry['id']}: {entry['title'][:60]}")
        bale.reset()
        bale.send_text(format_post(entry, title_fa, summary_fa, plain=True))
    except Exception as e:  # noqa: BLE001
        print(f"[ARXIV] error: {type(e).__name__}: {e}")
    return state
