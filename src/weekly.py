"""
جمع‌بندی هفتگی + نظرسنجی + اعمال خودکار نتیجه.

چرخهٔ کامل (بدون مدل و بدون هزینه):
  ۱) شنبه شب: ۳ خبر پرمحبوبیت هفته + نوار روند دسته‌ها در کانال.
  ۲) همان پست: نظرسنجی «هفتهٔ بعد روی چه موضوعی تمرکز کنم؟».
  ۳) بعد از POLL_CLOSE_HOURS: نظرسنجی بسته و برندهٔ آن ذخیره می‌شود.
  ۴) تا POLL_BOOST_DAYS روز:
       • خبرهای همان دسته در امتیازدهی بوست می‌گیرند،
       • «صبحانهٔ AI» همان دسته را اول می‌گذارد.

یعنی نتیجهٔ نظرسنجی به‌صورت قطعی روی «نوع پست بعدی» اثر می‌گذارد، نه
اینکه یک مدل بخواهد حدس بزند.
"""
from . import clock
from datetime import datetime, timedelta

from . import bale, cloudflare_kv, storage, views
from . import bot
from .config import (
    CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN, POLL_BOOST, POLL_BOOST_DAYS,
    POLL_CLOSE_HOURS, POLL_MIN_VOTES, WEEKLY_DAY, WEEKLY_ENABLED, WEEKLY_HOUR,
    WEEKLY_ITEMS,
)
from .formatting import clip, escape_html, safe_link

KEY = "weekly_state"
WEEKLY_CATEGORY = "weekly"
SKIP_CATEGORIES = ("edu", "digest", "paper", "brief", "weekly")

CATEGORY_LABELS = {
    "model_release": "انتشار مدل جدید",
    "benchmark": "بنچمارک و ارزیابی",
    "funding": "سرمایه‌گذاری و بازار",
    "research": "پژوهش و مقاله",
    "policy": "قانون و مقررات AI",
    "tool_release": "ابزار و محصول",
    "industry": "صنعت و شرکت‌ها",
    "other": "موضوع‌های عمومی",
}
FALLBACK_OPTIONS = ("model_release", "tool_release", "research", "funding")


def _iso(now=None) -> str:
    return (now or clock.utcnow()).isoformat() + "Z"


def _parse(raw):
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", ""))
    except (TypeError, ValueError):
        return None


def local_now(now=None):
    return (now or clock.utcnow()) + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def load_state() -> dict:
    st = cloudflare_kv.get(KEY, {})
    return st if isinstance(st, dict) else {}


def save_state(st: dict):
    cloudflare_kv.put(KEY, st if isinstance(st, dict) else {})


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(str(category or ""), str(category or "موضوع دیگر"))


def _post_link(item) -> str:
    return views.channel_post_link(item.get("message_id")) or safe_link(item.get("link"))


def _fmt(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _week_log(log, now, days: int = 7) -> list:
    cutoff = now - timedelta(days=max(1, days))
    rows = []
    for p in log or []:
        if not isinstance(p, dict) or not p.get("published") or p.get("deleted"):
            continue
        if p.get("category") in SKIP_CATEGORIES:
            continue
        when = _parse(p.get("published_at"))
        if when and when >= cutoff:
            rows.append(p)
    return rows


def category_counts(log, now, days: int = 7) -> list:
    """[(category, count)] مرتب بر اساس تعداد خبر هفته."""
    counts = {}
    for p in _week_log(log, now, days):
        cat = str(p.get("category") or "other")
        counts[cat] = counts.get(cat, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def format_recap(items, log, now=None) -> str:
    """متن جمع‌بندی: ۳ خبر برتر + نوار روند دسته‌ها."""
    now = now or clock.utcnow()
    lines = ["🗓 <b>جمع‌بندی هفته در AI</b>", "─" * 20]
    medals = ("🥇", "🥈", "🥉")
    for i, p in enumerate(items, 0):
        title = escape_html(clip(p.get("title_fa") or p.get("title"), 90))
        link = _post_link(p)
        line = f'{medals[i] if i < len(medals) else "•"} '
        line += f'<a href="{link}">{title}</a>' if link else title
        reactions, emoji = views.reaction_info(p)
        stats = []
        if p.get("views"):
            stats.append(f"👁 {_fmt(p.get('views'))}")
        if reactions:
            stats.append(f"{emoji or '❤️'} {_fmt(reactions)}")
        if stats:
            line += " — " + " · ".join(stats)
        lines.append(line)

    counts = category_counts(log, now)[:5]
    if counts:
        top = max(c for _, c in counts) or 1
        lines += ["", "📊 <b>این هفته</b>"]
        for cat, count in counts:
            bar = "█" * max(1, round(count / top * 8))
            lines.append(f"{escape_html(_label(cat))} <code>{bar}</code> {count}")

    lines += [
        "",
        "─" * 20,
        "🗳 پایین رأی بده: هفتهٔ بعد روی چه موضوعی تمرکز کنم؟",
        "🏷️ #جمع‌بندی_هفتگی",
    ]
    return "\n".join(lines)[:3900]


def format_recap_plain(items, log, now=None) -> str:
    now = now or clock.utcnow()
    lines = ["جمع‌بندی هفته در AI", ""]
    for i, p in enumerate(items, 1):
        lines.append(f"{i}. {clip(p.get('title_fa') or p.get('title'), 100)}")
        if p.get("link"):
            lines.append(f"   {p['link']}")
    counts = category_counts(log, now)[:5]
    if counts:
        lines += ["", "این هفته:"]
        lines += [f"- {_label(c)}: {n} خبر" for c, n in counts]
    lines += ["", "هفتهٔ بعد روی چه موضوعی تمرکز کنم؟ در کانال رأی بده.", "#جمع‌بندی_هفتگی"]
    return "\n".join(lines)[:4000]


def _poll_options(log, now) -> list:
    """حداکثر ۴ گزینهٔ دسته‌محور برای نظرسنجی."""
    picked, seen = [], set()
    for cat, _count in category_counts(log, now):
        if cat in SKIP_CATEGORIES or cat in seen:
            continue
        seen.add(cat)
        picked.append({"category": cat, "text": _label(cat)[:100]})
        if len(picked) >= 4:
            break
    for cat in FALLBACK_OPTIONS:
        if len(picked) >= 4:
            break
        if cat not in seen:
            seen.add(cat)
            picked.append({"category": cat, "text": _label(cat)[:100]})
    return picked


def refresh(now=None) -> dict:
    """اگر نظرسنجی به پایان رسیده، ببند و برنده را ذخیره کن."""
    now = now or clock.utcnow()
    st = load_state()
    if DRY_RUN:
        return st
    poll = st.get("poll")
    if not isinstance(poll, dict) or poll.get("closed"):
        return st

    closes = _parse(poll.get("closes_at"))
    if not closes or now < closes:
        return st

    mid = poll.get("message_id")
    res = bot.stop_poll(mid) if mid else None
    if not (res and res.get("ok")):
        # یک بار دیگر در اجرای بعدی تلاش می‌شود؛ بعد از ۳ تلاش رهایش کن.
        poll["close_tries"] = int(poll.get("close_tries") or 0) + 1
        if poll["close_tries"] >= 3:
            poll["closed"] = True
            poll["error"] = "stop_failed"
        st["poll"] = poll
        save_state(st)
        return st

    final = ((res.get("result") or {}).get("poll") or {})
    final_options = final.get("options") or []
    stored = poll.get("options") or []
    winner_idx, winner_votes, total = -1, 0, 0
    for i, opt in enumerate(final_options):
        try:
            votes = int(opt.get("voter_count") or 0)
        except (TypeError, ValueError):
            votes = 0
        total += votes
        if votes > winner_votes:
            winner_idx, winner_votes = i, votes

    poll["closed"] = True
    poll["closed_at"] = _iso(now)
    poll["total_votes"] = total

    if winner_idx >= 0 and total >= max(1, POLL_MIN_VOTES) and winner_idx < len(stored):
        category = (stored[winner_idx] or {}).get("category") or ""
        if category:
            st["result"] = {
                "category": category,
                "label": _label(category),
                "votes": winner_votes,
                "total": total,
                "at": _iso(now),
                "expires_at": _iso(now + timedelta(days=max(1, POLL_BOOST_DAYS))),
            }
            print(f"[WEEKLY] poll winner: {category} ({winner_votes}/{total})")
    else:
        print(f"[WEEKLY] poll ignored (votes {total} < {POLL_MIN_VOTES} or no winner)")

    st["poll"] = poll
    save_state(st)
    return st


def result_active(now=None) -> dict:
    """نتیجهٔ فعال (منقضی‌نشده) یا {}."""
    res = load_state().get("result")
    if not isinstance(res, dict) or not res.get("category"):
        return {}
    now = now or clock.utcnow()
    expires = _parse(res.get("expires_at"))
    if expires and now >= expires:
        return {}
    return res


def winner_category(now=None) -> str:
    return str(result_active(now).get("category") or "")


def boost(items, now=None) -> int:
    """دستهٔ برندهٔ نظرسنجی را تا چند روز در امتیازدهی تقویت می‌کند."""
    winner = winner_category(now)
    if not winner or not items:
        return 0
    boosted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("category") or "other") != winner:
            continue
        item["hot"] = min(100, int(item.get("hot") or 0) + max(1, POLL_BOOST))
        reasons = item.get("hot_reasons")
        tag = f"انتخاب مخاطبان (+{POLL_BOOST})"
        if isinstance(reasons, list) and tag not in reasons:
            reasons.append(tag)
        item["poll_hit"] = winner
        boosted += 1
    if boosted:
        print(f"[WEEKLY] boosted {boosted} item(s) for poll winner: {winner}")
    return boosted


def may_post(state: dict, now=None, force: bool = False) -> bool:
    """آیا الان زمان جمع‌بندی هفتگی است؟ (بدون ارسال)"""
    if not WEEKLY_ENABLED or not CHANNEL_ID or (state or {}).get("paused"):
        return False
    loc = local_now(now)
    if force:
        return True
    return loc.weekday() == WEEKLY_DAY and loc.hour in (
        WEEKLY_HOUR, (WEEKLY_HOUR + 1) % 24, (WEEKLY_HOUR + 2) % 24,
    )


def maybe_post(state: dict, now=None, force: bool = False) -> dict:
    """جمع‌بندی + شروع نظرسنجی هفته، حداکثر یک‌بار در هفته."""
    try:
        if not may_post(state, now, force):
            return state
        now_utc = now or clock.utcnow()
        loc = local_now(now_utc)
        today = loc.date().isoformat()
        st = load_state()
        if st.get("last_date") == today and not force:
            return state

        log = storage.load_published()
        items = views.top_items(log, days=7, limit=WEEKLY_ITEMS)
        if len(items) < 2:
            print(f"[WEEKLY] not enough news ({len(items)}) — skipped")
            return state

        text = format_recap(items, log, now_utc)
        if DRY_RUN:
            print(f"[DRY] weekly recap preview:\n{text}")
            return state

        res = bot.send_message(CHANNEL_ID, text)
        if not (res and res.get("ok")):
            print(f"[WEEKLY] recap send failed: {res}")
            return state
        mid = (res.get("result") or {}).get("message_id")

        entry = {
            "id": f"weekly-{today}",
            "title_fa": "جمع‌بندی هفته در AI",
            "source": "جمع‌بندی",
            "category": WEEKLY_CATEGORY,
            "published": True,
            "published_at": _iso(now_utc),
        }
        if mid:
            entry["message_id"] = mid
        log.append(entry)
        storage.save_published(log)

        if mid:
            try:
                bot.pin_message(mid)
            except Exception as e:  # noqa: BLE001
                print(f"[WEEKLY] pin skipped: {type(e).__name__}")

        bale.reset()
        bale.send_text(format_recap_plain(items, log, now_utc))

        options = _poll_options(log, now_utc)
        poll_res = bot.send_regular_poll(
            CHANNEL_ID, "هفتهٔ بعد بیشتر روی چه موضوعی کار کنم؟",
            [o["text"] for o in options],
        )
        poll_mid = ((poll_res or {}).get("result") or {}).get("message_id")
        st.update({
            "last_date": today,
            "recap_message_id": mid,
            "at": _iso(now_utc),
            "poll": {
                "message_id": poll_mid,
                "options": options,
                "created_at": _iso(now_utc),
                "closes_at": _iso(now_utc + timedelta(hours=max(1, POLL_CLOSE_HOURS))),
                "closed": False,
            },
        })
        save_state(st)
        print(f"[WEEKLY] recap sent; poll {'started' if poll_mid else 'failed'} "
              f"({len(options)} options, closes in {POLL_CLOSE_HOURS}h)")
    except Exception as e:  # noqa: BLE001
        print(f"[WEEKLY] error: {type(e).__name__}: {e}")
    return state


def report(now=None) -> str:
    """وضعیت جمع‌بندی/نظرسنجی برای دستور /weekly."""
    now = now or clock.utcnow()
    st = load_state()
    lines = ["🗓 <b>جمع‌بندی هفتگی</b>"]
    if st.get("last_date"):
        lines.append(f"آخرین اجرا: {escape_html(st.get('last_date'))}")
    poll = st.get("poll") if isinstance(st.get("poll"), dict) else {}
    if poll:
        if poll.get("closed"):
            lines.append(f"نظرسنجی: بسته ✅ (کل رأی: {poll.get('total_votes', 0)})")
        else:
            lines.append(f"نظرسنجی: باز — بسته‌شدن: {escape_html(str(poll.get('closes_at'))[:16])}")
    else:
        lines.append("نظرسنجی: هنوز شروع نشده")
    res = result_active(now)
    if res:
        lines.append(
            f"🏆 انتخاب مخاطبان: <b>{escape_html(res.get('label'))}</b> "
            f"({res.get('votes', 0)}/{res.get('total', 0)} رأی)"
        )
        lines.append(f"⏳ اثر بوست تا: {escape_html(str(res.get('expires_at'))[:10])}")
    lines.append(f"\n<i>زمان‌بندی: روز {WEEKLY_DAY} ساعت {WEEKLY_HOUR} به وقت ایران</i>")
    return "\n".join(lines)
