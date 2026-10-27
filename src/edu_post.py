"""
پست آموزشی هوش مصنوعی — هر ۲ ساعت یک پست کاربردی با Gemini (۱۲ پست در روز)

چطور کار می‌کند:
  • پایپ‌لاین هر ساعت اجرا می‌شود؛ روز به «اسلات‌های ۲ ساعته» (به وقت نمایش) تقسیم شده است.
    اولین اجرایی که در یک اسلات جدید می‌رسد، یک پست می‌سازد و منتشر می‌کند.
    اگر ساخت شکست بخورد (Gemini خطا داد / خروجی بی‌کیفیت بود)، اجرای بعدی (ساعت بعد، همان
    اسلات) یک‌بار دیگر تلاش می‌کند؛ بعد از ۲ شکست آن اسلات رد می‌شود. پست ناقص هرگز منتشر نمی‌شود.
  • موضوع: ۱۲ دستهٔ ثابت به‌صورت چرخشی (پرامپت آماده، اشتباه رایج، مفهوم ساده، تولید تصویر، …)
    تا در طول روز تنوع داشته باشد. عنوان‌های اخیر به Gemini داده می‌شود تا تکرار نکند و عنوان
    مشابه عنوان‌های اخیر رد می‌شود.
  • قالب‌بندی را خودِ کد می‌سازد (نه Gemini): خروجی JSON ساختاریافته، همه‌چیز escape می‌شود،
    پرامپت آماده داخل بلوک <pre> می‌آید تا با یک لمس کپی شود.
  • کلید خاموش/روشن: دستور /edu on|off در ربات (state.edu_enabled) یا EDU_POST_ENABLED=false.
  • بودجهٔ Gemini از src/gemini_budget.py رعایت می‌شود.
  • نسخهٔ متنی همان پست به کانال بله هم می‌رود (src/bale.py، فقط ارسال).
هیچ خطایی از این ماژول بیرون نمی‌آید و انتشار اخبار را مختل نمی‌کند.
"""
import json
import re
from datetime import datetime, timedelta

import requests

from . import bale, cloudflare_kv, gemini_budget, storage
from .bot import send_message
from .config import (
    CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, EDU_ENABLED, EDU_EVERY_HOURS,
    EDU_MAX_TRIES_PER_SLOT, EDU_RECENT_KEEP, GEMINI_API_KEY,
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


def validate(data, recent_titles):
    """خروجی Gemini را پاک‌سازی و اعتبارسنجی می‌کند. None = بی‌کیفیت"""
    if not isinstance(data, dict):
        return None
    title = _clean(data.get("title"), 80)
    hook = _clean(data.get("hook"), 240)
    tip = _clean(data.get("tip"), 200)
    prompt = str(data.get("prompt") or "")
    prompt = _URL_RE.sub("", prompt).replace("```", "").strip()[:750]
    steps = [_clean(s, 220) for s in (data.get("steps") or []) if isinstance(s, str) and s.strip()][:5]
    tags = [re.sub(r"[^\w؀-ۿ‌]", "", str(t)).strip("_") for t in (data.get("hashtags") or [])][:2]
    tags = [t for t in tags if t]

    if not title or not hook:
        return None
    if len(steps) < 2 and len(prompt) < 40:
        return None
    if _persian_ratio(title + " " + hook) < 0.5:
        return None
    if too_similar(title, recent_titles):
        return None
    return {"title": title, "hook": hook, "steps": steps, "prompt": prompt, "tip": tip, "hashtags": tags}


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


def generate(category_desc: str, recent_titles: list):
    """یک درخواست به Gemini → JSON خام (dict) یا None"""
    model = _resolve_model()
    recent = "\n".join(f"- {t}" for t in recent_titles[-15:]) or "(none yet)"
    payload = {
        "contents": [{"parts": [{"text": PROMPT.format(topic=category_desc, recent=recent)}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 2048, "responseMimeType": "application/json"},
    }
    try:
        resp = requests.post(f"{GEMINI_API_BASE}/models/{model}:generateContent",
                             params={"key": GEMINI_API_KEY}, json=payload, timeout=60)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _extract_json(text)
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        print(f"[EDU] Gemini HTTP {code}")
        if code in (400, 404):
            _invalidate_model()  # مدل منقضی → اجرای بعد مدل تازه پیدا کند
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] Gemini error: {type(e).__name__}")
        return None


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
    if not GEMINI_API_KEY or not CHANNEL_ID:
        return

    slot, ordinal = current_slot(now)
    est = cloudflare_kv.get(KEY, {})
    est = est if isinstance(est, dict) else {}
    if est.get("last_slot") == slot:
        return
    tries = est.get("tries") if est.get("tries_slot") == slot else 0
    if (tries or 0) >= EDU_MAX_TRIES_PER_SLOT:
        return

    if not gemini_budget.can_call():
        return

    tag, desc = pick_category(ordinal)
    recent = [r.get("title", "") for r in est.get("recent", []) if isinstance(r, dict)]

    raw = generate(desc, recent)
    gemini_budget.mark_called()
    post = validate(raw, recent) if raw is not None else None

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
    est["recent"] = (est.get("recent", []) + [{"title": post["title"], "tag": tag, "at": datetime.utcnow().isoformat() + "Z"}])[-EDU_RECENT_KEEP:]
    cloudflare_kv.put(KEY, est)
    print(f"[EDU] Published: {post['title'][:50]} ({tag})")

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
