import re
import requests
from .config import (
    BOT_TOKEN, ADMIN_ID, AUTO_TRUST_THRESHOLD, DISPLAY_TZ_OFFSET_MIN, START_BANNER_URL
)
from . import storage, source_trust
from .formatting import escape_html as _escape_html_fmt, clip, is_untranslated

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(chat_id, text, parse_mode="HTML", reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{BASE}/sendMessage", json=data, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"[ERROR] send_message: {e}")
        return None

def send_photo(chat_id, photo, caption, parse_mode="HTML", reply_markup=None):
    data = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{BASE}/sendPhoto", json=data, timeout=20)
        return resp.json()
    except Exception as e:
        print(f"[ERROR] send_photo: {e}")
        return None

def answer_callback(callback_query_id, text=None, show_alert=False):
    data = {
        "callback_query_id": callback_query_id,
        "show_alert": show_alert
    }
    if text:
        data["text"] = text
    try:
        requests.post(f"{BASE}/answerCallbackQuery", json=data, timeout=10)
    except:
        pass

def poll_updates(state):
    offset = state.get("last_update_id", 0)
    try:
        resp = requests.get(f"{BASE}/getUpdates", params={
            "offset": offset + 1,
            "allowed_updates": ["message", "callback_query"]
        }, timeout=30)
        updates = resp.json().get("result") or []
    except Exception as e:
        print(f"[ERROR] poll_updates: {e}")
        return state, []

    handled = []
    for upd in updates:
        state["last_update_id"] = upd["update_id"]

        if "callback_query" in upd:
            _handle_callback(upd["callback_query"], state)
            handled.append(upd)
        elif "message" in upd:
            _handle_message(upd["message"], state)
            handled.append(upd)

    return state, handled

def _record_decision(item, action):
    """تصمیم ادمین را در آمار اعتبار منبع ثبت می‌کند (اعتبار پویا)"""
    try:
        stats = storage.load_source_stats()
        source_trust.record_decision(stats, item.get("source", ""), action, prior=item.get("source_prior"))
        storage.save_source_stats(stats)
    except Exception as e:
        print(f"[WARN] record_decision failed: {e}")


def clear_keyboard(pending_n, approved_n):
    """کیبورد تایید پاک‌کردن صف"""
    rows = [[{"text": f"🗑 فقط صف تایید ({pending_n})", "callback_data": "clrp"}]]
    if approved_n:
        rows.append([{"text": f"🗑 همه، با صف آماده انتشار ({pending_n + approved_n})", "callback_data": "clra"}])
    rows.append([{"text": "↩️ انصراف", "callback_data": "clrx"}])
    return {"inline_keyboard": rows}


def _ask_clear(chat_id):
    pending_n = len(storage.load_pending())
    approved_n = len(storage.load_approved())
    if not pending_n and not approved_n:
        send_message(chat_id, "📭 صف خالی است")
        return
    send_message(
        chat_id,
        f"⚠️ <b>پاک‌کردن صف</b>\n\n📋 در انتظار تایید: {pending_n}\n✅ آماده انتشار: {approved_n}\n\n"
        "این کار برگشت‌پذیر نیست. (رد‌شدن‌ها در اعتبار منابع حساب نمی‌شوند.)",
        reply_markup=clear_keyboard(pending_n, approved_n),
    )


def _do_clear(include_approved):
    pending = storage.load_pending()
    n_p = len(pending)
    storage.save_pending([])
    n_a = 0
    if include_approved:
        n_a = len(storage.load_approved())
        storage.save_approved([])
    return n_p, n_a


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa_num(value):
    """اعداد فارسی برای نمایش زیباتر در پیام‌های مدیریتی."""
    return str(value).translate(_FA_DIGITS)


def _parse_auto_choice(raw):
    """تفسیر «انتشار خودکار» از متن دستور یا callback_data.

    true = روشن، false = خاموش، None = نامشخص.
    پشتیبانی: /auto on|off، /auto_on، /auto-off، /autoon، auto:on، ...
    """
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if "روشن" in s and "خاموش" not in s:
        return True
    if "خاموش" in s and "روشن" not in s:
        return False
    cleaned = re.sub(r"@[a-z0-9_]+", "", s)
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
    if cleaned in {"autoon", "autopublishon", "enableauto", "autoenable", "autotrue", "auto1"}:
        return True
    if cleaned in {"autooff", "autopublishoff", "disableauto", "autodisable", "autofalse", "auto0"}:
        return False
    return None


def _handle_callback(cb, state):
    data = cb.get("data", "")
    cb_id = cb.get("id", "")
    if ADMIN_ID and cb.get("from", {}).get("id") != ADMIN_ID:
        answer_callback(cb_id, "⛔️ دسترسی ندارید", show_alert=True)
        return

    # دکمه‌های انتشار خودکار (auto_on / auto_off / auto:on / ...)
    auto_choice = _parse_auto_choice(data)
    if auto_choice is not None:
        state["auto_publish_enabled"] = auto_choice
        state["auto_publish_set_by_admin"] = True
        label = "روشن ✅" if auto_choice else "خاموش ❌"
        answer_callback(cb_id, f"انتشار خودکار: {label}")
        send_message(ADMIN_ID, f"انتشار خودکار: {label}")
        return

    parts = data.split(":")
    action = parts[0]
    item_hash = parts[1] if len(parts) > 1 else ""

    # دکمه‌های منوی /start → همان دستورها را اجرا می‌کنند
    menu_commands = {"status", "pending", "sources", "feeds", "runs", "stats", "clear", "auto"}
    if action == "cmd" and item_hash in menu_commands:
        answer_callback(cb_id)
        _handle_message({"text": "/" + item_hash, "chat": {"id": ADMIN_ID}}, state)
        return

    pending = storage.load_pending()
    approved = storage.load_approved()

    if action == "approve":
        item = _find_and_remove(pending, item_hash)
        if item:
            _record_decision(item, "approve")
            item["status"] = "approved"
            item["admin_action"] = "approved"
            approved.append(item)
            storage.save_pending(pending)
            storage.save_approved(approved)
            answer_callback(cb_id, "✅ تایید شد — در انتشار بعدی ارسال می‌شود")
        else:
            answer_callback(cb_id, "❌ یافت نشد", show_alert=True)

    elif action == "reject":
        item = _find_and_remove(pending, item_hash)
        if item:
            _record_decision(item, "reject")
            item["status"] = "rejected"
            storage.save_pending(pending)
            pub = storage.load_published()
            pub.append({**item, "published": False, "rejected_at": _now()})
            storage.save_published(pub)
            answer_callback(cb_id, "❌ رد شد")
        else:
            answer_callback(cb_id, "❌ یافت نشد", show_alert=True)

    elif action == "feature":
        item = _find_and_remove(pending, item_hash)
        if item:
            _record_decision(item, "feature")
            item["status"] = "approved"
            item["admin_action"] = "featured"
            approved.append(item)
            storage.save_pending(pending)
            storage.save_approved(approved)
            answer_callback(cb_id, "⭐️ ویژه — در بالای کانال منتشر می‌شود")
        else:
            answer_callback(cb_id, "❌ یافت نشد", show_alert=True)

    elif action == "edit":
        for i, item in enumerate(pending):
            if _item_key(item) == item_hash:
                pending[i]["_editing"] = True
                storage.save_pending(pending)
                if ADMIN_ID:
                    send_message(ADMIN_ID, "✏️ متن ویرایش‌شده رو بفرست:")
                answer_callback(cb_id)
                break

    elif action == "clearq":
        answer_callback(cb_id)
        _ask_clear(cb.get("from", {}).get("id") or ADMIN_ID)

    elif action in ("clrp", "clra"):
        n_p, n_a = _do_clear(include_approved=(action == "clra"))
        answer_callback(cb_id, "🗑 پاک شد")
        extra = f" و {n_a} خبر آماده انتشار" if action == "clra" else ""
        send_message(ADMIN_ID, f"🗑 {n_p} خبر در انتظار تایید{extra} پاک شد.")

    elif action == "clrx":
        answer_callback(cb_id, "انصراف داده شد")

    elif action == "pause":
        state["paused"] = True
        answer_callback(cb_id, "⏸️ ربات متوقف شد")

    elif action == "resume":
        state["paused"] = False
        answer_callback(cb_id, "▶️ ربات فعال شد")

def _handle_message(msg, state):
    text = msg.get("text", "").strip()
    chat_id = msg.get("chat", {}).get("id", 0)

    pending = storage.load_pending()
    # اگر هنگام ویرایش دستوری رسید، حالت ویرایش لغو شود
    if text.startswith("/"):
        cancelled = False
        for item in pending:
            if item.get("_editing"):
                item["_editing"] = False
                cancelled = True
        if cancelled:
            storage.save_pending(pending)
    # دستورها هنگام ویرایش هم کار کنند (متنِ شروع‌شده با / را ویرایش نگیر)
    if text and not text.startswith("/"):
        for i, item in enumerate(pending):
            if item.get("_editing"):
                pending[i]["summary_fa"] = text
                pending[i]["_editing"] = False
                pending[i]["_edited"] = True
                storage.save_pending(pending)
                send_message(ADMIN_ID, "✅ ویرایش انجام شد — در انتظار تایید")
                return

    if chat_id != ADMIN_ID:
        return

    if text == "/start":
        active = "⏸️ متوقف" if state.get("paused") else "▶️ فعال"
        auto_on = state.get("auto_publish_enabled", True)
        stage_label = {
            "idle": "در انتظار اجرا",
            "collecting": "در حال جمع‌آوری",
            "analyzing": "در حال تحلیل",
            "publishing": "در حال انتشار",
            "waiting": "در انتظار تایید",
            "error": "خطا",
        }.get(state.get("current_stage", "idle"), "در انتظار اجرا")
        menu_text = (
            "🤖 <b>ربات مدیریت اخبار AI</b>\n"
            "<i>جمع‌آوری، ترجمه و انتشار هوشمند</i>\n\n"
            "━━━━━━━━━━━━━━\n"
            "📊 <b>وضعیت لحظه‌ای</b>\n"
            f"├ وضعیت: {active}\n"
            f"├ انتشار خودکار: {'روشن ✅' if auto_on else 'خاموش ❌'}\n"
            f"├ مرحله: {stage_label}\n"
            f"├ 📥 در انتظار تایید: <b>{_fa_num(len(pending))}</b>\n"
            f"└ 📤 آماده انتشار: <b>{_fa_num(len(storage.load_approved()))}</b>\n\n"
            "━━━━━━━━━━━━━━\n"
            "⌨️ <b>دستورهای پرکاربرد</b>\n"
            "<code>/status</code> — وضعیت کامل\n"
            "<code>/pending</code> — بررسی صف تایید\n"
            "<code>/auto</code> — روشن/خاموش کردن انتشار خودکار\n"
            "<code>/pause</code> · <code>/resume</code> — توقف/ادامهٔ انتشار\n"
            "<code>/clear</code> — پاک‌کردن صف\n"
            "<code>/sources</code> — اعتبار منابع\n"
            "<code>/feeds</code> — سلامت فیدها\n"
            "<code>/runs</code> — آمار اجراها\n"
            "<code>/stats</code> — آمار کلی\n\n"
            "━━━━━━━━━━━━━━\n"
            "💡 <i>از دکمه‌های زیر هم می‌توانی استفاده کنی.</i>"
        )
        menu_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 وضعیت", "callback_data": "cmd:status"},
                    {"text": "📋 صف تایید", "callback_data": "cmd:pending"},
                ],
                [
                    {"text": "📡 منابع", "callback_data": "cmd:sources"},
                    {"text": "🩺 فیدها", "callback_data": "cmd:feeds"},
                ],
                [
                    {"text": "📈 آمار", "callback_data": "cmd:stats"},
                    {"text": "🗞 اجراها", "callback_data": "cmd:runs"},
                ],
                [
                    {"text": "🤖 روشن", "callback_data": "auto_on"},
                    {"text": "⏸ خاموش", "callback_data": "auto_off"},
                ],
                [
                    {"text": "🧹 پاک‌کردن صف", "callback_data": "cmd:clear"},
                ],
            ]
        }
        sent = send_photo(chat_id, START_BANNER_URL, menu_text, reply_markup=menu_keyboard)
        if not (isinstance(sent, dict) and sent.get("ok")):
            # اگر تلگرام نتوانست بنر را بگیرد، منو بدون عکس ارسال می‌شود
            send_message(chat_id, menu_text, reply_markup=menu_keyboard)

    elif text == "/status":
        pending_count = len(storage.load_pending())
        approved_count = len(storage.load_approved())
        paused = "⏸️ متوقف" if state.get("paused") else "▶️ فعال"
        auto = "روشن" if state.get("auto_publish_enabled", True) else "خاموش"

        stage = state.get("current_stage", "idle")
        stage_emoji = {
            "idle": "💤",
            "collecting": "📡",
            "analyzing": "🧠",
            "publishing": "📤",
            "waiting": "⏳",
            "error": "🔴"
        }.get(stage, "❓")

        stage_time = state.get("stage_updated_at", "N/A")
        if stage_time and stage_time != "N/A":
            stage_time = stage_time[:19].replace("T", " ")

        last_error = state.get("last_error")
        error_text = f"\n🔴 خطای آخر: {last_error}" if last_error else ""

        send_message(chat_id,
            f"📊 <b>وضعیت ربات</b>\n\n"
            f"{stage_emoji} مرحله: {stage}\n"
            f"🕐 آخرین بروزرسانی: {stage_time}\n"
            f"وضعیت: {paused}\n"
            f"در انتظار تایید: {pending_count}\n"
            f"آماده انتشار: {approved_count}\n"
            f"انتشار خودکار: {auto}"
            f"{error_text}"
        )

    elif text == "/pending":
        pending = storage.load_pending()
        if not pending:
            send_message(chat_id, "📭 خبری در انتظار تایید نیست")
            return
        for item in pending[:5]:
            _send_review(item)
        if len(pending) > 5:
            send_message(chat_id, f"📋 {len(pending) - 5} خبر دیگر هم در صف است.",
                         reply_markup={"inline_keyboard": [[{"text": "🗑 پاک کردن کل صف", "callback_data": "clearq"}]]})

    elif text == "/clear":
        _ask_clear(chat_id)

    elif text == "/runs":
        send_message(chat_id, runs_report(storage.load_run_log()))

    elif text == "/feeds":
        send_message(chat_id, feeds_report(storage.load_feed_health()))

    elif text == "/sources":
        send_message(chat_id, sources_report(storage.load_source_stats()))

    elif text == "/pause":
        state["paused"] = True
        send_message(chat_id, "⏸️ انتشار متوقف شد")

    elif text == "/resume":
        state["paused"] = False
        send_message(chat_id, "▶️ انتشار ادامه یافت")

    elif text.lower().startswith("/auto") or re.match(r"^auto[\s_-]*(on|off)$", text, re.I):
        val = _parse_auto_choice(text)
        if val is None:
            # بدون آرگومان (دکمهٔ منوی فرمان‌های تلگرام): وضعیت + دکمه‌های شیشه‌ای
            current = state.get("auto_publish_enabled", True)
            send_message(
                chat_id,
                f"انتشار خودکار الان: {'روشن ✅' if current else 'خاموش ❌'}\nیکی را انتخاب کن:",
                reply_markup={"inline_keyboard": [[
                    {"text": "🤖 روشن", "callback_data": "auto_on"},
                    {"text": "⏸ خاموش", "callback_data": "auto_off"},
                ]]},
            )
        else:
            state["auto_publish_enabled"] = val
            state["auto_publish_set_by_admin"] = True
            status = "روشن ✅" if val else "خاموش ❌"
            send_message(chat_id, f"انتشار خودکار: {status}")

    elif text == "/stats":
        pub = storage.load_published()
        published = [p for p in pub if p.get("published")]
        rejected = [p for p in pub if not p.get("published")]
        send_message(chat_id,
            f"📊 <b>آمار</b>\n\n"
            f"منتشر شده: {len(published)}\n"
            f"رد شده: {len(rejected)}"
        )

def _find_and_remove(items, item_id):
    for i, item in enumerate(items):
        if _item_key(item) == item_id:
            return items.pop(i)
    return None

def _item_key(item):
    """کلید یکتای خبر — سازگار با آیتم‌های قدیمی (hash) و جدید (id)"""
    return item.get("id") or item.get("hash") or ""

def _now():
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"

def _parse_iso(iso):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(iso).replace("Z", ""))
    except Exception:
        return None


def _ago_text(iso):
    """«۳ ساعت پیش» / «۱۲ دقیقه پیش» / «۲ روز پیش»"""
    from datetime import datetime
    t = _parse_iso(iso)
    if not t:
        return "؟"
    mins = max(0, int((datetime.utcnow() - t).total_seconds() // 60))
    if mins < 60:
        return f"{mins} دقیقه پیش"
    if mins < 60 * 48:
        return f"{mins // 60} ساعت پیش"
    return f"{mins // 1440} روز پیش"


def runs_report(log, n=12):
    """گزارش «قیف» اجراهای اخیر: چند خبر پیدا شد و کجا کم شد"""
    from datetime import timedelta
    if not log:
        return "📈 هنوز اجرایی ثبت نشده (بعد از اولین اجرای pipeline پر می‌شود)."
    lines = ["📈 <b>اجراهای اخیر</b> (ساعت محلی)\n"]
    for r in log[-n:][::-1]:
        t = _parse_iso(r.get("t"))
        hhmm = (t + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)).strftime("%m-%d %H:%M") if t else "؟"
        tag = "🖐" if r.get("forced") else "⏱"
        if r.get("paused") and not r.get("collected"):
            lines.append(f"{tag} {hhmm} — ⏸ ربات متوقف است")
        elif r.get("skipped"):
            lines.append(f"{tag} {hhmm} — جمع‌آوری نشد (فاصله از اجرای قبلی کم بود) | منتشر: {r.get('published', 0)}")
        elif r.get("collected"):
            lines.append(
                f"{tag} {hhmm} — فید {r.get('feeds_ok', '?')}/{r.get('feeds_total', '?')} | "
                f"جدید {r.get('fetched', 0)} → تحلیل {r.get('analyzed', 0)} "
                f"(🤖{r.get('auto', 0)} 👀{r.get('pending', 0)} ❌{r.get('low_score', 0) + r.get('rejected_keyword', 0)}) | "
                f"📤 منتشر {r.get('published', 0)} | صف: {r.get('queue_pending', 0)} تایید، {r.get('queue_approved', 0)} آماده"
                + (f" | 🔤 {r['untranslated']} ترجمه‌نشده" if r.get("untranslated") else ""))
        else:
            lines.append(f"{tag} {hhmm} — جمع‌آوری انجام نشد | منتشر: {r.get('published', 0)}")
    lines.append("\n🤖 خودکار | 👀 نیازمند تایید شما | ❌ ردشده (کم‌امتیاز/کلمه مشکوک)")
    return "\n".join(lines)


def feeds_report(health):
    """گزارش سلامت فیدها (آخرین جمع‌آوری)"""
    if not health:
        return "📡 هنوز گزارشی نیست (بعد از اولین جمع‌آوری پر می‌شود)."
    bad = [(n, h) for n, h in health.items() if not h.get("ok")]
    good = [(n, h) for n, h in health.items() if h.get("ok")]
    lines = [f"📡 <b>سلامت فیدها</b>  ✅{len(good)}  ❌{len(bad)}\n"]
    for n, h in sorted(bad):
        lines.append(f"❌ <b>{_escape_html(n)}</b> — {_escape_html(h.get('err') or '?')} (پیاپی: {h.get('fails', 0)})")
    for n, h in sorted(good):
        age = f" | آخرین خبر فید: {_ago_text(h['newest'])}" if h.get("newest") else ""
        lines.append(f"✅ {_escape_html(n)}: {h.get('items', 0)} آیتم، {h.get('new', 0)} جدید"
                     + (f"، {h.get('filtered')} ردشده با فیلتر" if h.get("filtered") else "")
                     + (f"، {h.get('old')} کهنه" if h.get("old") else "") + age)
        for t in (h.get("filtered_titles") or [])[:3]:
            lines.append(f"      🚫 {_escape_html(t)}")
    checked = max((h.get("checked") or "" for h in health.values()), default="")
    if checked:
        lines.append(f"\n🕐 آخرین بررسی: {checked[:16].replace('T', ' ')} UTC")
    return "\n".join(lines)


def sources_report(stats):
    """گزارش اعتبار پویای منابع (پایه ← فعلی)"""
    from .collectors import FEEDS
    names = [f["name"] for f in FEEDS]
    rows = []
    for name in names:
        base = source_trust.prior_of(name)
        now_t = source_trust.trust_of(name, stats)
        e = stats.get(name) or {}
        pos, neg = round(float(e.get("pos", 0)), 1), round(float(e.get("neg", 0)), 1)
        arrow = "🔼" if now_t - base >= 0.3 else "🔽" if base - now_t >= 0.3 else "▫️"
        auto = "🤖" if now_t >= AUTO_TRUST_THRESHOLD else "👀"
        rows.append((now_t, f"{auto}{arrow} {_escape_html(name)}: {base} ← <b>{now_t:.1f}</b>  (✅{pos} ❌{neg})"))
    rows.sort(key=lambda r: -r[0])
    return ("📡 <b>اعتبار پویای منابع</b>\n"
            f"🤖 = انتشار خودکار (≥ {AUTO_TRUST_THRESHOLD}) | 👀 = نیازمند تایید شما\n\n"
            + "\n".join(r[1] for r in rows))


def _send_review(item):
    if not ADMIN_ID:
        return

    trust = float(item.get("source_trust", 3))
    trust_emoji = "🟢" if trust >= 4 else "🟡" if trust >= 3 else "🔴"

    score = item.get("score", 0)
    if score >= 70:
        score_label = "زیاد"
        score_emoji = "🟢"
    elif score >= 50:
        score_label = "متوسط"
        score_emoji = "🟡"
    elif score >= 30:
        score_label = "کم"
        score_emoji = "🟠"
    else:
        score_label = "ضعیف"
        score_emoji = "🔴"

    text = f"📰 <b>{_escape_html(item['title_fa'])}</b>\n\n"
    text += f"{_escape_html(item['summary_fa'])}\n\n"
    if is_untranslated(item):
        text += "🔤 <i>ترجمه نشده — متن اصلی</i>\n\n"
    elif item.get("title") and item.get("lang") not in ("fa", None):
        text += f"🌐 {_escape_html(clip(item['title'], 150))}\n\n"
    text += f"📌 اهمیت: {_escape_html(item['importance'])} | اعتبار: {_escape_html(item['confidence'])}\n"
    text += f"{trust_emoji} منبع: {_escape_html(item['source'])} (اعتبار: {trust:.1f}/5)"
    if item.get("corroborated_by"):
        text += f" — 🔗 تایید شده توسط {item['corroborated_by']} منبع دیگر"
    text += "\n"
    text += f"{score_emoji} اسکور: {score}/100 ({score_label})\n"
    if "hot" in item:
        why = "، ".join(item.get("hot_reasons") or [])
        text += f"🔥 جذابیت: {item['hot']}/100 (حد انتشار خودکار: {item.get('hot_threshold', 0):.0f})"
        text += (f" — {_escape_html(why)}" if why else "") + "\n"
    text += f"🏷️ {' '.join('#'+_escape_html(h) for h in item['hashtags'])}"
    text += f"\n🔗 {_escape_html(item['link'])}"

    if item.get("admin_question"):
        text += f"\n\n❓ {_escape_html(item['admin_question'])}"

    item_id = item.get("id", item.get("hash", ""))
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ تایید", "callback_data": f"approve:{item_id}"},
                {"text": "❌ رد", "callback_data": f"reject:{item_id}"}
            ],
            [
                {"text": "⭐️ ویژه", "callback_data": f"feature:{item_id}"},
                {"text": "✏️ ویرایش", "callback_data": f"edit:{item_id}"}
            ],
            [{"text": "🗑 پاک کردن کل صف", "callback_data": "clearq"}]
        ]
    }

    # دکمه لینک فقط وقتی لینک معتبر باشد (وگرنه کل پیام رد می‌شود)
    link = item.get("link") or ""
    if link.startswith("http"):
        keyboard["inline_keyboard"].insert(2, [{"text": "🔗 مشاهده منبع", "url": link}])

    send_message(ADMIN_ID, text, reply_markup=keyboard)

def notify_admin_new(item):
    if not ADMIN_ID:
        return
    text = f"🔔 خبر جدید (تایید خودکار — در صف انتشار):\n\n"
    text += f"<b>{_escape_html(item['title_fa'])}</b>\n"
    text += f"{_escape_html(item['summary_fa'])}\n\n"
    text += f"📌 {_escape_html(item['importance'])} | {_escape_html(item['confidence'])}\n"
    via = {"official": "🏛 منبع رسمی", "hot": "🔥 خبر جذاب"}.get(item.get("auto_reason"), "")
    if via:
        why = "، ".join(item.get("hot_reasons") or [])
        text += f"{via}: {_escape_html(item['source'])} — جذابیت {item.get('hot', 0)}/100"
        text += (f" ({_escape_html(why)})" if why else "")
    send_message(ADMIN_ID, text)

def _escape_html(value):
    return _escape_html_fmt(value)
