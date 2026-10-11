import feedparser
import hashlib
import re
from datetime import datetime
from bs4 import BeautifulSoup

# trusted: اگر True باشد + خبر confirmed باشد → خودکار منتشر می‌شود
# fast_track: اگر True باشد → بدون Gemini فقط ترجمه فارسی + انتشار
FEEDS = [
    # English AI sources (trusted — منابع رسمی/معتبر)
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "HuggingFace", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "lang": "en", "trusted": True, "fast_track": False},
    # English (untrusted — نیاز به تایید)
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en", "trusted": False, "fast_track": False},
    {"name": "AI News", "url": "https://www.artificialintelligence-news.com/feed/", "lang": "en", "trusted": False, "fast_track": False},
    # Persian sources (fast_track — ترجمه خودکار)
    {"name": "دیجیاتو", "url": "https://www.digiato.com/feed", "lang": "fa", "trusted": False, "fast_track": True},
    {"name": "تک‌راسا", "url": "https://www.techrasa.com/feed", "lang": "fa", "trusted": False, "fast_track": True},
]

def _hash(text):
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:20]

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

                h = _hash(title + "|" + link)
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
