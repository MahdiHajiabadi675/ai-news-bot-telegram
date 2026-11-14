"""
ادغام خبرهای تکراری — «چند منبع، یک پست».

مشکل: وقتی OpenAI، TechCrunch و The Verge یک خبر را می‌دهند، کانال سه پست تقریباً
یکسان می‌گیرد. این ماژول عنوان‌های شبیه را در یک گروه می‌گذارد، بهترین آیتم را
اصلی می‌کند و بقیه را به‌صورت «همچنین در: ...» زیر همان پست می‌آورد.

محافظ‌ها:
  • شباهت با مجموعهٔ واژه‌ها (نه رشته) و آستانهٔ نسبتاً سخت‌گیر (۰٫۶).
  • اگر شمارهٔ نسخه/مدل در دو عنوان متفاوت باشد (GPT-5 در برابر GPT-6)، آستانه
    به ۰٫۸ می‌رود تا دو خبر متفاوت با هم قاطی نشوند.
  • حداکثر ۴ منبع اضافه؛ نام منبع تکراری دوبار نمی‌آید.
  • ترتیب انتخاب اصلی: اعتبار پویا ← جذابیت ← امتیاز.
"""
import re

THRESHOLD = 0.6
MAX_EXTRA_SOURCES = 4

STOPWORDS = {
    # انگلیسی
    "the", "and", "for", "with", "that", "this", "from", "its", "are", "was",
    "were", "has", "have", "had", "will", "can", "could", "would", "should",
    "not", "but", "you", "your", "our", "their", "his", "her", "they", "them",
    "into", "over", "more", "than", "after", "before", "about", "what", "why",
    "how", "when", "where", "who", "new", "now", "says", "said", "one", "two",
    "get", "gets", "got", "make", "makes", "made", "here", "there", "all",
    # فارسی
    "و", "در", "به", "از", "که", "این", "را", "با", "برای", "است", "می",
    "شود", "کرد", "های", "ها", "یک", "تا", "بر", "هم", "نیز", "اما", "یا",
    "آن", "خود", "دو", "چه", "چرا", "چگونه", "شد", "شده", "کند", "کنند",
    "دارد", "دارند", "بود", "بودن", "بین", "روی", "پس", "هر", "همه", "دیگر",
}

_WORD_RE = re.compile(r"[A-Za-z0-9\u0600-\u06FF]+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def tokens(text):
    words = _WORD_RE.findall(str(text or "").lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def numbers(text):
    return set(_NUM_RE.findall(str(text or "")))


def similarity(a, b) -> float:
    """نسبت واژه‌های مشترک به کوچک‌ترِ دو مجموعه (۰ تا ۱)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def same_story(a, b) -> bool:
    """یک رویداد است؟ اگر شمارهٔ نسخه/عدد کلیدی متفاوت باشد، هرگز ادغام نمی‌شود."""
    na, nb = numbers(a.get("title")), numbers(b.get("title"))
    if na and nb and na != nb:
        return False
    return similarity(a.get("title"), b.get("title")) >= THRESHOLD


def _rank(item):
    return (
        float(item.get("source_trust") or 0),
        int(item.get("hot") or 0),
        int(item.get("score") or 0),
    )


def merge_similar(items):
    """گروه‌بندی خبرهای هم‌موضوع؛ خروجی: یک آیتم به‌ازای هر رویداد."""
    groups = []
    for item in items:
        for group in groups:
            if same_story(item, group[0]):
                group.append(item)
                break
        else:
            groups.append([item])

    out = []
    for group in groups:
        group.sort(key=_rank, reverse=True)
        primary = dict(group[0])
        extras, seen = [], {str(primary.get("source") or "")}
        for other in group[1:]:
            src = str(other.get("source") or "").strip()
            if not src or src in seen:
                continue
            seen.add(src)
            extras.append({"source": src, "link": other.get("link") or ""})
            if len(extras) >= MAX_EXTRA_SOURCES:
                break
        if extras:
            primary["also_sources"] = extras
            print(f"[CLUSTER] «{str(primary.get('title'))[:40]}» + {len(extras)} منبع دیگر")
        out.append(primary)
    return out
