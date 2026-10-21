"""
Analyzer — تحلیل اخبار با Gemini (بودجه‌دار)

سیاست مصرف Gemini:
  • در هر اجرا حداکثر **یک** درخواست به Gemini زده می‌شود (batch).
  • بین دو درخواست حداقل GEMINI_MIN_INTERVAL_MIN دقیقه فاصله است.
  • سقف روزانه GEMINI_MAX_CALLS_PER_DAY.
  • خبرهای فارسی هرگز به Gemini نمی‌روند (پردازش محلی).
  • خبرهای بیشتر از GEMINI_BATCH_SIZE → ترجمه ساده (Google Translate).
  • اگر Gemini در دسترس نباشد → همه با ترجمه ساده و برچسب fallback.

هیچ‌وقت روی چند مدل loop نمی‌زند و هیچ‌وقت بیش از یک درخواست در هر اجرا نمی‌فرستد.
"""
import json
import re
import requests
from .config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BATCH_SIZE
from . import gemini_budget, cloudflare_kv

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODEL_CACHE = None

PROMPT = """You are an AI news analyst for a Persian Telegram channel about AI models.

Analyze EACH article below. For each one output a JSON object with these fields:
- i: integer — the 1-based index of the article from the input list
- relevant: boolean — is this about AI models, LLMs, AI tools, AI research, or major AI industry news?
- title_fa: string — compelling Persian title (max 100 chars)
- summary_fa: string — engaging Persian summary (2-3 sentences, ~200 chars)
- importance: "high" | "medium" | "low"
- confidence: "confirmed" | "likely" | "rumor"
- category: "model_release" | "benchmark" | "funding" | "research" | "policy" | "tool_release" | "industry" | "other"
- hashtags: array of 3-5 hashtags (mix Persian & English, no # prefix)
- admin_question: string — if confidence != "confirmed", a short Persian question for the channel admin

Output ONLY a JSON array, no other text.

Articles:
"""


def analyze_articles(articles: list):
    """تحلیل اخبار — حداکثر یک درخواست به Gemini، بقیه ترجمه ساده

    • اخبار فارسی (lang == "fa") هرگز به Gemini فرستاده نمی‌شوند و کاملاً
      به‌صورت محلی پردازش می‌شوند.
    • برمی‌گرداند: (analyzed_items, meta)
    """
    meta = {
        "gemini_used": False,
        "gemini_failed": False,
        "skipped_budget": False,
        "gemini_batch": 0,
        "fallback_count": 0,
        "persian_local": 0,
    }

    if not articles:
        return [], meta

    # ── جدا کردن فارسی و انگلیسی ──
    fa_articles = [a for a in articles if a.get("lang") == "fa"]
    en_articles = [a for a in articles if a.get("lang") != "fa"]

    analyzed = []

    # اخبار فارسی: بدون هیچ درخواستی به Gemini
    if fa_articles:
        local = _basic_scoring(fa_articles)
        analyzed += local
        meta["persian_local"] = len(fa_articles)
        meta["fallback_count"] += len(local)
        print(f"[INFO] {len(fa_articles)} Persian article(s) — local processing (no Gemini)")

    if not en_articles:
        return analyzed, meta

    if not GEMINI_API_KEY:
        print("[INFO] No GEMINI_API_KEY — simple translation")
        fallback = _basic_scoring(en_articles)
        meta["fallback_count"] += len(fallback)
        return analyzed + fallback, meta

    if not gemini_budget.can_call():
        print("[INFO] Gemini budget exhausted — simple translation")
        meta["skipped_budget"] = True
        fallback = _basic_scoring(en_articles)
        meta["fallback_count"] += len(fallback)
        return analyzed + fallback, meta

    batch = en_articles[:GEMINI_BATCH_SIZE]
    rest = en_articles[GEMINI_BATCH_SIZE:]
    meta["gemini_batch"] = len(batch)

    print(f"[INFO] Asking Gemini about {len(batch)} article(s) (1 request)")
    results = _try_gemini_once(batch)

    if not results:
        print("[WARN] Gemini failed — simple translation for all")
        meta["gemini_failed"] = True
        fallback = _basic_scoring(en_articles)
        meta["fallback_count"] += len(fallback)
        return analyzed + fallback, meta

    gemini_budget.mark_called()
    meta["gemini_used"] = True
    analyzed += _merge(batch, results)

    if rest:
        print(f"[INFO] {len(rest)} extra article(s) beyond batch — simple translation")
        fallback = _basic_scoring(rest)
        meta["fallback_count"] += len(fallback)
        analyzed += fallback

    return analyzed, meta


def _merge(articles: list, results: list) -> list:
    """ادغام نتایج Gemini با اخبار (مطابقت با ایندکس، fallback به ترتیب)"""
    by_index = {}
    for pos, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        idx = r.get("i")
        if isinstance(idx, int) and 1 <= idx <= len(articles):
            by_index[idx - 1] = r
        else:
            by_index[pos] = r

    analyzed = []
    for i, a in enumerate(articles):
        r = by_index.get(i)
        if not r or not r.get("relevant", False):
            continue

        a.update({
            "id": a.get("id", a.get("hash", "")),
            "title_fa": r.get("title_fa", a["title"]),
            "summary_fa": r.get("summary_fa", ""),
            "importance": r.get("importance", "medium"),
            "confidence": r.get("confidence", "likely"),
            "category": r.get("category", "other"),
            "hashtags": r.get("hashtags", ["هوش_مصنوعی"]),
            "admin_question": r.get("admin_question", ""),
            "status": "analyzed"
        })
        analyzed.append(a)

    print(f"[INFO] Analyzed: {len(analyzed)} relevant from {len(articles)}")
    return analyzed


def _resolve_model() -> str:
    """مدل Gemini — از کش KV استفاده می‌کنه تا فقط یک بار کشف بشه"""
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return _MODEL_CACHE

    cached = cloudflare_kv.get("gemini_model")
    if cached:
        _MODEL_CACHE = cached
        return cached

    model = _discover_model() or GEMINI_MODEL
    try:
        cloudflare_kv.put("gemini_model", model)
    except Exception:
        pass
    _MODEL_CACHE = model
    print(f"[INFO] Gemini model selected: {model}")
    return model


def _invalidate_model():
    """مدل کش‌شده رو پاک کن تا اجرای بعدی مدل تازه کشف کنه"""
    global _MODEL_CACHE
    _MODEL_CACHE = None
    try:
        cloudflare_kv.put("gemini_model", "")
    except Exception:
        pass


def _discover_model():
    """بهترین مدل موجود با متد generateContent را پیدا می‌کند"""
    try:
        resp = requests.get(
            f"{GEMINI_API_BASE}/models",
            params={"key": GEMINI_API_KEY, "pageSize": 200},
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        usable = [
            m.get("name", "").replace("models/", "")
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        if not usable:
            return None

        if GEMINI_MODEL in usable:
            return GEMINI_MODEL

        bad = ("thinking", "lite", "8b", "embedding", "aqa", "tts", "image", "live", "vision")

        def ver(n):
            m = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
            return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)

        flash = [n for n in usable if "flash" in n and not any(b in n for b in bad)]
        pool = flash or [n for n in usable if not any(b in n for b in bad)] or usable
        pool.sort(key=ver, reverse=True)
        return pool[0]
    except Exception as e:
        print(f"[WARN] Gemini model list failed: {e}")
        return None


def _try_gemini_once(articles: list):
    """فقط و فقط یک درخواست به Gemini"""
    if not articles:
        return None

    model = _resolve_model()
    payload = {
        "contents": [{"parts": [{"text": PROMPT + _build_articles_text(articles)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json"
        }
    }

    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    try:
        resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        print(f"[INFO] Gemini succeeded ({model})")
        return _normalize_results(json.loads(text))
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        print(f"[WARN] Gemini ({model}): HTTP {code}")
        if code in (400, 404):
            # مدل منقضی شده — اجرای بعدی مدل تازه انتخاب می‌کنه
            _invalidate_model()
        return None
    except Exception as e:
        print(f"[WARN] Gemini ({model}): {e}")
        return None


def _normalize_results(results):
    """خروجی Gemini ممکنه آرایه، دیکشنری یا تک‌آبجکت باشه"""
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        for v in results.values():
            if isinstance(v, list):
                return v
        return [results]
    return []


def _basic_scoring(articles: list):
    """ترجمه ساده + برچسب fallback (بدون هیچ درخواستی به Gemini)"""
    analyzed = []
    for a in articles:
        if a["lang"] == "fa":
            title_fa = a["title"]
            summary_fa = a["summary"][:400]
        else:
            from .fallback_translator import translate_with_fallback
            translated = translate_with_fallback(a["title"], a.get("summary", "") or "")
            if translated:
                title_fa = translated.get("title_fa", a["title"])
                summary_fa = translated.get("summary_fa", a["summary"][:400])
            else:
                title_fa = a["title"]
                summary_fa = a["summary"][:400]

        analyzed.append({
            "id": a.get("id", a.get("hash", "")),
            "source": a["source"],
            "lang": a["lang"],
            "trusted": a.get("trusted", False),
            "title": a["title"],
            "link": a["link"],
            "summary": a["summary"],
            "title_fa": title_fa,
            "summary_fa": summary_fa,
            "importance": "medium",
            "confidence": "likely",
            "category": "other",
            "hashtags": ["هوش_مصنوعی", "اخبار_AI"],
            "admin_question": "",
            "status": "analyzed",
            "fallback": True
        })
    return analyzed


def _build_articles_text(articles: list) -> str:
    text = ""
    for i, a in enumerate(articles):
        text += f"\n--- Article {i+1} ---\n"
        text += f"Source: {a['source']} ({a['lang']}) | Trusted: {a.get('trusted', False)}\n"
        text += f"Title: {a['title']}\n"
        text += f"Summary: {a['summary'][:800]}\n"
    return text
