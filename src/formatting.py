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
from .config import SHOW_ORIGINAL_TEXT

TG_LIMIT = 4000  # سقف تلگرام ۴۰۹۶ است؛ کمی حاشیه


def escape_html(text) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def format_message(item: dict) -> str:
    emoji, divider = _emoji_divider(item)
    title = escape_html(item.get("title_fa") or item.get("title") or "")
    summary = escape_html(clip(item.get("summary_fa"), 700))
    link = escape_html(item.get("link"))
    source = escape_html(item.get("source"))
    hashtags = " ".join(f"#{escape_html(h)}" for h in (item.get("hashtags") or []))

    original = ""
    if has_original_block(item):
        orig_title = escape_html(clip(item.get("title"), 200))
        orig_summary = escape_html(clip(item.get("summary"), 500))
        body = f"<b>{orig_title}</b>" + (f"\n\n{orig_summary}" if orig_summary else "")
        original = f"\n\n🌐 <b>متن اصلی</b>\n<blockquote expandable>{body}</blockquote>"

    text = f"{emoji} <b>{title}</b>\n{divider}\n\n{summary}{original}\n\n{divider}\n"
    text += f'📰 منبع: <a href="{link}">{source}</a>'
    if hashtags:
        text += f"\n🏷️ {hashtags}"
    if is_untranslated(item):
        text += "\n\n🔤 <i>ترجمه نشده — متن اصلی</i>"
    if item.get("confidence") == "rumor":
        text += "\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>"

    # امنیت: اگر طولانی شد، اول بلوک اصلی را حذف کن (تگ‌ها نیمه‌کاره نشوند)
    if len(text) > TG_LIMIT and original:
        text = text.replace(original, "")
    return text[:TG_LIMIT] if len(text) > TG_LIMIT else text
