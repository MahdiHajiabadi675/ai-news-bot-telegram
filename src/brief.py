"""
«صبحانهٔ AI» — خلاصهٔ کوتاه صبح، هر روز یک‌بار.

چرا؟ شب کانال پر از پست است و صبح ساکت. یک پست کوتاه ۸ صبح (۵ تیتر + لینک)
عادت روزانه می‌سازد، بدون هزینهٔ مدل و بدون تولید محتوای تازه: فقط از
`published` همان ۲۴ ساعت گذشته انتخاب می‌کند.

قواعد:
  • یک‌بار در روز، ساعت BRIEF_HOUR (پیش‌فرض ۸ به وقت ایران).
  • فقط خبر واقعی (نه آموزشی/دایجست/مقاله) در ۲۶ ساعت اخیر.
  • کمتر از ۳ خبر → پست نمی‌شود.
  • شناسهٔ روز در `brief_state.last_date` ذخیره می‌شود تا تکرار نشود.
"""
from datetime import datetime, timedelta

from . import bale, cloudflare_kv, storage
from .bot import send_message
from .config import BRIEF_ENABLED, BRIEF_HOUR, BRIEF_ITEMS, CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN
from .formatting import clip, escape_html, safe_link

KEY = "brief_state"
SKIP_CATEGORIES = ("edu", "digest", "paper", "brief")
IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
MIN_ITEMS = 3
TG_LIMIT = 3900


def local_now(now=None):
    return (now or datetime.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def _published_at(item):
    raw = str(item.get("published_at") or "").replace("Z", "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def recent_news(log, now_utc, hours: int = 26):
    cutoff = now_utc - timedelta(hours=hours)
    out = []
    for p in log or []:
        if not isinstance(p, dict) or not p.get("published"):
            continue
        if p.get("deleted") or p.get("category") in SKIP_CATEGORIES:
            continue
        if _published_at(p) < cutoff:
            continue
        out.append(p)
    return out


def pick(items, limit: int = BRIEF_ITEMS):
    return sorted(items, key=lambda p: (
        IMPORTANCE_ORDER.get(p.get("importance"), 2),
        -int(p.get("score") or 0),
        -_published_at(p).timestamp(),
    ))[:limit]


def format_brief(items, limit: int = TG_LIMIT) -> str:
    e = escape_html
    lines = ["☀️ <b>صبحانهٔ AI | چند خبری که باید بدانی</b>", "─" * 20, ""]
    for i, p in enumerate(items, 1):
        title = e(clip(p.get("title_fa") or p.get("title"), 90))
        link = safe_link(p.get("link"))
        lines.append(f'{i}. <a href="{link}">{title}</a>' if link else f"{i}. {title}")
        source = e(clip(p.get("source"), 40))
        if source:
            lines.append(f"   <i>{source}</i>")
    lines += [
        "",
        "─" * 20,
        "☕️ بقیهٔ خبرها در طول روز همین‌جا منتشر می‌شود.",
        "🏷️ #صبحانه_AI",
    ]
    text = "\n".join(lines)
    if len(text) > limit and len(items) > 1:
        return format_brief(items[:-1], limit)
    return text


def format_plain(items) -> str:
    lines = ["☀️ صبحانهٔ AI | چند خبری که باید بدانی", ""]
    for i, p in enumerate(items, 1):
        lines.append(f"{i}. {clip(p.get('title_fa') or p.get('title'), 120)}")
        if p.get("link"):
            lines.append(f"   {p['link']}")
        if p.get("source"):
            lines.append(f"   {p['source']}")
    lines += ["", "بقیهٔ خبرها در طول روز در کانال منتشر می‌شود.", "#صبحانه_AI"]
    return "\n".join(lines)[:4000]


def _log_brief(day: str, message_id=None):
    entry = {
        "id": f"brief-{day}",
        "title_fa": "صبحانهٔ AI",
        "source": "صبحانه",
        "category": "brief",
        "published": True,
        "published_at": datetime.utcnow().isoformat() + "Z",
    }
    if message_id:
        entry["message_id"] = message_id
    log = storage.load_published()
    log.append(entry)
    storage.save_published(log)


def maybe_post(state: dict, now=None, force: bool = False) -> dict:
    """اگر ساعت صبحانه است و امروز نرفته، بفرست."""
    try:
        if not BRIEF_ENABLED or not CHANNEL_ID or state.get("paused"):
            return state
        if DRY_RUN:
            print("[DRY] brief skipped (dry-run)")
            return state

        now_utc = now or datetime.utcnow()
        loc = local_now(now_utc)
        # پنجرهٔ ۳ ساعته تا تأخیر چندساعتهٔ GitHub Actions صبحانه را از دست ندهد،
        # ولی پست تکراری/عصرگاهی هم نسازد.
        if not force and loc.hour not in (BRIEF_HOUR, (BRIEF_HOUR + 1) % 24, (BRIEF_HOUR + 2) % 24):
            return state

        st = cloudflare_kv.get(KEY, {})
        st = st if isinstance(st, dict) else {}
        today = loc.date().isoformat()
        if st.get("last_date") == today:
            return state

        items = pick(recent_news(storage.load_published(), now_utc))
        if len(items) < MIN_ITEMS:
            print(f"[BRIEF] not enough news ({len(items)}/{MIN_ITEMS}) — skipped")
            return state

        res = send_message(CHANNEL_ID, format_brief(items))
        if not (res and res.get("ok")):
            print(f"[BRIEF] send failed: {res}")
            return state

        mid = (res.get("result") or {}).get("message_id")
        st.update({"last_date": today, "at": now_utc.isoformat() + "Z", "count": len(items)})
        cloudflare_kv.put(KEY, st)
        _log_brief(today, mid)
        print(f"[BRIEF] published {len(items)} items for {today}")
        bale.reset()
        bale.send_text(format_plain(items))
    except Exception as e:  # noqa: BLE001
        print(f"[BRIEF] error: {type(e).__name__}: {e}")
    return state
