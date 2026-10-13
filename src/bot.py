import requests
from .config import BOT_TOKEN, ADMIN_ID
from . import storage

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
        updates = resp.json().get("result", [])
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

def _handle_callback(cb, state):
    data = cb.get("data", "")
    cb_id = cb.get("id", "")
    parts = data.split(":")
    action = parts[0]
    item_hash = parts[1] if len(parts) > 1 else ""

    pending = storage.load_pending()
    approved = storage.load_approved()

    if action == "approve":
        item = _find_and_remove(pending, item_hash)
        if item:
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
            if item["hash"] == item_hash:
                pending[i]["_editing"] = True
                storage.save_pending(pending)
                if ADMIN_ID:
                    send_message(ADMIN_ID, "✏️ متن ویرایش‌شده رو بفرست:")
                answer_callback(cb_id)
                break

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
        send_message(chat_id,
            "🤖 <b>ربات مدیریت کانال اخبار AI</b>\n\n"
            "دستورات:\n"
            "/status - وضعیت سیستم\n"
            "/pending - اخبار در انتظار تایید\n"
            "/pause - توقف انتشار\n"
            "/resume - ادامه انتشار\n"
            "/auto on|off - انتشار خودکار اخبار مهم\n"
            "/stats - آمار"
        )

    elif text == "/status":
        pending_count = len(storage.load_pending())
        approved_count = len(storage.load_approved())
        paused = "⏸️ متوقف" if state.get("paused") else "▶️ فعال"
        auto = "روشن" if state.get("auto_publish") else "خاموش"

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

        gemini_fails = state.get("gemini_fail_count", 0)
        gemini_text = f"\nGemini fail: {gemini_fails}" if gemini_fails > 0 else ""

        send_message(chat_id,
            f"📊 <b>وضعیت ربات</b>\n\n"
            f"{stage_emoji} مرحله: {stage}\n"
            f"🕐 آخرین بروزرسانی: {stage_time}\n"
            f"وضعیت: {paused}\n"
            f"در انتظار تایید: {pending_count}\n"
            f"آماده انتشار: {approved_count}\n"
            f"انتشار خودکار: {auto}"
            f"{gemini_text}"
            f"{error_text}"
        )

    elif text == "/pending":
        pending = storage.load_pending()
        if not pending:
            send_message(chat_id, "📭 خبری در انتظار تایید نیست")
            return
        for item in pending[:5]:
            _send_review(item)

    elif text == "/pause":
        state["paused"] = True
        send_message(chat_id, "⏸️ انتشار متوقف شد")

    elif text == "/resume":
        state["paused"] = False
        send_message(chat_id, "▶️ انتشار ادامه یافت")

    elif text.startswith("/auto"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in ["on", "off"]:
            state["auto_publish"] = parts[1] == "on"
            status = "روشن ✅" if state["auto_publish"] else "خاموش ❌"
            send_message(chat_id, f"انتشار خودکار: {status}")
        else:
            send_message(chat_id, "استفاده: /auto on یا /auto off")

    elif text == "/stats":
        pub = storage.load_published()
        published = [p for p in pub if p.get("published")]
        rejected = [p for p in pub if not p.get("published")]
        send_message(chat_id,
            f"📊 <b>آمار</b>\n\n"
            f"منتشر شده: {len(published)}\n"
            f"رد شده: {len(rejected)}"
        )

def _find_and_remove(items, item_hash):
    for i, item in enumerate(items):
        if item["hash"] == item_hash:
            return items.pop(i)
    return None

def _now():
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"

def _send_review(item):
    if not ADMIN_ID:
        return

    trust = item.get("source_trust", 3)
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

    text = f"📰 <b>{item['title_fa']}</b>\n\n"
    text += f"{item['summary_fa']}\n\n"
    text += f"📌 اهمیت: {item['importance']} | اعتبار: {item['confidence']}\n"
    text += f"{trust_emoji} منبع: {item['source']} (اعتبار: {trust}/5)\n"
    text += f"{score_emoji} اسکور: {score}/100 ({score_label})\n"
    text += f"🏷️ {' '.join('#'+h for h in item['hashtags'])}"
    text += f"\n🔗 {item['link']}"

    if item.get("admin_question"):
        text += f"\n\n❓ {item['admin_question']}"

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
            [
                {"text": "🔗 مشاهده منبع", "url": item.get("link", "")}
            ]
        ]
    }

    send_message(ADMIN_ID, text, reply_markup=keyboard)

def notify_admin_new(item):
    if not ADMIN_ID:
        return
    text = f"🔔 خبر جدید (خودکار منتشر شد):\n\n"
    text += f"<b>{item['title_fa']}</b>\n"
    text += f"{item['summary_fa']}\n\n"
    text += f"📌 {item['importance']} | {item['confidence']}"
    send_message(ADMIN_ID, text)
