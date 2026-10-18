"""
Analyzer — تحلیل اخبار با LLM
اگه LLM در دسترس نباشه → فقط ترجمه + انتشار خودکار
"""
import json
import re
import requests
from .config import GEMINI_API_KEY, GEMINI_MODEL

# === Gemini ===
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_working_model = None

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

def analyze_articles(articles: list) -> list:
    if not articles:
        return []

    # فقط یک درخواست به Gemini
    results = _try_gemini_once(articles)

    # اگه Gemini fail شد → basic scoring (بدون هیچ LLM دیگه‌ای)
    if not results:
        print("[WARN] Gemini failed - using basic scoring (translate only)")
        return _basic_scoring(articles)

    # ادغام نتایج (مطابقت با ایندکس Gemini، با fallback به ترتیب)
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
        if not r:
            continue
        if not r.get("relevant", False):
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

def _resolve_model():
    """مدل موجود رو از ListModels پیدا می‌کنه (وقتی مدل تنظیم‌شده منقضی شده باشه)"""
    global _working_model
    if _working_model:
        return _working_model
    try:
        resp = requests.get(
            f"{GEMINI_API_BASE}/models",
            params={"key": GEMINI_API_KEY, "pageSize": 200},
            timeout=15,
        )
        if resp.status_code == 200:
            usable = [
                m.get("name", "").replace("models/", "")
                for m in resp.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            if GEMINI_MODEL in usable:
                _working_model = GEMINI_MODEL
                return _working_model
            bad = ("thinking", "lite", "8b", "embedding", "aqa", "tts", "image", "live", "vision")

            def ver(n):
                m = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
                return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)

            flash = [n for n in usable if "flash" in n and not any(b in n for b in bad)]
            pool = flash or [n for n in usable if not any(b in n for b in bad)] or usable
            if pool:
                pool.sort(key=ver, reverse=True)
                _working_model = pool[0]
                print(f"[INFO] Gemini model auto-selected: {_working_model}")
                return _working_model
    except Exception as e:
        print(f"[WARN] Gemini model list failed: {e}")
    _working_model = GEMINI_MODEL
    return _working_model

def _try_gemini_once(articles: list):
    """فقط یک درخواست به Gemini"""
    if not GEMINI_API_KEY:
        return None

    articles_text = _build_articles_text(articles)
    payload = {
        "contents": [{"parts": [{"text": PROMPT + articles_text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json"
        }
    }

    model = _resolve_model()
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    try:
        resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        results = json.loads(text)
        print(f"[INFO] Gemini succeeded ({model})")
        return _normalize_results(results)
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
    """فقط ترجمه + انتشار خودکار (بدون LLM)"""
    analyzed = []
    for a in articles:
        # اگه فارسیه → مستقیم، اگه انگلیسیه → ترجمه با Google Translate
        if a["lang"] == "fa":
            title_fa = a["title"]
            summary_fa = a["summary"][:400]
        else:
            from .fallback_translator import translate_with_fallback
            translated = translate_with_fallback(a["title"], a["summary"])
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
