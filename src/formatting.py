"""
قالب پیام کانال — متن فارسی + متن اصلی زیر آن

  🤖 عنوان فارسی
  ──────────
  خلاصهٔ فارسی

  🌐 متن اصلی
  ▎عنوان اصلی (انگلیسی) + خلاصهٔ اصلی      ← بلوک جمع‌شونده (expandable blockquote)

  ──────────
  📰 منبع · 🏷️ هشتگ‌ها

اگر ترجمه نشده باشد: متن اصلی با برچسب «ترجمه نشده» منتشر می‌شود (بدون بلوک تکراری).
نسخهٔ معادل این قالب در worker/index.js هم هست (formatChannelMessage).
"""
from urllib.parse import urlsplit

from .config import SHOW_ORIGINAL_TEXT, CHANNEL_SIGNATURE

TG_LIMIT = 4000  # سقف تلگرام ۴۰۹۶ است؛ کمی حاشیه

# طول‌های کوتاه‌سازی خلاصه به ترتیب اولویت (اول بلندترین).
# پیام هرگز «برش خام» نمی‌شود؛ فقط متن ورودی کوتاه می‌شود تا تگ HTML نصفه نماند.
_SUMMARY_STEPS = (700, 400, 200, 80, 0)
_TITLE_CLIP = 300
_SOURCE_CLIP = 120


def escape_html(text) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(text) -> str:
    """برای مقدار attribute (مثل href) — کوتیشن هم باید اسکیپ شود."""
    return escape_html(text).replace('"', "&quot;").replace("'", "&#39;")


def safe_link(link):
    """
    فقط http/https با دامنهٔ غیرخالی و بدون فاصله/کنترل‌کاراکتر.
    لینک نامعتبر → رشتهٔ خالی تا پیام به‌خاطر href خراب نشود (تلگرام 400 می‌دهد).
    """
    raw = str(link or "").strip()
    if not raw or any(ch in raw for ch in ' \t\r\n"<>'):
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return escape_attr(raw)


def clip(text, n: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def is_untranslated(item: dict) -> bool:
    """فقط وقتی صراحتاً translated=False باشد (آیتم‌های قدیمی بدون این فیلد ترجمه‌شده فرض می‌شوند)"""
    return item.get("translated") is False


def has_original_block(item: dict) -> bool:
    return (
        SHOW_ORIGINAL_TEXT
        and item.get("lang") not in ("fa", None)
        and not is_untranslated(item)
        and bool(item.get("title"))
    )


def _emoji_divider(item: dict):
    if item.get("category") == "fast_track":
        return "📡", "─" * 20
    if item.get("admin_action") == "featured":
        return "⭐️", "═" * 20
    if item.get("importance") == "high":
        return "🔥", "─" * 20
    if item.get("confidence") == "rumor":
        return "⚠️", "─" * 20
    return "🤖", "─" * 20


def _render(item: dict, summary_len: int, show_original: bool, tag_count: int = -1) -> str:
    """ساخت پیام با طول‌های مشخص. تنها جایی که HTML تولید می‌شود."""
    emoji, divider = _emoji_divider(item)

    title = escape_html(clip(item.get("title_fa") or item.get("title") or "", _TITLE_CLIP))
    summary = escape_html(clip(item.get("summary_fa"), summary_len)) if summary_len else ""
    source = escape_html(clip(item.get("source"), _SOURCE_CLIP))
    tags = [str(h) for h in (item.get("hashtags") or [])]
    if tag_count >= 0:
        tags = tags[:tag_count]
    hashtags = " ".join(f"#{escape_html(h)}" for h in tags)

    original = ""
    if show_original and has_original_block(item):
        orig_title = escape_html(clip(item.get("title"), 200))
        orig_summary = escape_html(clip(item.get("summary"), 500))
        body = f"<b>{orig_title}</b>" + (f"\n\n{orig_summary}" if orig_summary else "")
        original = f"\n\n🌐 <b>متن اصلی</b>\n<blockquote expandable>{body}</blockquote>"

    text = f"{emoji} <b>{title}</b>\n{divider}\n\n{summary}{original}\n\n{divider}\n"
    link = safe_link(item.get("link"))
    if link:
        text += f'📰 منبع: <a href="{link}">{source}</a>'
    else:
        # بدون لینک معتبر، فقط نام منبع (به‌جای href خراب که کل پیام را 400 می‌کند)
        text += f"📰 منبع: {source}"
    if hashtags:
        text += f"\n🏷️ {hashtags}"
    also = [s for s in (item.get("also_sources") or []) if isinstance(s, dict) and s.get("source")]
    if also:
        names = "، ".join(escape_html(clip(s.get("source"), 40)) for s in also[:4])
        if names:
            text += f"\n🔗 همچنین در: {names}"
    hn_info = item.get("hn")
    if isinstance(hn_info, dict) and hn_info.get("points"):
        text += f"\n🔥 Hacker News: {int(hn_info['points'])} امتیاز"
        if hn_info.get("comments"):
            text += f" · {int(hn_info['comments'])} نظر"
    if is_untranslated(item):
        text += "\n\n🔤 <i>ترجمه نشده — متن اصلی</i>"
    if item.get("confidence") == "rumor":
        text += "\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>"
    if CHANNEL_SIGNATURE:
        text += f"\n\n{escape_html(CHANNEL_SIGNATURE)}"
    return text


def format_message(item: dict) -> str:
    can_show_original = has_original_block(item)

    # اولویت: بلوک متن اصلی حفظ شود؛ اگر جا نشد کوتاه‌ترش کنیم و در آخر حذفش کنیم.
    candidates = []
    for summary_len in _SUMMARY_STEPS:
        if can_show_original:
            candidates.append((summary_len, True))
        candidates.append((summary_len, False))

    for summary_len, show_original in candidates:
        text = _render(item, summary_len, show_original)
        if len(text) <= TG_LIMIT:
            return text

    # عنوان/منبع/هشتگ‌ها هم می‌توانند غیرعادی بلند باشند: هشتگ‌ها را کم کن
    for tag_count in (8, 4, 0):
        for summary_len in (80, 0):
            text = _render(item, summary_len, False, tag_count=tag_count)
            if len(text) <= TG_LIMIT:
                return text

    # آخرین سنگر: بدون خلاصه/متن اصلی/هشتگ. title و source از قبل کوتاه شده‌اند،
    # پس این خروجی همیشه زیر سقف است و برش خام (خراب‌کنندهٔ تگ) لازم نمی‌شود.
    return _render(item, 0, False, tag_count=0)
