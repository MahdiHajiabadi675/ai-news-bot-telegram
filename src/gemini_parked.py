"""
Gemini (کنار گذاشته‌شده) — این ماژول در پایپ‌لاین استفاده نمی‌شود

Gemini فعلاً هیچ نقشی در ترجمه و مسیر انتشار ندارد (ترجمه: src/translator.py،
طبقه‌بندی: src/classifier.py). این فایل فقط نقطهٔ شروعِ ایدهٔ بعدی است:
  • پیدا کردن مدل موجود و کش آن در KV (_resolve_model)
  • یک درخواست JSON به Gemini (_try_gemini_once)
  • بودجهٔ درخواست‌ها در src/gemini_budget.py

پرامپت فعلی عمداً فقط «تحلیل» می‌خواهد و هیچ فیلد ترجمه‌ای ندارد.
"""
import json
import re
import requests
from .config import GEMINI_API_KEY, GEMINI_MODEL
from . import cloudflare_kv

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODEL_CACHE = None

PROMPT = """You are an AI news analyst. Analyze EACH article below and output a JSON array.
Each element has:
- i: integer — the 1-based index of the article from the input list
- relevant: boolean — is this about AI models, LLMs, AI tools, AI research, or major AI industry news?
- importance: "high" | "medium" | "low"
- confidence: "confirmed" | "likely" | "rumor"
- category: "model_release" | "benchmark" | "funding" | "research" | "policy" | "tool_release" | "industry" | "other"

Output ONLY a JSON array, no other text.

Articles:
"""


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


def _build_articles_text(articles: list) -> str:
    text = ""
    for i, a in enumerate(articles):
        text += f"\n--- Article {i+1} ---\n"
        text += f"Source: {a['source']} ({a['lang']}) | Trusted: {a.get('trusted', False)}\n"
        text += f"Title: {a['title']}\n"
        text += f"Summary: {a['summary'][:800]}\n"
    return text
