"""
سیگنال Hacker News — «آیا این خبر واقعاً داغ است؟»

چرا؟ امتیاز جذابیت فعلی از عنوان و دسته ساخته می‌شود، ولی «داغ بودن» واقعی را
جامعهٔ فنی تعیین می‌کند. HN API (Algolia) رایگان و بدون کلید است؛ برای چند خبر
برتر هر اجرا یک جست‌وجو می‌زنیم، اگر عنوان واقعاً مطابق بود امتیاز/نظرها را
می‌چسبانیم و کمی به جذابیت اضافه می‌کنیم.

محافظ‌ها:
  • فقط عنوان‌های لاتین (خبر فارسی در HN نیست).
  • تطابق عنوان با شباهت واژه‌ای (≥ ۰٫۵)؛ وگرنه چیزی اضافه نمی‌شود.
  • حداکثر ۵ خبر در هر اجرا، تایم‌اوت کوتاه، هر خطا = نادیده گرفتن.
  • سقف جذابیت ۱۰۰ است.
"""
import re

import requests

from .cluster import similarity

API = "https://hn.algolia.com/api/v1/search"
TIMEOUT = 6
MAX_ITEMS = 5
MIN_SIMILARITY = 0.5
POINTS_HOT = 300
POINTS_WARM = 100
BONUS_HOT = 10
BONUS_WARM = 6

_LATIN_RE = re.compile(r"[A-Za-z]")


def _latin_ratio(text) -> float:
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _LATIN_RE.match(c)) / len(letters)


def lookup(title):
    """(points، comments، url) برای همین عنوان در HN، یا None."""
    if _latin_ratio(title) < 0.6:
        return None
    try:
        resp = requests.get(
            API,
            params={"query": str(title)[:120], "tags": "story", "hitsPerPage": 3},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        hits = (resp.json() or {}).get("hits") or []
    except Exception as e:  # noqa: BLE001
        print(f"[HN] lookup failed: {type(e).__name__}")
        return None

    for hit in hits:
        hit_title = hit.get("title") or ""
        if similarity(title, hit_title) < MIN_SIMILARITY:
            continue
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        if points <= 0:
            continue
        url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        return {"points": points, "comments": comments, "url": url}
    return None


def enrich(items, limit: int = MAX_ITEMS):
    """به چند خبر برتر، اطلاعات HN و کمی امتیاز جذابیت اضافه می‌کند."""
    candidates = sorted(
        [i for i in items if _latin_ratio(i.get("title")) >= 0.6],
        key=lambda i: int(i.get("hot") or 0),
        reverse=True,
    )[:limit]

    found = 0
    for item in candidates:
        info = lookup(item.get("title"))
        if not info:
            continue
        item["hn"] = info
        bonus = BONUS_HOT if info["points"] >= POINTS_HOT else BONUS_WARM if info["points"] >= POINTS_WARM else 0
        if bonus:
            base = int(item.get("hot") or 0)
            item["hot"] = min(100, base + bonus)
            reasons = item.get("hot_reasons")
            tag = f"HN {info['points']}"
            if isinstance(reasons, list) and tag not in reasons:
                reasons.append(tag)
        found += 1
    if found:
        print(f"[HN] matched {found} item(s) on Hacker News")
    return items
