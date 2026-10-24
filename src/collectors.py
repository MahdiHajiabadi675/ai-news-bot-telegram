import feedparser
import hashlib
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from .classifier import is_ai_related

FEEDS = [
    # === منابع رسمی AI (Trust 5) ===
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "DeepMind", "url": "https://deepmind.google/blog/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Anthropic", "url": "https://www.anthropic.com/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Meta AI", "url": "https://ai.meta.com/blog/rss/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "HuggingFace", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Microsoft AI", "url": "https://blogs.microsoft.com/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},

    # === منابع معتبر AI (Trust 4) ===
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en", "trusted": True, "fast_track": False},

    # === منابع تحقیقاتی AI (Trust 4) ===
    {"name": "Papers with Code", "url": "https://paperswithcode.com/rss.xml", "lang": "en", "trusted": True, "fast_track": False},

    # === منابع عمومی معتبر (Trust 3) ===
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

# حداکثر آیتم از هر فید (بقیه در اجرای بعدی دیده می‌شوند)
MAX_PER_FEED = 8


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


def _looks_ai_related(text: str) -> bool:
    """برای فیدهای عمومی: فقط خبرهای مرتبط با AI نگه داشته شوند"""
    return is_ai_related(text)

def collect(seen: set) -> list:
    """
    جمع‌آوری از همهٔ فیدها. وضعیت هر فید در collect.health ثبت می‌شود:
      {نام: {"ok", "items" (کل آیتم‌های فید), "new" (جدید و پذیرفته‌شده),
             "filtered" (ردشده با فیلتر AI), "err"}}
    فیدی که خطا بدهد یا صفر آیتم داشته باشد ok=False می‌شود.
    """
    articles = []
    collected_ids = set()
    health = {}
    for feed_cfg in FEEDS:
        name = feed_cfg["name"]
        h = {"ok": False, "items": 0, "new": 0, "filtered": 0, "err": None}
        health[name] = h
        try:
            response = requests.get(
                feed_cfg["url"],
                timeout=10,
                headers=FEED_HEADERS,
            )
            response.raise_for_status()
            d = feedparser.parse(response.content)
            h["items"] = len(d.entries)
            if not d.entries:
                # صفحه‌ای که RSS نیست (مثلاً HTML یا ۴۰۴ نرم) → آیتمی ندارد
                h["err"] = "فید خالی یا نامعتبر (RSS نیست)"
                print(f"[WARN] {name}: no entries")
                continue
            h["ok"] = True
            for entry in d.entries[:MAX_PER_FEED]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary = entry.get("summary", "")
                content = entry.get("content", [])
                if content and isinstance(content, list):
                    summary = content[0].get("value", summary)
                summary = _clean_html(summary)

                # فیدهای عمومی (مثل TechCrunch/The Verge/دیجیاتو) فقط اگر
                # خبر مرتبط با AI باشد نگه داشته می‌شود.
                if not feed_cfg["trusted"] and not _looks_ai_related(title + " " + summary):
                    h["filtered"] += 1
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
                    "fast_track": feed_cfg["fast_track"],
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "collected_at": datetime.utcnow().isoformat() + "Z",
                    "status": "new",
                    "published": False
                })
        except Exception as e:
            h["err"] = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"[WARN] {name}: {e}")
    collect.health = health
    ok = sum(1 for h in health.values() if h["ok"])
    print(f"[INFO] Feeds OK: {ok}/{len(health)} — Collected {len(articles)} new articles")
    return articles


collect.health = {}
