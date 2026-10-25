import calendar
import feedparser
import hashlib
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from .classifier import ai_relevance
from .ai_terms import PASS_SCORE
from .config import MAX_PER_FEED, MAX_ITEM_AGE_HOURS

# official=True: منبع رسمی آزمایشگاه/شرکت (انتشار خودکار؛ src/newsworthiness.py)
# فیدهای trusted=True اختصاصی AI هستند و فیلتر نمی‌شوند (لازم نیست «AI» در متن بیاید).
# فیدهای trusted=False عمومی‌اند و با امتیاز ارتباط (src/ai_terms.py) فیلتر می‌شوند.
# وضعیت هر فید در ربات: /feeds — فیدی که خراب شود همان‌جا و با پیام هشدار دیده می‌شود.
_COMMUNITY = "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds"

FEEDS = [
    # === سایت رسمی شرکت‌ها و آزمایشگاه‌های AI ===
    {"name": "OpenAI Blog", "url": "https://openai.com/news/rss.xml", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    {"name": "Google AI", "url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    {"name": "Google Research", "url": "https://research.google/blog/rss/", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    {"name": "DeepMind", "url": "https://deepmind.google/blog/rss.xml", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    {"name": "HuggingFace", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    # Anthropic و Meta فید رسمی ندارند؛ این‌ها فیدهای جامعه‌اند که هر ساعت از سایت رسمی ساخته می‌شوند
    {"name": "Anthropic", "url": f"{_COMMUNITY}/feed_anthropic_news.xml", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    {"name": "Meta AI", "url": f"{_COMMUNITY}/feed_meta_ai.xml", "lang": "en", "trusted": True, "official": True, "fast_track": False},
    # بلاگ‌های عمومی شرکت‌ها (همه‌ی موضوع‌ها، پس فیلتر AI دارند)
    {"name": "Microsoft Research", "url": "https://www.microsoft.com/en-us/research/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "AWS ML Blog", "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Apple ML Research", "url": "https://machinelearning.apple.com/rss.xml", "lang": "en", "trusted": True, "fast_track": False},

    # === رسانه‌های اختصاصی AI ===
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "The Decoder", "url": "https://the-decoder.com/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "MarkTechPost", "url": "https://www.marktechpost.com/feed/", "lang": "en", "trusted": True, "fast_track": False},

    # === رسانه‌های عمومی (فیلتر AI دارند) ===
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "Ars Technica", "url": "https://arstechnica.com/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "دیجیاتو", "url": "https://www.digiato.com/feed", "lang": "fa", "trusted": False, "fast_track": False},
    {"name": "زومگ", "url": "https://zoomg.ir/feed", "lang": "fa", "trusted": False, "fast_track": False},
    {"name": "تک‌راسا", "url": "https://www.techrasa.com/feed", "lang": "fa", "trusted": False, "fast_track": False},
]

# بعضی فیدها به User-Agentهای غیرمرورگر ۴۰۳ می‌دهند
FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

FETCH_WORKERS = 8


def _normalize_url(url: str) -> str:
    """نرمال‌سازی لینک برای مقایسه بهتر"""
    url = url.strip().lower()
    url = url.split("?")[0]
    url = url.rstrip("/")
    return url

def _generate_id(title: str, link: str) -> str:
    """ساخت ID یکتا برای خبر"""
    # ترکیب لینک نرمال‌شده + عنوان هش شده
    normalized = _normalize_url(link) + "|" + title.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

def _clean_html(html):
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def _entry_tags(entry) -> str:
    """تگ/دستهٔ RSS خبر (مثلاً «AI»، «OpenAI») — نشانهٔ قوی از طرف سردبیر خود سایت"""
    tags = entry.get("tags") or []
    return " ".join(t.get("term", "") for t in tags if isinstance(t, dict))


def _entry_date(entry):
    """تاریخ انتشار خبر (UTC naive) یا None اگر فید تاریخ نداده باشد"""
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        return datetime.utcfromtimestamp(calendar.timegm(t))
    except Exception:
        return None


def _newest_first(entries):
    """
    مرتب‌سازی بر اساس تاریخ انتشار (جدیدترین اول).
    فیدهایی مثل OpenAI (۱۳۰۰+ آیتم) یا HuggingFace (۹۰۰+) کل آرشیو را برمی‌گردانند و ترتیبشان
    تضمین‌شده «جدیدترین اول» نیست؛ بدون مرتب‌سازی، «۸ آیتم اول» ممکن است همیشه خبرهای قدیمی باشد.
    آیتم بدون تاریخ جای خودش را (به‌ترتیب فید) حفظ می‌کند.
    """
    dated = [(e, _entry_date(e)) for e in entries]
    if not any(d for _, d in dated):
        return list(entries)
    far_past = datetime(1970, 1, 1)
    return [e for e, _ in sorted(dated, key=lambda x: x[1] or far_past, reverse=True)]


def interleave_by_source(articles: list) -> list:
    """
    ترتیب نوبتی بین منابع (هر منبع یکی، به‌ترتیب). وقتی سقف MAX_ARTICLES_PER_RUN اعمال می‌شود،
    منابع آخر لیست (فارسی‌ها) گرسنه نمی‌مانند.
    """
    by_source, order = {}, []
    for a in articles:
        if a["source"] not in by_source:
            by_source[a["source"]] = []
            order.append(a["source"])
        by_source[a["source"]].append(a)
    out, i = [], 0
    while len(out) < len(articles):
        for name in order:
            if i < len(by_source[name]):
                out.append(by_source[name][i])
        i += 1
    return out


def _fetch(feed_cfg):
    """دریافت یک فید (در thread). برمی‌گرداند (entries, None) یا (None, خطا)"""
    try:
        response = requests.get(feed_cfg["url"], timeout=10, headers=FEED_HEADERS)
        response.raise_for_status()
        return feedparser.parse(response.content).entries, None
    except Exception as e:
        return None, e


def collect(seen: set) -> list:
    """
    جمع‌آوری از همهٔ فیدها (موازی). وضعیت هر فید در collect.health ثبت می‌شود:
      {نام: {"ok", "items" (کل آیتم‌های فید), "new" (جدید و پذیرفته‌شده),
             "filtered" (ردشده با فیلتر AI), "filtered_titles", "old" (قدیمی‌تر از سقف سن),
             "newest" (تاریخ جدیدترین خبر فید), "err"}}
    فیدی که خطا بدهد یا صفر آیتم داشته باشد ok=False می‌شود.
    """
    articles = []
    collected_ids = set()
    health = {}
    now = datetime.utcnow()
    oldest_ok = now - timedelta(hours=MAX_ITEM_AGE_HOURS)

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        fetched = list(pool.map(_fetch, FEEDS))

    for feed_cfg, (entries, error) in zip(FEEDS, fetched):
        name = feed_cfg["name"]
        h = {"ok": False, "items": 0, "new": 0, "filtered": 0, "filtered_titles": [],
             "old": 0, "newest": None, "err": None}
        health[name] = h
        if error is not None:
            h["err"] = f"{type(error).__name__}: {str(error)[:120]}"
            print(f"[WARN] {name}: {error}")
            continue
        h["items"] = len(entries)
        if not entries:
            # صفحه‌ای که RSS نیست (مثلاً HTML یا ۴۰۴ نرم) → آیتمی ندارد
            h["err"] = "فید خالی یا نامعتبر (RSS نیست)"
            print(f"[WARN] {name}: no entries")
            continue
        h["ok"] = True

        entries = _newest_first(entries)
        newest = _entry_date(entries[0])
        h["newest"] = newest.isoformat() + "Z" if newest else None

        for entry in entries[:MAX_PER_FEED]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if not title or not link:
                continue

            published = _entry_date(entry)
            if published and published < oldest_ok:
                h["old"] += 1
                continue

            summary = entry.get("summary", "")
            content = entry.get("content", [])
            if content and isinstance(content, list):
                summary = content[0].get("value", summary)
            summary = _clean_html(summary)

            # فیدهای عمومی فقط اگر خبر مرتبط با AI باشد؛ فیدهای اختصاصی AI فیلتر نمی‌شوند.
            # امتیاز: واژه‌های وزن‌دار + تگ‌های RSS (src/ai_terms.py)
            if not feed_cfg["trusted"] and ai_relevance(title, summary, _entry_tags(entry)) < PASS_SCORE:
                h["filtered"] += 1
                if len(h["filtered_titles"]) < 3:
                    h["filtered_titles"].append(title[:80])
                continue

            news_id = _generate_id(title, link)
            if news_id in seen or news_id in collected_ids:
                continue
            collected_ids.add(news_id)
            h["new"] += 1

            articles.append({
                "id": news_id,
                "source": name,
                "lang": feed_cfg["lang"],
                "trusted": feed_cfg["trusted"],
                "official": feed_cfg.get("official", False),
                "fast_track": feed_cfg["fast_track"],
                "title": title,
                "link": link,
                "summary": summary,
                "published_at": published.isoformat() + "Z" if published else None,
                "collected_at": datetime.utcnow().isoformat() + "Z",
                "status": "new",
                "published": False
            })

    collect.health = health
    ok = sum(1 for h in health.values() if h["ok"])
    print(f"[INFO] Feeds OK: {ok}/{len(health)} — Collected {len(articles)} new articles")
    return articles


collect.health = {}
