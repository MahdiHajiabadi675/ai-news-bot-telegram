"""
موضوعات مورد علاقه — اولویت‌دهی، نه فیلتر.

ادمین از داخل ربات (`/topics add ایمنی`) موضوعاتی را ثبت می‌کند. هر خبری که با
آن موضوع هم‌خوانی داشته باشد:

  • یک نشانهٔ `topic_hit` می‌گیرد (در پیام بازبینی و مرتب‌سازی انتشار دیده می‌شود)،
  • «جذابیت»اش ۱۲ واحد بالا می‌رود (پس شانس انتشار خودکار بیشتر می‌شود)،
  • در `calculate_score` هم ۱۰ امتیاز اضافه می‌گیرد.

هیچ خبری به‌خاطر نداشتن موضوع حذف نمی‌شود؛ فقط ترتیب و شانس اولویت عوض می‌شود.
"""
import re

MAX_TOPICS = 10
MAX_LEN = 40
HOT_BONUS = 12
SCORE_BONUS = 10


def clean_topic(raw) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    return text[:MAX_LEN]


def clean_list(raw) -> list:
    out, seen = [], set()
    for item in raw or []:
        topic = clean_topic(item)
        key = topic.lower()
        if topic and key not in seen:
            seen.add(key)
            out.append(topic)
        if len(out) >= MAX_TOPICS:
            break
    return out


def load(state) -> list:
    return clean_list((state or {}).get("topics") or [])


def _haystack(item) -> str:
    parts = [item.get("title"), item.get("summary"), item.get("source"),
             item.get("category"), " ".join(item.get("hashtags") or [])]
    return " ".join(str(p or "") for p in parts).lower()


def matches(item, topics) -> str:
    """نام اولین موضوعی که با این خبر هم‌خوانی دارد (یا رشتهٔ خالی)"""
    text = _haystack(item)
    for topic in topics:
        if topic and topic.lower() in text:
            return topic
    return ""


def boost(items, topics) -> int:
    """به خبرهای هم‌خوان با موضوعات، نشانه و کمی جذابیت اضافه می‌کند."""
    topics = clean_list(topics)
    if not topics:
        return 0
    hits = 0
    for item in items:
        topic = matches(item, topics)
        if not topic:
            continue
        item["topic_hit"] = topic
        item["hot"] = min(100, int(item.get("hot") or 0) + HOT_BONUS)
        reasons = item.get("hot_reasons")
        tag = f"موضوع: {topic}"
        if isinstance(reasons, list) and tag not in reasons:
            reasons.append(tag)
        hits += 1
    if hits:
        print(f"[TOPICS] boosted {hits} item(s) for topics: {', '.join(topics)}")
    return hits
