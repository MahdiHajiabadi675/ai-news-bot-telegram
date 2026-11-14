"""
دایجست هفتگی — جمع‌بندی ۷ خبر مهم هفته، جمعه شب.

چرا؟ کانال هر ساعت خبر می‌دهد و مخاطبی که تازه عضو شده، گذشته را نمی‌بیند.
یک پست جمع‌بندی هفتگی هم بازگشت مخاطب را بالا می‌برد و هم آرشیو قابل‌اشتراک می‌سازد.

قواعد:
  • فقط جمعه (به وقت ایران) و در ساعت‌های پربازدید، حداکثر یک‌بار در هفته.
  • فقط خبرهای واقعی (نه پست آموزشی و نه خودِ دایجست)، ۷ روز اخیر.
  • اولویت: اهمیت، سپس امتیاز، سپس تازگی.
  • اگر کمتر از ۳ خبر باشد، پست نمی‌شود (جمع‌بندی بی‌محتوا ارزش ندارد).
  • شناسهٔ هفته در `digest_state.last_week` ذخیره می‌شود تا دوبار پست نشود.
"""
from datetime import datetime, timedelta

from . import bale, cloudflare_kv, storage
from .bot import pin_message, send_message
from .config import CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN
from .formatting import clip, escape_html, safe_link

KEY = "digest_state"
WEEKDAY = 4          # 0=دوشنبه … 4=جمعه
HOURS = (19, 20, 21, 22)
MAX_ITEMS = 7
MIN_ITEMS = 3
IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
SKIP_CATEGORIES = ("edu", "digest", "paper")
TG_LIMIT = 4000


def local_now(now=None):
    return (now or datetime.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def week_key(loc) -> str:
    iso = loc.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _published_at(item) -> datetime:
    raw = str(item.get("published_at") or "").replace("Z", "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def recent_items(log, now_utc, days: int = 7):
    """خبرهای واقعیِ منتشرشدهٔ چند روز اخیر"""
    cutoff = now_utc - timedelta(days=days)
    out = []
    for p in log:
        if not isinstance(p, dict) or not p.get("published"):
            continue
        if p.get("deleted") or p.get("category") in SKIP_CATEGORIES:
            continue
        if _published_at(p) < cutoff:
            continue
        out.append(p)
    return out


def _sort_key(p):
    return (
        IMPORTANCE_ORDER.get(p.get("importance"), 2),
        -int(p.get("score") or 0),
        -_published_at(p).timestamp(),
    )


def pick(items, limit: int = MAX_ITEMS):
    return sorted(items, key=_sort_key)[:limit]


def format_digest(items, limit: int = TG_LIMIT, quizzes=None) -> str:
    e = escape_html
    lines = ["🗞 <b>جمع‌بندی هفته | مهم‌ترین اخبار AI</b>", "─" * 20, ""]
    for i, p in enumerate(items, 1):
        title = e(clip(p.get("title_fa") or p.get("title"), 90))
        link = safe_link(p.get("link"))
        lines.append(f'{i}. <a href="{link}">{title}</a>' if link else f"{i}. {title}")
        source = e(clip(p.get("source"), 40))
        if source:
            lines.append(f"   <i>{source}</i>")
    if quizzes:
        lines += ["", "─" * 20, "🧠 <b>کوییزهای هفته (پاسخ‌ها)</b>"]
        for i, qz in enumerate(quizzes[:3], 1):
            question = e(clip(qz.get("question"), 70))
            options = qz.get("options") or []
            try:
                answer = str(options[int(qz.get("correct"))])
            except (IndexError, TypeError, ValueError):
                answer = ""
            lines.append(f"{i}. {question}")
            if answer:
                lines.append(f"   ✅ {e(clip(answer, 60))}")
    lines += ["", "─" * 20, "📣 این جمع‌بندی را برای دوستت فوروارد کن", "🏷️ #جمع‌بندی_هفته"]
    text = "\n".join(lines)
    if len(text) > limit:  # دفاع آخر: از انتها کم کن (تگ‌ها سالم می‌مانند)
        text = format_digest(items[:-1], limit, quizzes) if len(items) > 1 else text[:limit]
    return text


def format_plain(items, quizzes=None) -> str:
    lines = ["🗞 جمع‌بندی هفته | مهم‌ترین اخبار AI", ""]
    for i, p in enumerate(items, 1):
        title = clip(p.get("title_fa") or p.get("title"), 120)
        lines.append(f"{i}. {title}")
        if p.get("link"):
            lines.append(f"   {p['link']}")
        if p.get("source"):
            lines.append(f"   {p['source']}")
    if quizzes:
        lines += ["", "🧠 کوییزهای هفته (پاسخ‌ها)"]
        for i, qz in enumerate(quizzes[:3], 1):
            options = qz.get("options") or []
            try:
                answer = str(options[int(qz.get("correct"))])
            except (IndexError, TypeError, ValueError):
                answer = ""
            lines.append(f"{i}. {clip(qz.get('question'), 70)}")
            if answer:
                lines.append(f"   ✅ {clip(answer, 60)}")
    lines += ["", "📣 این جمع‌بندی را برای دوستت فوروارد کن", "#جمع‌بندی_هفته"]
    return "\n".join(lines)[:4000]


def _recent_quizzes(log, now_utc, days: int = 7, limit: int = 3):
    cutoff = now_utc - timedelta(days=days)
    out = []
    for p in log:
        if not isinstance(p, dict) or not p.get("quiz") or p.get("deleted"):
            continue
        if _published_at(p) < cutoff:
            continue
        out.append(p["quiz"])
    return out[-limit:][::-1]


def _log_digest(week: str, message_id=None):
    entry = {
        "id": f"digest-{week}",
        "title_fa": "جمع‌بندی هفته",
        "source": "جمع‌بندی",
        "category": "digest",
        "published": True,
        "published_at": datetime.utcnow().isoformat() + "Z",
    }
    if message_id:
        entry["message_id"] = message_id
    log = storage.load_published()
    log.append(entry)
    storage.save_published(log)


def maybe_post(state: dict, now=None, force: bool = False) -> dict:
    """اگر جمعهٔ ساعت پربازدید است و این هفته دایجست نرفته، بفرست."""
    try:
        if not CHANNEL_ID or state.get("paused"):
            return state
        if DRY_RUN:
            print("[DRY] digest skipped (dry-run)")
            return state

        now_utc = now or datetime.utcnow()
        loc = local_now(now_utc)
        if not force and (loc.weekday() != WEEKDAY or loc.hour not in HOURS):
            return state

        st = cloudflare_kv.get(KEY, {})
        st = st if isinstance(st, dict) else {}
        week = week_key(loc)
        if st.get("last_week") == week:
            return state

        items = pick(recent_items(storage.load_published(), now_utc))
        if len(items) < MIN_ITEMS:
            print(f"[DIGEST] not enough news ({len(items)}/{MIN_ITEMS}) — skipped")
            return state

        quizzes = _recent_quizzes(storage.load_published(), now_utc)
        text = format_digest(items, quizzes=quizzes)
        res = send_message(CHANNEL_ID, text)
        if not (res and res.get("ok")):
            print(f"[DIGEST] send failed: {res}")
            return state

        mid = (res.get("result") or {}).get("message_id")
        st.update({"last_week": week, "at": now_utc.isoformat() + "Z", "count": len(items)})
        cloudflare_kv.put(KEY, st)
        _log_digest(week, mid)
        if mid:
            try:
                pin_message(mid)
                print("[DIGEST] pinned the weekly digest")
            except Exception as e:  # noqa: BLE001
                print(f"[DIGEST] pin failed: {type(e).__name__}")

        print(f"[DIGEST] published {len(items)} items for {week}")
        bale.reset()
        bale.send_text(format_plain(items, quizzes))
    except Exception as e:  # noqa: BLE001
        print(f"[DIGEST] error: {type(e).__name__}: {e}")
    return state
