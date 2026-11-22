"""
بازدید پست‌ها از پیش‌نمایش عمومی کانال — رایگان و بدون API.

چرا؟ Bot API تلگرام تعداد بازدید نمی‌دهد. ولی صفحهٔ عمومی `t.me/s/<channel>`
برای هر پست `data-post="channel/123"` و `<span class="tgme_widget_message_views">`
دارد. با خواندن همان صفحه (بدون توکن و بدون سرویس پولی) می‌فهمیم کدام دستهٔ
خبر واقعاً خوانده می‌شود.

خروجی این ماژول سه چیز است:
  ۱) `sync()` — بازدیدها را به رکورد همان پیام در `published` می‌چسباند.
  ۲) `boost()` — به خبرهایی از دسته‌های پرخوان، کمی «جذابیت» اضافه می‌کند
     تا شانس انتشار خودکارشان بالاتر برود.
  ۳) `top_report()` — متن دستور `/top` برای ادمین.

همهٔ خطاها بلعیده می‌شوند؛ این ماژول هرگز نباید انتشار خبر را متوقف کند.
"""
import re
from datetime import datetime, timedelta
from statistics import mean

import requests

from . import cloudflare_kv, storage
from .config import (
    CHANNEL_HANDLE, CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, DRY_RUN, REACTIONS_BOOST_MAX,
    REACTIONS_ENABLED, REACTIONS_WEIGHT, VIEWS_BOOST_MAX, VIEWS_ENABLED,
    VIEWS_MIN_INTERVAL_MIN, VIEWS_PAGES,
)
from .formatting import clip, escape_html

KEY = "views_state"
REACTIONS_KEY = "reactions"
TIMEOUT = 12
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MSG_SPLIT = re.compile(r'(?=data-post="[^"/]+/\d+")')
_POST_RE = re.compile(r'data-post="[^"/]+/(\d+)"')
_VIEWS_RE = re.compile(r'tgme_widget_message_views[^>]*>([^<]+)<')
_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def channel_handle() -> str:
    """هندل عمومی کانال؛ اگر کانال با آیدی عددی تنظیم شده باشد، خالی است."""
    raw = (CHANNEL_HANDLE or "").strip() or str(CHANNEL_ID or "").strip()
    if raw.startswith("https://t.me/"):
        raw = raw[len("https://t.me/"):]
    raw = raw.lstrip("@").strip("/")
    return raw if re.fullmatch(r"[A-Za-z0-9_]{4,64}", raw or "") else ""


def channel_post_link(message_id) -> str:
    """لینک عمومی یک پست کانال؛ بدون هندل عمومی، رشتهٔ خالی."""
    handle = channel_handle()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return ""
    return f"https://t.me/{handle}/{mid}" if handle and mid > 0 else ""


def load_reactions() -> dict:
    """{message_id: {total, by, at}} — نوشتهٔ Worker یا خود پایپ‌لاین."""
    data = cloudflare_kv.get(REACTIONS_KEY, {})
    return data if isinstance(data, dict) else {}


def record_reactions(message_id, reactions, now=None) -> int:
    """جمع واکنش‌های یک پست را در KV ذخیره می‌کند. هرگز استثنا نمی‌دهد."""
    if not REACTIONS_ENABLED or DRY_RUN:
        return 0
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return 0
    totals, total = {}, 0
    for r in reactions or []:
        if not isinstance(r, dict):
            continue
        try:
            n = int(r.get("total_count") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            continue
        emoji = str((r.get("type") or {}).get("emoji") or "?")
        totals[emoji] = totals.get(emoji, 0) + n
        total += n
    if total <= 0:
        return 0
    try:
        data = load_reactions()
        data[str(mid)] = {
            "total": total,
            "by": totals,
            "at": (now or datetime.utcnow()).isoformat() + "Z",
        }
        if len(data) > 500:
            newest = sorted(
                data.items(),
                key=lambda kv: str((kv[1] or {}).get("at") or ""),
                reverse=True,
            )[:500]
            data = dict(newest)
        cloudflare_kv.put(REACTIONS_KEY, data)
    except Exception as e:  # noqa: BLE001
        print(f"[REACTIONS] save failed: {type(e).__name__}")
        return 0
    return total


def reaction_info(item, data=None) -> tuple:
    """(تعداد کل, ایموجی غالب) برای یک آیتم منتشرشده."""
    if not isinstance(item, dict):
        return 0, ""
    known = item.get("reactions") if isinstance(item.get("reactions"), dict) else {}
    try:
        total = int(item.get("reactions_total") or known.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    if item.get("message_id") is not None:
        data = load_reactions() if data is None else data
        row = data.get(str(item.get("message_id"))) or {}
        if isinstance(row, dict):
            total = max(total, int(row.get("total") or 0))
            known = row if row else known
    top = ""
    by = known.get("by") if isinstance(known, dict) else None
    if isinstance(by, dict) and by:
        top = max(by.items(), key=lambda kv: int(kv[1] or 0))[0]
    return total, top


def engagement(item, data=None) -> int:
    """امتیاز محبوبیت: بازدید + وزن واکنش‌ها."""
    if not isinstance(item, dict):
        return 0
    try:
        views = int(item.get("views") or 0)
    except (TypeError, ValueError):
        views = 0
    reactions, _ = reaction_info(item, data)
    return views + (reactions * max(0, REACTIONS_WEIGHT) if REACTIONS_ENABLED else 0)



def _to_int(raw: str, suffix: str = "") -> int:
    text = (str(raw or "").translate(_FA_DIGITS)
            .replace(",", "").replace("٬", "").replace("٫", ".").strip())
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([KMB]?)$", text, re.I)
    if not m:
        return 0
    value = float(m.group(1))
    unit = (m.group(2) or suffix or "").upper()
    return int(value * _SUFFIX.get(unit, 1))


def parse_views(html: str) -> dict:
    """{message_id: views} از HTML صفحهٔ عمومی."""
    out = {}
    for part in _MSG_SPLIT.split(str(html or ""))[1:]:
        pm = _POST_RE.match(part)
        if not pm:
            continue
        mid = int(pm.group(1))
        vm = _VIEWS_RE.search(part)
        if not vm:
            continue
        views = _to_int(vm.group(1).strip())
        if views > 0:
            out[mid] = max(views, out.get(mid, 0))
    return out


def fetch_page(handle: str, before=None) -> str:
    params = {"before": int(before)} if before else None
    resp = requests.get(
        f"https://t.me/s/{handle}",
        params=params,
        timeout=TIMEOUT,
        headers={"User-Agent": _UA, "Accept-Language": "fa,en;q=0.8"},
    )
    resp.raise_for_status()
    return resp.text


def _parse_iso(raw):
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", ""))
    except ValueError:
        return None


def _recent_hours(now_utc, interval_min) -> bool:
    st = cloudflare_kv.get(KEY, {})
    st = st if isinstance(st, dict) else {}
    last = _parse_iso(st.get("last_at"))
    if not last:
        return False
    return now_utc - last < timedelta(minutes=max(1, interval_min))


def collect_pages(handle: str, pages: int = 2) -> dict:
    """بازدید چند صفحهٔ آخر (هر صفحه ~۲۰ پست)."""
    found = {}
    before = None
    for _ in range(max(1, pages)):
        try:
            html = fetch_page(handle, before)
        except Exception as e:  # noqa: BLE001
            print(f"[VIEWS] fetch failed: {type(e).__name__}")
            break
        page = parse_views(html)
        if not page:
            break
        found.update(page)
        before = min(page)
    return found


def sync(now=None, force: bool = False, log=None) -> dict:
    """بازدیدها را در published ذخیره می‌کند. هرگز استثنا نمی‌دهد."""
    if not VIEWS_ENABLED:
        return {"ok": False, "reason": "disabled"}
    if DRY_RUN:
        print("[DRY] views sync skipped (dry-run)")
        return {"ok": False, "reason": "dry_run"}
    handle = channel_handle()
    if not handle:
        print("[VIEWS] no public channel handle (set CHANNEL_HANDLE=@your_channel)")
        return {"ok": False, "reason": "no_handle"}

    now_utc = now or datetime.utcnow()
    if not force and _recent_hours(now_utc, VIEWS_MIN_INTERVAL_MIN):
        return {"ok": False, "reason": "too_soon"}

    try:
        views = collect_pages(handle, VIEWS_PAGES)
    except Exception as e:  # noqa: BLE001
        print(f"[VIEWS] collect error: {type(e).__name__}")
        return {"ok": False, "reason": "error"}

    if not views:
        # صفحه خوانده شد ولی بازدیدی نداشت (کانال خصوصی/بدون بازدید)؛ باز هم
        # زمان را ثبت می‌کنیم تا هر اجرا دوباره درخواست ندهیم.
        st = cloudflare_kv.get(KEY, {})
        st = st if isinstance(st, dict) else {}
        st.update({"last_at": now_utc.isoformat() + "Z", "updated": 0, "seen": 0, "handle": handle})
        cloudflare_kv.put(KEY, st)
        print("[VIEWS] no views found on public preview page")
        return {"ok": False, "reason": "empty"}

    published = log if isinstance(log, list) else storage.load_published()
    changed = 0
    for item in published:
        if not isinstance(item, dict) or not item.get("published"):
            continue
        mid = item.get("message_id")
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        if mid in views and item.get("views") != views[mid]:
            item["views"] = views[mid]
            item["views_at"] = now_utc.isoformat() + "Z"
            changed += 1

    if changed:
        try:
            storage.save_published(published)
        except Exception as e:  # noqa: BLE001
            print(f"[VIEWS] save failed: {type(e).__name__}")
            return {"ok": False, "reason": "save_failed"}

    st = cloudflare_kv.get(KEY, {})
    st = st if isinstance(st, dict) else {}
    st.update({
        "last_at": now_utc.isoformat() + "Z",
        "updated": int(changed),
        "seen": len(views),
        "handle": handle,
    })
    cloudflare_kv.put(KEY, st)
    print(f"[VIEWS] {len(views)} post(s) read, {changed} updated ({handle})")
    return {"ok": True, "changed": changed, "seen": len(views)}


def _published_at(item):
    return _parse_iso(str(item.get("published_at") or "").replace("Z", "")) or datetime.min


def stats(log, days: int = 14) -> dict:
    """میانگین محبوبیت (بازدید + واکنش) کل و هر دسته."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    data = load_reactions()
    rows = []
    for p in log or []:
        if not isinstance(p, dict) or not p.get("published"):
            continue
        if p.get("deleted") or p.get("category") in ("edu", "digest", "paper", "brief", "weekly"):
            continue
        if _published_at(p) < cutoff:
            continue
        if engagement(p, data) <= 0:
            continue
        rows.append(p)

    if not rows:
        return {"count": 0, "overall": 0.0, "by_category": {}}

    by_cat = {}
    for p in rows:
        by_cat.setdefault(str(p.get("category") or "other"), []).append(engagement(p, data))

    return {
        "count": len(rows),
        "overall": float(mean(engagement(p, data) for p in rows)),
        "by_category": {k: float(mean(v)) for k, v in by_cat.items() if len(v) >= 2},
    }


def boost(items, log) -> int:
    """
    به خبرهای دسته‌های پرخوان نسبت به میانگین، حداکثر VIEWS_BOOST_MAX امتیاز
    جذابیت اضافه می‌کند. حداقل ۲ نمونه در دسته لازم است تا نویز اثر نگذارد.
    برمی‌گرداند: تعداد خبرهای تقویت‌شده.
    """
    if not items or not (VIEWS_ENABLED or REACTIONS_ENABLED):
        return 0
    s = stats(log)
    overall = s.get("overall") or 0
    if overall <= 0 or not s.get("by_category"):
        return 0

    cap = max(1, VIEWS_BOOST_MAX + (REACTIONS_BOOST_MAX if REACTIONS_ENABLED else 0))
    boosted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        avg = s["by_category"].get(str(item.get("category") or "other"))
        if not avg:
            continue
        ratio = avg / overall
        if ratio < 1.10:
            continue
        bonus = min(cap, max(1, round((ratio - 1) * 10)))
        item["hot"] = min(100, int(item.get("hot") or 0) + bonus)
        reasons = item.get("hot_reasons")
        tag = f"دستهٔ پرخوان (+{bonus})"
        if isinstance(reasons, list) and tag not in reasons:
            reasons.append(tag)
        boosted += 1

    if boosted:
        print(f"[VIEWS] boosted {boosted} item(s) from high-engagement categories")
    return boosted


def best_hours(log, days: int = 14, limit: int = 3) -> list:
    """بهترین ساعت‌های انتشار به وقت محلی بر اساس محبوبیت پست‌ها."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    data = load_reactions()
    buckets = {}
    for p in log or []:
        if not isinstance(p, dict) or not p.get("published") or p.get("deleted"):
            continue
        if p.get("category") in ("edu", "digest", "paper", "brief", "weekly"):
            continue
        when = _published_at(p)
        if when < cutoff or engagement(p, data) <= 0:
            continue
        local = when + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)
        buckets.setdefault(local.hour, []).append(engagement(p, data))
    if sum(len(v) for v in buckets.values()) < 6:
        return []
    rows = [(h, float(mean(v)), len(v)) for h, v in buckets.items() if len(v) >= 2]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:limit]


def top_items(log, days: int = 7, limit: int = 3) -> list:
    """پرمحبوبیت‌ترین خبرهای بازهٔ اخیر (برای جمع‌بندی هفتگی)."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    data = load_reactions()
    rows = []
    for p in log or []:
        if not isinstance(p, dict) or not p.get("published") or p.get("deleted"):
            continue
        if p.get("category") in ("edu", "digest", "paper", "brief", "weekly"):
            continue
        if _published_at(p) < cutoff or engagement(p, data) <= 0:
            continue
        rows.append(p)
    rows.sort(key=lambda p: engagement(p, data), reverse=True)
    return rows[:max(1, limit)]


def _fmt_views(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def top_report(log=None, n: int = 10) -> str:
    """متن دستور /top — پرمحبوبیت‌ترین‌های اخیر (بازدید + واکنش)."""
    log = log if isinstance(log, list) else storage.load_published()
    data = load_reactions()
    rows = [
        p for p in log
        if isinstance(p, dict) and p.get("published") and not p.get("deleted")
        and engagement(p, data) > 0
    ]
    if not rows:
        hint = "" if channel_handle() else (
            "\n\n⚠️ برای فعال‌شدن این قابلیت، متغیر <code>CHANNEL_HANDLE</code> را "
            "مثل <code>@your_channel</code> تنظیم کن."
        )
        return "📈 هنوز بازدید/واکنشی ثبت نشده است." + hint

    rows.sort(key=lambda p: engagement(p, data), reverse=True)
    lines = ["📈 <b>پربازدیدترین‌های اخیر</b>", "─" * 20]
    for i, p in enumerate(rows[:n], 1):
        title = escape_html(clip(p.get("title_fa") or p.get("title"), 70))
        source = escape_html(clip(p.get("source"), 30))
        views = _fmt_views(p.get("views"))
        reactions, top_emoji = reaction_info(p, data)
        extra = f" · {top_emoji} {_fmt_views(reactions)}" if reactions else ""
        lines.append(f"{i}. <b>{views}</b>{extra} — {title}")
        if source:
            lines.append(f"   <i>{source}</i>")

    s = stats(log)
    if s.get("by_category"):
        best = sorted(s["by_category"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines += ["", "─" * 20, "📊 <b>میانگین محبوبیت دسته‌ها</b>"]
        lines += [f"• {escape_html(k)}: {_fmt_views(v)}" for k, v in best]

    hours = best_hours(log)
    if hours:
        lines += ["", "🕐 <b>بهترین ساعت‌های انتشار (ایران)</b>"]
        lines += [f"• {h:02d}:00 — میانگین {_fmt_views(v)}" for h, v, _ in hours]

    lines += ["", f"<i>نمونهٔ آماری: {s.get('count', 0)} پست</i>"]
    return "\n".join(lines)[:3900]
