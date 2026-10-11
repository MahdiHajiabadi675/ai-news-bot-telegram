import feedparser
import hashlib
import re
from datetime import datetime
from bs4 import BeautifulSoup

# trusted: منبع معتبر (انتشار خودکار با اسکور بالا)
# fast_track: انتشار سریع بدون Gemini (فقط ترجمه فارسی)
FEEDS = [
    # === منابع معتبر (Trust 5) ===
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "HuggingFace", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Anthropic", "url": "https://www.anthropic.com/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Meta AI", "url": "https://ai.meta.com/blog/rss/", "lang": "en", "trusted": True, "fast_track": False},

    # === منابع خوب (Trust 4) ===
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en", "trusted": True, "fast_track": False},

    # === منابع متوسط (Trust 3) ===
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "AI News", "url": "https://www.artificialintelligence-news.com/feed/", "lang": "en", "trusted": False, "fast_track": False},

    # === منابع فارسی (Fast-track) ===
    {"name": "دیجیاتو", "url": "https://www.digiato.com/feed", "lang": "fa", "trusted": False, "fast_track": True},
    {"name": "تک‌راسا", "url": "https://www.techrasa.com/feed", "lang": "fa", "trusted": False, "fast_track": True},
]

def _normalize_url(url: str) -> str:
    """نرمال‌سازی لینک برای مقایسه بهتر"""
    url = url.strip().lower()
    url = url.split("?")[0]  # حذف query params
    url = url.rstrip("/")     # حذف اسلش آخر
    return url

def _hash(title: str, link: str) -> str:
    """هش بر اساس لینک نرمال‌شده (نه عنوان) تا تکراری نشناخته شه"""
    return hashlib.sha256(_normalize_url(link).encode("utf-8")).hexdigest()[:20]

def _clean_html(html):
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]

def collect(seen: set) -> list:
    articles = []
    for feed_cfg in FEEDS:
        try:
            d = feedparser.parse(feed_cfg["url"])
            for entry in d.entries[:15]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary = entry.get("summary", "")
                content = entry.get("content", [])
                if content and isinstance(content, list):
                    summary = content[0].get("value", summary)
                summary = _clean_html(summary)

                h = _hash(title, link)
                if h in seen:
                    continue

                articles.append({
                    "hash": h,
                    "source": feed_cfg["name"],
                    "lang": feed_cfg["lang"],
                    "trusted": feed_cfg["trusted"],
                    "fast_track": feed_cfg["fast_track"],
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "collected_at": datetime.utcnow().isoformat() + "Z"
                })
        except Exception as e:
            print(f"[WARN] {feed_cfg['name']}: {e}")
    print(f"[INFO] Collected {len(articles)} new articles")
    return articles
