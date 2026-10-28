"""
پست آموزشی هوش مصنوعی — فقط متن، با بازهٔ طولانی (پیش‌فرض ۲۴ ساعت)

چطور کار می‌کند:
  • پایپ‌لاین هر ساعت اجرا می‌شود؛ روز به «اسلات‌های EDU_EVERY_HOURS ساعته» (به وقت نمایش)
    تقسیم شده است. اولین اجرایی که در یک اسلات جدید می‌رسد، یک پست می‌سازد و منتشر می‌کند.
    اگر ساخت شکست بخورد (Gemini خطا داد / خروجی بی‌کیفیت بود)، اجرای بعدی (ساعت بعد، همان
    اسلات) یک‌بار دیگر تلاش می‌کند؛ بعد از EDU_MAX_TRIES_PER_SLOT شکست آن اسلات رد می‌شود.
    پست ناقص هرگز منتشر نمی‌شود.
  • منبع ساخت: اول Gemini؛ اگر در دسترس نبود (مثلاً خطای 503) مدل‌های رایگان OpenRouter
    امتحان می‌شوند. فقط مدل‌های :free یا با قیمت صفر.
  • موضوع: ۱۲ دستهٔ ثابت به‌صورت چرخشی (پرامپت آماده، اشتباه رایج، مفهوم ساده، تولید تصویر، …)
    تا در طول زمان تنوع داشته باشد. عنوان‌های اخیر به مدل داده می‌شود تا تکرار نکند و عنوان
    مشابه عنوان‌های اخیر رد می‌شود.
  • قالب‌بندی را خودِ کد می‌سازد (نه مدل): خروجی JSON ساختاریافته، همه‌چیز escape می‌شود،
    پرامپت آماده داخل بلوک <pre> می‌آید تا با یک لمس کپی شود. هیچ عکس/ویدیو/فایلی تولید نمی‌شود.
  • کلید خاموش/روشن: دستور /edu on|off در ربات (state.edu_enabled) یا EDU_POST_ENABLED=false.
  • بودجهٔ Gemini از src/gemini_budget.py رعایت می‌شود؛ اگر Gemini کلید/بودجه ندارد مستقیم OpenRouter.
  • اولویت ثابت: Gemini ← مدل‌های flash دیگر ← OpenRouter رایگان. اولین مدلی که خروجی
    قابل‌قبول (validate) داد برنده است و بقیه صدا زده نمی‌شوند.
  • نسخهٔ متنی همان پست به کانال بله هم می‌رود (src/bale.py، فقط ارسال).
هیچ خطایی از این ماژول بیرون نمی‌آید و انتشار اخبار را مختل نمی‌کند.
"""
import json
import re
import time
from datetime import datetime, timedelta

import requests

from . import bale, cloudflare_kv, gemini_budget, storage
from .bot import send_message
from .config import (
    CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, EDU_ENABLED, EDU_EVERY_HOURS,
    EDU_MAX_OUTPUT_TOKENS, EDU_MAX_TRIES_PER_SLOT, EDU_RECENT_KEEP, EDU_RECENT_PROMPT,
    GEMINI_API_KEY, OPENROUTER_API_KEY, OPENROUTER_APP_NAME, OPENROUTER_MAX_MODELS,
    OPENROUTER_MODELS, OPENROUTER_SITE_URL,
)
from .formatting import escape_html
from .gemini_parked import GEMINI_API_BASE, _invalidate_model, _resolve_model

KEY = "edu_state"
TG_LIMIT = 3900

# (کلید هشتگ، توضیح موضوع برای Gemini) — به‌ترتیب، چرخشی
CATEGORIES = [
    ("پرامپت_آماده", "A ready-to-copy prompt template with [placeholders] for a very common real-life or work task"),
    ("ترفند_چت‌بات", "One concrete technique that makes chatbot answers clearly better (role, examples, constraints, step-by-step, asking it to ask questions first...)"),
    ("اشتباه_رایج", "A common mistake people make when using AI assistants, why it hurts, and the corrected way"),
    ("بهره‌وری", "Using AI to save time on an everyday work task (emails, summaries, meeting notes, planning, spreadsheets)"),
    ("یادگیری_با_AI", "Using AI to learn faster (study plan, explain a topic at different levels, self-quiz, practice partner)"),
    ("تولید_تصویر", "How to write better prompts for AI image generation (anatomy of a good image prompt, with an example)"),
    ("برنامه‌نویسی_با_AI", "A practical way to use AI for coding (debugging prompt, code review, explaining code, writing tests)"),
    ("مفهوم_ساده", "One AI concept explained simply with an everyday analogy (token, context window, hallucination, RAG, fine-tuning, temperature...)"),
    ("کاربرد_روزمره", "A surprising but practical everyday use of AI (cooking, travel, shopping decisions, health-info literacy, home tasks)"),
    ("ورک‌فلو", "A 3-4 step workflow that chains several prompts together to finish a bigger task"),
    ("نویسندگی_و_محتوا", "Using AI for writing: rewriting in a tone, outlining, proofreading, summarizing, translation checks"),
    ("تصمیم_و_تحلیل", "Using AI to think better: compare options, pros and cons, brainstorm, critique your own plan, analyze data"),
]

PROMPT = """You write one short, genuinely useful educational post in PERSIAN (Farsi) for a Telegram channel about practical AI use.
The reader is a normal person (not necessarily technical) who wants to use AI tools better TODAY.

Topic type for this post: {topic}

Rules:
- Be concrete and specific. One clear idea, immediately usable. No fluff, no generic motivation.
- Do NOT invent statistics, studies, prices, release dates or specific product features. Refer to tools generically
  ("هر چت‌بات هوش مصنوعی مثل ChatGPT یا Gemini یا Claude") unless the idea is truly tool-independent.
- No URLs. No markdown symbols (no *, #, `, _). Plain text only.
- Persian text must be natural and fluent (not literal translation). Keep standard English terms (prompt, token...) as is.
- If the post contains a ready prompt, write it so the reader can copy it as-is, with [placeholders] in square brackets.
  The prompt itself may be Persian (preferred) or English.
- Make the title curious and specific (max 70 chars), not clickbait.
- Do NOT repeat or closely resemble these recent titles:
{recent}

Return ONLY a JSON object with exactly these keys:
{{
  "title": "string (Persian, max 70 chars)",
  "hook": "string — 1-2 sentences: why this matters / what problem it solves (max 220 chars)",
  "steps": ["2 to 5 short strings, each max 200 chars — how to do it"],
  "prompt": "string — a ready-to-copy prompt (max 700 chars), or empty string if the topic does not need one",
  "tip": "string — one short pro tip (max 180 chars)",
  "hashtags": ["1 to 2 Persian hashtag words without #, words joined by underscore"]
}}"""

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_MD_RE = re.compile(r"[*`#_]{1,}")
_PERSIAN_RE = re.compile(r"[؀-ۿ]")
_NUMS = "۱۲۳۴۵۶"


# ───────────── اسلات ۲ ساعته ─────────────

def local_now(now=None):
    now = now or datetime.utcnow()
    return now + timedelta(minutes=DISPLAY_TZ_OFFSET_MIN)


def current_slot(now=None):
    """(شناسهٔ اسلات، شمارهٔ ترتیبی روز-اسلات برای چرخش موضوع)"""
    loc = local_now(now)
    per_day = max(1, 24 // max(1, EDU_EVERY_HOURS))
    n = loc.hour // max(1, EDU_EVERY_HOURS)
    return f"{loc.date().isoformat()}#{n}", loc.toordinal() * per_day + n


def pick_category(ordinal: int):
    return CATEGORIES[ordinal % len(CATEGORIES)]


# ───────────── اعتبارسنجی و قالب‌بندی ─────────────

def _clean(text, limit):
    text = _URL_RE.sub("", str(text or ""))
    text = _MD_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _persian_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _PERSIAN_RE.match(c)) / len(letters)


def _words(text):
    return {w for w in re.findall(r"[\w؀-ۿ]+", (text or "").lower()) if len(w) > 2}


def too_similar(title, recent_titles, threshold=0.6) -> bool:
    a = _words(title)
    if not a:
        return False
    for t in recent_titles:
        b = _words(t)
        if b and len(a & b) / max(1, min(len(a), len(b))) >= threshold:
            return True
    return False


def _unwrap(data):
    """بعضی مدل‌ها JSON را در لیست یا کلید بیرونی می‌پیچند: [{...}] یا {"post": {...}}"""
    for _ in range(3):
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), None)
        elif isinstance(data, dict) and "title" not in data:
            inner = [v for v in data.values() if isinstance(v, (dict, list))]
            if len(inner) != 1:
                break
            data = inner[0]
        else:
            break
    return data if isinstance(data, dict) else None


def _step_text(item):
    """مرحله ممکن است رشته یا دیکشنری ({"step": "..."}) باشد"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in ("text", "step", "description", "title", "content"):
            if isinstance(item.get(k), str):
                return item[k]
        return next((v for v in item.values() if isinstance(v, str)), "")
    return ""


def check_post(data, recent_titles):
    """(post یا None، دلیل رد) — دلیل برای لاگ است تا بدانیم چرا خروجی مدل قبول نشد"""
    data = _unwrap(data)
    if data is None:
        return None, "not_an_object"
    title = _clean(data.get("title"), 80)
    hook = _clean(data.get("hook"), 240)
    tip = _clean(data.get("tip"), 200)
    prompt = str(data.get("prompt") or "")
    prompt = _URL_RE.sub("", prompt).replace("```", "").strip()[:750]
    raw_steps = data.get("steps") or []
    if isinstance(raw_steps, str):
        raw_steps = [x for x in re.split(r"\n+", raw_steps) if x.strip()]
    steps = [_clean(_step_text(x), 220) for x in raw_steps if _step_text(x).strip()][:5]
    raw_tags = data.get("hashtags") or []
    raw_tags = [raw_tags] if isinstance(raw_tags, str) else raw_tags
    tags = [re.sub(r"[^\w\u0600-\u06FF\u200c]", "", str(t)).strip("_") for t in raw_tags][:2]
    tags = [t for t in tags if t]

    if not title:
        return None, "missing_title"
    if not hook:
        return None, "missing_hook"
    if len(steps) < 2 and len(prompt) < 40:
        return None, f"too_thin(steps={len(steps)},prompt={len(prompt)})"
    ratio = _persian_ratio(title + " " + hook)
    if ratio < 0.5:
        return None, f"not_persian({ratio:.2f})"
    if too_similar(title, recent_titles):
        return None, "similar_to_recent_title"
    return {"title": title, "hook": hook, "steps": steps, "prompt": prompt, "tip": tip, "hashtags": tags}, ""


def validate(data, recent_titles):
    """خروجی مدل را پاک‌سازی و اعتبارسنجی می‌کند. None = بی‌کیفیت"""
    return check_post(data, recent_titles)[0]


def _validate_logged(data, recent_titles):
    """مثل validate ولی دلیل رد را چاپ می‌کند (برای عیب‌یابی لاگ Actions)"""
    post, why = check_post(data, recent_titles)
    if post is None:
        sample = ""
        d = _unwrap(data)
        if d:
            sample = f" title={str(d.get('title', ''))[:50]!r}"
        print(f"[EDU] output rejected: {why}{sample}")
    return post


def format_html(post: dict, category_tag: str) -> str:
    e = escape_html
    div = "─" * 20

    def build(prompt_text):
        parts = [f"🎓 <b>{e(post['title'])}</b>", div, "", e(post["hook"])]
        if post["steps"]:
            parts += ["", "📌 <b>چطور؟</b>"]
            parts += [f"{_NUMS[i]}. {e(st)}" for i, st in enumerate(post["steps"])]
        if prompt_text:
            parts += ["", "📋 <b>پرامپت آماده</b> (روی متن بزن تا کپی شود)", f"<pre>{e(prompt_text)}</pre>"]
        if post["tip"]:
            parts += ["", f"💡 <b>نکته:</b> {e(post['tip'])}"]
        tags = list(dict.fromkeys(["آموزش_هوش_مصنوعی", category_tag] + post["hashtags"]))
        parts += ["", div, "🏷️ " + " ".join("#" + e(t) for t in tags), "<i>✍️ تولیدشده با هوش مصنوعی</i>"]
        return "\n".join(parts)

    text = build(post["prompt"])
    if len(text) > TG_LIMIT:  # دفاع آخر؛ با سقف فیلدها تقریباً هرگز رخ نمی‌دهد
        text = build(post["prompt"][:300])
    return text[:TG_LIMIT]


def format_plain(post: dict, category_tag: str) -> str:
    """نسخهٔ متن ساده برای بله"""
    lines = [f"🎓 {post['title']}", "", post["hook"]]
    if post["steps"]:
        lines += ["", "📌 چطور؟"] + [f"{_NUMS[i]}. {s}" for i, s in enumerate(post["steps"])]
    if post["prompt"]:
        lines += ["", "📋 پرامپت آماده:", post["prompt"]]
    if post["tip"]:
        lines += ["", f"💡 نکته: {post['tip']}"]
    tags = ["آموزش_هوش_مصنوعی", category_tag] + post["hashtags"]
    lines += ["", "🏷️ " + " ".join("#" + t for t in dict.fromkeys(tags)), "✍️ تولیدشده با هوش مصنوعی"]
    return "\n".join(lines)[:4000]


# ───────────── Gemini ─────────────

def _extract_json(text: str):
    text = (text or "").strip()
    if "```" in text:
        chunks = [c for c in text.split("```") if c.strip()]
        text = chunks[0] if chunks else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return json.loads(text[start:end + 1])


# ───────────── OpenRouter (فقط مدل رایگان، fallback) ─────────────

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# اگر فهرست زندهٔ مدل‌ها نیامد، این‌ها امتحان می‌شوند (شناسه‌ها ممکن است عوض شوند).
DEFAULT_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
]

_FREE_BAD_PARTS = (
    "vision", "image", "audio", "embed", "rerank", "tts", "whisper",
    "moderation", "guard", "safety",
)


def _model_rank(model_id: str) -> int:
    """مدل‌های قوی‌تر/شناخته‌شده‌تر اول امتحان می‌شوند"""
    mid = model_id.lower()
    order = ("llama-3.3-70b", "gemma-4", "gemma-3", "qwen", "nemotron",
             "mistral", "deepseek", "llama", "inkling")
    for i, key in enumerate(order):
        if key in mid:
            return i
    return len(order)


def _pick_free_models() -> list:
    """فهرست زندهٔ مدل‌های رایگان OpenRouter (fallback: کش، سپس فهرست پیش‌فرض)"""
    if OPENROUTER_MODELS:
        return OPENROUTER_MODELS[:max(1, OPENROUTER_MAX_MODELS)]

    free = []
    try:
        resp = requests.get(f"{OPENROUTER_BASE}/models", timeout=20)
        if resp.status_code == 200:
            for model in (resp.json().get("data") or []):
                mid = str(model.get("id") or "")
                if not mid or any(part in mid.lower() for part in _FREE_BAD_PARTS):
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
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] OpenRouter model list failed: {type(e).__name__}")

    if free:
        free = sorted(set(free), key=_model_rank)
        try:
            cloudflare_kv.put("openrouter_free_models", free[:10])
        except Exception:  # noqa: BLE001
            pass
        return free[:max(1, OPENROUTER_MAX_MODELS)]

    cached = cloudflare_kv.get("openrouter_free_models")
    if isinstance(cached, list) and cached:
        return [m for m in cached if isinstance(m, str)][:max(1, OPENROUTER_MAX_MODELS)]

    return DEFAULT_FREE_MODELS[:max(1, OPENROUTER_MAX_MODELS)]


def _call_openrouter(model: str, prompt: str, timeout: float = None):
    """یک درخواست به مدل رایگان OpenRouter؛ JSON معتبر یا None"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": EDU_MAX_OUTPUT_TOKENS,
        "temperature": 0.9,
    }
    try:
        resp = requests.post(f"{OPENROUTER_BASE}/chat/completions",
                             headers=headers, json=payload, timeout=timeout or CALL_TIMEOUT)
        if resp.status_code != 200:
            print(f"[EDU] OpenRouter {model}: HTTP {resp.status_code}")
            return None
        choices = resp.json().get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else ""
        return _extract_json(text)
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] OpenRouter {model}: {type(e).__name__}")
        return None


def _openrouter_generate(prompt: str, good=None, left=None):
    """
    اولین مدل رایگان OpenRouter که خروجی «قابل‌قبول» بدهد برنده است؛ بقیه صدا زده نمی‌شوند.
    good(data) → آیا خروجی قابل‌قبول است؟  left() → ثانیهٔ باقی‌ماندهٔ بودجهٔ زمانی.
    """
    if not OPENROUTER_API_KEY:
        print("[EDU] OpenRouter key missing — fallback skipped")
        return None
    for model in _pick_free_models():
        if left is not None and left() <= 5:
            break
        timeout = CALL_TIMEOUT if left is None else min(CALL_TIMEOUT, max(5, left()))
        data = _call_openrouter(model, prompt, timeout)
        if data is None:
            continue
        if good is not None and not good(data):
            print(f"[EDU] OpenRouter {model}: output rejected — next model")
            continue
        _RUN.update(provider="openrouter", model=model)
        print(f"[EDU] OpenRouter success: {model}")
        return data
    return None


RETRY_STATUS = (0, 429, 500, 502, 503, 504)  # خطاهای موقتی سمت سرور/شبکه
BACKOFF_SECONDS = (6, 15)                     # فاصلهٔ تلاش‌های مجدد روی همان مدل
MAX_TOTAL_SECONDS = 150                       # سقف کل زمان ساخت یک پست (Gemini + OpenRouter، همهٔ تلاش‌ها)
CALL_TIMEOUT = 40
_sleep = time.sleep
_RUN = {"gemini_calls": 0, "provider": "", "model": ""}  # گزارش آخرین generate


def _call(model: str, payload: dict, timeout: float = CALL_TIMEOUT):
    """یک درخواست؛ (dict یا None، کد وضعیت). کد ۰ = خطای شبکه/timeout/پاسخ نامعتبر"""
    try:
        resp = requests.post(f"{GEMINI_API_BASE}/models/{model}:generateContent",
                             params={"key": GEMINI_API_KEY}, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None, resp.status_code
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _extract_json(text), 200
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] Gemini error: {type(e).__name__}")
        return None, 0


def _alternatives(exclude: str, limit: int = 2) -> list:
    """مدل‌های flash دیگر (جدیدترین اول) برای وقتی مدل اصلی شلوغ/خراب است"""
    try:
        resp = requests.get(f"{GEMINI_API_BASE}/models", params={"key": GEMINI_API_KEY, "pageSize": 200}, timeout=15)
        if resp.status_code != 200:
            return []
        names = [m.get("name", "").replace("models/", "") for m in resp.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        bad = ("thinking", "lite", "8b", "embedding", "aqa", "tts", "image", "live", "vision", "audio", "robotics")

        def ver(n):
            m = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
            return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)

        flash = sorted((n for n in names if "flash" in n and n != exclude and not any(x in n for x in bad)),
                       key=ver, reverse=True)
        return flash[:limit]
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] model list failed: {type(e).__name__}")
        return []


def generate(category_desc: str, recent_titles: list, accept=None, use_gemini: bool = True):
    """
    ساخت JSON خام. ترتیب اولویت (ثابت):
      ۱) Gemini (مدل اصلی؛ روی خطای موقتی چند بار با فاصله)
      ۲) مدل‌های flash دیگر Gemini
      ۳) مدل‌های رایگان OpenRouter (به ترتیب رتبه)
    اولین مدلی که خروجی «قابل‌قبول» بدهد برنده است و بقیه اصلاً صدا زده نمی‌شوند.
    «قابل‌قبول» یعنی accept(data) (در _run همان validate) None برنگرداند. خروجی ناخوانا یا
    بی‌کیفیت یعنی مدل بعدی. کل زمان از MAX_TOTAL_SECONDS بیشتر نمی‌شود.
    """
    _RUN.update(gemini_calls=0, provider="", model="")
    recent = "\n".join(f"- {t}" for t in recent_titles[-max(1, EDU_RECENT_PROMPT):]) or "(none yet)"
    prompt = PROMPT.format(topic=category_desc, recent=recent)
    t0 = time.monotonic()

    def left():
        return MAX_TOTAL_SECONDS - (time.monotonic() - t0)

    def good(data):
        if data is None:
            return False
        if accept is None:
            return True
        try:
            return accept(data) is not None
        except Exception:  # noqa: BLE001
            return False

    if use_gemini and GEMINI_API_KEY:
        model = _resolve_model()
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.9,
                "maxOutputTokens": EDU_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
            },
        }
        code = 0
        for delay in (0,) + BACKOFF_SECONDS:
            if left() <= 5:
                break
            if delay:
                _sleep(delay)
            _RUN["gemini_calls"] += 1
            data, code = _call(model, payload, min(CALL_TIMEOUT, max(5, left())))
            if code == 200:
                if good(data):
                    _RUN.update(provider="gemini", model=model)
                    return data
                print(f"[EDU] Gemini {model}: output rejected")
                break  # جواب داد ولی قابل‌قبول نبود؛ تکرار روی همان مدل فایده ندارد
            print(f"[EDU] Gemini {model}: HTTP {code}")
            if code not in RETRY_STATUS:
                break

        if code in (400, 404):
            _invalidate_model()  # مدل منقضی → اجرای بعد مدل تازه پیدا کند
        elif code in RETRY_STATUS or code == 200:
            for alt in _alternatives(model):
                if left() <= 5:
                    break
                _RUN["gemini_calls"] += 1
                data, c = _call(alt, payload, min(CALL_TIMEOUT, max(5, left())))
                print(f"[EDU] Gemini fallback {alt}: HTTP {c}")
                if c == 200 and good(data):
                    try:
                        cloudflare_kv.put("gemini_model", alt)  # اجراهای بعد از همین شروع کنند
                    except Exception:  # noqa: BLE001
                        pass
                    _RUN.update(provider="gemini", model=alt)
                    return data
        print("[EDU] Gemini unavailable — trying free OpenRouter models")
    else:
        print("[EDU] Gemini skipped — trying free OpenRouter models")
    return _openrouter_generate(prompt, good=good, left=left)


# ───────────── نقطهٔ ورود ─────────────

def maybe_post(state: dict, now=None) -> dict:
    """اگر وقتش است یک پست آموزشی بساز و منتشر کن. state را برمی‌گرداند (تغییر نمی‌دهد)."""
    try:
        _run(state, now)
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] unexpected error: {type(e).__name__}: {e}")
    return state


def _run(state, now):
    if not EDU_ENABLED or state.get("edu_enabled") is False:
        return
    if state.get("paused"):
        return
    if not CHANNEL_ID:
        return

    slot, ordinal = current_slot(now)
    est = cloudflare_kv.get(KEY, {})
    est = est if isinstance(est, dict) else {}
    if est.get("last_slot") == slot:
        return
    tries = est.get("tries") if est.get("tries_slot") == slot else 0
    if (tries or 0) >= EDU_MAX_TRIES_PER_SLOT:
        return

    # Gemini فقط وقتی کلید دارد و بودجه‌اش اجازه می‌دهد؛ در غیر این صورت مستقیم OpenRouter
    gemini_ok = bool(GEMINI_API_KEY) and gemini_budget.can_call()
    if not gemini_ok and not OPENROUTER_API_KEY:
        return

    tag, desc = pick_category(ordinal)
    recent = [r.get("title", "") for r in est.get("recent", []) if isinstance(r, dict)]

    raw = generate(desc, recent, accept=lambda r: _validate_logged(r, recent), use_gemini=gemini_ok)
    if gemini_ok and _RUN.get("gemini_calls"):
        gemini_budget.mark_called()
    post = validate(_unwrap(raw), recent) if raw is not None else None

    if post is None:
        est.update({"tries_slot": slot, "tries": (tries or 0) + 1})
        cloudflare_kv.put(KEY, est)
        print(f"[EDU] No valid post for slot {slot} (try {(tries or 0) + 1}/{EDU_MAX_TRIES_PER_SLOT})")
        return

    result = send_message(CHANNEL_ID, format_html(post, tag))
    if not (result and result.get("ok")):
        est.update({"tries_slot": slot, "tries": (tries or 0) + 1})
        cloudflare_kv.put(KEY, est)
        print(f"[EDU] Telegram send failed: {result}")
        return

    est["last_slot"] = slot
    est["tries_slot"], est["tries"] = slot, 0
    est["recent"] = (est.get("recent", []) + [{"title": post["title"], "tag": tag, "via": f"{_RUN.get('provider', '')}/{_RUN.get('model', '')}", "at": datetime.utcnow().isoformat() + "Z"}])[-EDU_RECENT_KEEP:]
    cloudflare_kv.put(KEY, est)
    print(f"[EDU] Published: {post['title'][:50]} ({tag}) via {_RUN.get('provider') or '?'}/{_RUN.get('model') or '?'}")

    # لاگ انتشار (برای /stats) — خطا نباید چیزی را خراب کند
    try:
        log = storage.load_published()
        log.append({"id": f"edu-{slot}", "title_fa": post["title"], "source": "آموزش AI", "category": "edu",
                    "published": True, "published_at": datetime.utcnow().isoformat() + "Z"})
        storage.save_published(log)
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] log failed: {type(e).__name__}")

    bale.reset()
    bale.send_text(format_plain(post, tag))
