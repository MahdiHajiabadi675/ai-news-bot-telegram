"""پست آموزشی هوش مصنوعی — فقط متن، کم‌توکن و با فاصلهٔ طولانی.

ترتیب تلاش:
  1) Gemini (اگر خطا داد — مثلاً 503 — ادامه می‌دهیم)
  2) مدل‌های رایگان OpenRouter (فقط مدل‌های :free یا با قیمت صفر)

سیاست مصرف:
  • حداکثر یک پست در هر AI_POST_INTERVAL_HOURS (پیش‌فرض ۲۴ ساعت)
  • بعد از شکست، تا AI_POST_RETRY_MINUTES دوباره تلاش نمی‌شود
  • خروجی مدل با maxOutputTokens کوچک محدود می‌شود
  • هیچ عکس/ویدیو/فایلی تولید یا ارسال نمی‌شود
"""
import re
from datetime import datetime, timedelta

import requests

from .bot import send_message
from .config import (
    AI_POST_ENABLED,
    AI_POST_INTERVAL_HOURS,
    AI_POST_MAX_CHARS,
    AI_POST_MAX_TOKENS,
    AI_POST_RETRY_MINUTES,
    CHANNEL_ID,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_NAME,
    OPENROUTER_MAX_MODELS,
    OPENROUTER_MODELS,
    OPENROUTER_SITE_URL,
)
from .formatting import clip, escape_html
from . import cloudflare_kv

STATE_KEY = "ai_post_state"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# اگر فهرست مدل‌های OpenRouter در دسترس نبود، این‌ها امتحان می‌شوند.
# (شناسه‌ها ممکن است عوض شوند؛ OPENROUTER_MODELS همیشه اولویت دارد.)
DEFAULT_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
]

# چرخش موضوع‌ها؛ هر پست یک موضوع تازه می‌گیرد تا محتوا تکراری نشود.
TOPICS = [
    {"title": "هوش مصنوعی، یادگیری ماشین و یادگیری عمیق چه تفاوتی دارند؟",
     "hint": "تفاوت این سه را با یک مثال روزمره توضیح بده."},
    {"title": "مدل زبانی چطور متن می‌سازد؟",
     "hint": "ایدهٔ «پیش‌بینی توکن بعدی» را ساده توضیح بده."},
    {"title": "تفاوت چت‌بات و مدل زبانی چیست؟",
     "hint": "نقش رابط کاربری و نقش مغز مدل را جدا کن."},
    {"title": "یک پرامپت خوب چه ویژگی‌هایی دارد؟",
     "hint": "نقش، هدف، محدودیت و قالب خروجی را با یک مثال کوتاه بگو."},
    {"title": "RAG چیست و چرا برای شرکت‌ها مهم است؟",
     "hint": "تفاوت آن با آموزش دادن مدل را توضیح بده."},
    {"title": "توهم هوش مصنوعی (Hallucination) چیست؟",
     "hint": "چرا مدل با اعتماد به نفس اشتباه می‌گوید و چطور آن را کم کنیم."},
    {"title": "یادگیری نظارت‌شده و بدون نظارت",
     "hint": "تفاوت دادهٔ برچسب‌دار و بدون برچسب را با مثال بگو."},
    {"title": "شبکهٔ عصبی در یک نگاه",
     "hint": "نورون، وزن و لایه را بدون فرمول پیچیده توضیح بده."},
    {"title": "هوش مصنوعی مولد چه چیزهایی می‌سازد؟",
     "hint": "متن، تصویر، صدا و ویدیو را با یک مثال کوتاه معرفی کن."},
    {"title": "عامل‌های هوش مصنوعی (AI Agents) چه هستند؟",
     "hint": "تفاوت آن‌ها با یک چت‌بات معمولی را بگو."},
    {"title": "حریم خصوصی در استفاده از ابزارهای AI",
     "hint": "چه داده‌هایی را نباید در چت‌بات‌ها وارد کرد."},
    {"title": "مدل باز (Open-weight) در برابر مدل بسته",
     "hint": "مزایا و محدودیت‌های هر کدام را منصفانه بگو."},
    {"title": "چطور خبرهای هوش مصنوعی را درست دنبال کنیم؟",
     "hint": "تفاوت خبر رسمی، پیش‌چاپ و شایعه را توضیح بده."},
    {"title": "هوش مصنوعی در کار روزمره: از کجا شروع کنیم؟",
     "hint": "سه کار کوچک و کم‌ریسک پیشنهاد بده."},
    {"title": "Fine-tuning و آموزش مجدد مدل",
     "hint": "چه زمانی به fine-tuning نیاز داریم و چه زمانی نه."},
    {"title": "ارزیابی کیفیت خروجی مدل‌های زبانی",
     "hint": "چرا بنچمارک تنها کافی نیست و تست واقعی چه نقشی دارد."},
]

_BAD_MODEL_PARTS = (
    "vision", "image", "audio", "embed", "rerank", "tts", "whisper",
    "moderation", "guard", "safety",
)


def _now():
    return datetime.utcnow()


def _parse_time(value):
    """ISO → datetime (سازگار با Z و بدون منطقهٔ زمانی). خطا → None"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def _load_state():
    data = cloudflare_kv.get(STATE_KEY, {})
    return data if isinstance(data, dict) else {}


def should_run(data=None, now=None, force=False):
    """آیا الان وقت پست آموزشی است؟ (بازهٔ طولانی + عقب‌نشینی بعد از شکست)"""
    if not AI_POST_ENABLED:
        return False
    data = _load_state() if data is None else data
    if not isinstance(data, dict):
        data = {}
    if data.get("paused"):
        return False
    if force:
        return True

    now = now or _now()
    last_post = _parse_time(data.get("last_post_at"))
    if last_post and now - last_post < timedelta(hours=AI_POST_INTERVAL_HOURS):
        return False

    last_attempt = _parse_time(data.get("last_attempt_at"))
    if last_attempt and now - last_attempt < timedelta(minutes=AI_POST_RETRY_MINUTES):
        return False
    return True


def _build_prompt(topic):
    """پرامپت کوتاه — عمداً بدون توضیح اضافه، برای کم‌ترین مصرف توکن"""
    return (
        "تو یک نویسندهٔ فارسی‌زبان و آموزش‌دهندهٔ هوش مصنوعی هستی.\n"
        f"یک پست آموزشی کوتاه دربارهٔ «{topic['title']}» بنویس.\n"
        f"نکتهٔ محتوایی: {topic.get('hint', '')}\n\n"
        "قواعد:\n"
        "- فقط فارسی روان و ساده؛ بین ۱۲۰ تا ۱۸۰ کلمه.\n"
        "- با یک جملهٔ جذاب شروع کن، سپس ۳ نکتهٔ کوتاه و یک جمع‌بندی یک‌خطی.\n"
        "- از تیتر، markdown، لینک، کد، نقل‌قول و تبلیغات استفاده نکن.\n"
        "- خروجی فقط متن پست باشد؛ بدون مقدمه و بدون توضیح دربارهٔ خودت."
    )


def _clean(text):
    """پاک‌سازی خروجی مدل: حذف fences/markdown/لینک و فشرده‌سازی خط‌های خالی"""
    text = str(text or "").strip()
    if "```" in text:
        parts = [p for p in text.split("```") if p.strip()]
        text = parts[0] if parts else text
        if text.lstrip().lower().startswith(("text", "markdown", "html")):
            text = text.lstrip().split("\n", 1)[-1]
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]*)\)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip().strip('"').strip("«»").strip()
    return text


def format_post(topic, body):
    """قالب HTML امن برای تلگرام (بدون هیچ رسانه‌ای)"""
    body = clip(_clean(body), AI_POST_MAX_CHARS)
    title = escape_html(topic.get("title", "آموزش هوش مصنوعی"))
    return (
        f"🧠 <b>{title}</b>\n\n"
        f"{escape_html(body)}\n\n"
        "━━━━━━━━━━━━━━\n"
        "📚 <i>آموزش کوتاه هوش مصنوعی</i>\n"
        "#آموزش_هوش_مصنوعی"
    )


def _gemini_generate(prompt):
    """یک درخواست به Gemini — هر خطایی → None تا fallback انجام شود"""
    if not GEMINI_API_KEY:
        print("[INFO] ai_post: GEMINI_API_KEY نیست — می‌روم سراغ OpenRouter")
        return None
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": AI_POST_MAX_TOKENS,
        },
    }
    url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent"
    try:
        resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=45)
        if resp.status_code != 200:
            print(f"[WARN] ai_post Gemini: HTTP {resp.status_code}")
            return None
        data = resp.json()
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        text = _clean(text)
        if len(text) < 40:
            print("[WARN] ai_post Gemini: خروجی خیلی کوتاه/خالی")
            return None
        print(f"[INFO] ai_post: Gemini موفق بود ({GEMINI_MODEL})")
        return text
    except Exception as e:
        print(f"[WARN] ai_post Gemini: {e}")
        return None


def _model_rank(model_id):
    """مدل‌های قوی‌تر و شناخته‌شده‌تر اول امتحان می‌شوند (بدون هزینهٔ اضافه)"""
    mid = model_id.lower()
    order = ("llama-3.3-70b", "gemma-4", "gemma-3", "qwen", "nemotron",
             "mistral", "deepseek", "llama", "inkling")
    for i, key in enumerate(order):
        if key in mid:
            return i
    return len(order)


def _pick_free_models():
    """فهرست زندهٔ مدل‌های رایگان OpenRouter (با fallback به کش/فهرست پیش‌فرض)"""
    if OPENROUTER_MODELS:
        return OPENROUTER_MODELS[:max(1, OPENROUTER_MAX_MODELS)]

    free = []
    try:
        resp = requests.get(f"{OPENROUTER_BASE}/models", timeout=20)
        if resp.status_code == 200:
            for model in (resp.json().get("data") or []):
                mid = str(model.get("id") or "")
                if not mid or any(part in mid.lower() for part in _BAD_MODEL_PARTS):
                    continue
                pricing = model.get("pricing") or {}
                prompt_price = str(pricing.get("prompt", "1")).strip()
                completion_price = str(pricing.get("completion", "1")).strip()
                is_free = mid.endswith(":free") or (
                    prompt_price in ("0", "0.0", "0.00")
                    and completion_price in ("0", "0.0", "0.00")
                )
                if is_free:
                    free.append(mid)
    except Exception as e:
        print(f"[WARN] ai_post: فهرست مدل‌های OpenRouter نیامد ({e})")

    if free:
        free = sorted(set(free), key=_model_rank)
        try:
            cloudflare_kv.put("openrouter_free_models", free[:10])
        except Exception:
            pass
        return free[:max(1, OPENROUTER_MAX_MODELS)]

    cached = cloudflare_kv.get("openrouter_free_models")
    if isinstance(cached, list) and cached:
        return [m for m in cached if isinstance(m, str)][:max(1, OPENROUTER_MAX_MODELS)]

    return DEFAULT_FREE_MODELS[:max(1, OPENROUTER_MAX_MODELS)]


def _openrouter_generate(prompt):
    """تلاش روی چند مدل رایگان OpenRouter؛ اولین موفق برمی‌گردد"""
    if not OPENROUTER_API_KEY:
        print("[INFO] ai_post: OPENROUTER_API_KEY نیست")
        return None

    models = _pick_free_models()
    if not models:
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": AI_POST_MAX_TOKENS,
        "temperature": 0.7,
    }

    for model in models:
        body = dict(payload, model=model)
        try:
            resp = requests.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=headers, json=body, timeout=60,
            )
            if resp.status_code != 200:
                print(f"[WARN] ai_post OpenRouter {model}: HTTP {resp.status_code}")
                continue
            data = resp.json()
            choices = data.get("choices") or []
            text = (choices[0].get("message") or {}).get("content") if choices else ""
            text = _clean(text)
            if len(text) < 40:
                print(f"[WARN] ai_post OpenRouter {model}: خروجی خالی/کوتاه")
                continue
            print(f"[INFO] ai_post: OpenRouter موفق بود ({model})")
            return text
        except Exception as e:
            print(f"[WARN] ai_post OpenRouter {model}: {e}")
    return None


def generate(topic):
    """Gemini اول؛ اگر در دسترس نبود اولین مدل رایگان OpenRouter"""
    prompt = _build_prompt(topic)
    text = _gemini_generate(prompt)
    if text:
        return text, f"gemini:{GEMINI_MODEL}"
    print("[INFO] ai_post: Gemini در دسترس نبود — تلاش با مدل‌های رایگان OpenRouter")
    text = _openrouter_generate(prompt)
    if text:
        return text, "openrouter"
    return None, None


def maybe_publish(state=None, force=False):
    """اگر نوبت باشد، یک پست آموزشی متنی منتشر می‌کند. خروجی: True/False"""
    if state and state.get("paused"):
        print("[INFO] ai_post: ربات متوقف است — رد شد")
        return False
    if not AI_POST_ENABLED:
        print("[INFO] ai_post: خاموش است (AI_POST_ENABLED=false)")
        return False

    data = _load_state()
    if not should_run(data=data, force=force):
        print("[INFO] ai_post: هنوز زمان پست آموزشی نرسیده")
        return False

    now = _now()
    index = int(data.get("topic_index", 0) or 0) % len(TOPICS)
    topic = TOPICS[index]
    print(f"[INFO] ai_post: موضوع «{topic['title']}»")

    body, provider = generate(topic)
    data["last_attempt_at"] = now.isoformat() + "Z"

    if not body:
        data["failures"] = int(data.get("failures", 0) or 0) + 1
        data["last_error"] = "generation_failed"
        cloudflare_kv.put(STATE_KEY, data)
        print("[WARN] ai_post: هیچ مدلی پاسخ نداد — تلاش بعدی در بازهٔ retry")
        return False

    text = format_post(topic, body)
    result = send_message(CHANNEL_ID, text)
    ok = isinstance(result, dict) and bool(result.get("ok"))
    if not ok:
        data["failures"] = int(data.get("failures", 0) or 0) + 1
        data["last_error"] = "telegram_send_failed"
        cloudflare_kv.put(STATE_KEY, data)
        print("[WARN] ai_post: ارسال به کانال ناموفق بود")
        return False

    data.update({
        "last_post_at": now.isoformat() + "Z",
        "last_provider": provider,
        "last_topic": topic["title"],
        "topic_index": (index + 1) % len(TOPICS),
        "failures": 0,
        "last_error": None,
    })
    cloudflare_kv.put(STATE_KEY, data)
    print(f"[INFO] ai_post: منتشر شد ({provider})")
    return True
