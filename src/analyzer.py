"""
Analyzer — تحلیل اخبار با LLM
اگه LLM در دسترس نباشه → فقط ترجمه + انتشار خودکار
"""
import json
import requests
from .config import GEMINI_API_KEY, GEMINI_MODEL, GROQ_API_KEY

# === Gemini ===
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
FALLBACK_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash"]

# === Groq (جایگزین رایگان) ===
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

PROMPT = """You are an AI news analyst for a Persian Telegram channel about AI models.

Analyze EACH article below. For each one output a JSON object with these fields:
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

    # اول Gemini را امتحان کن
    results = _try_gemini(articles)

    # اگه Gemini fail شد → Groq
    if not results and GROQ_API_KEY:
        print("[INFO] Gemini failed, trying Groq...")
        results = _try_groq(articles)

    # اگه هر دو fail شد → basic scoring (فقط ترجمه)
    if not results:
        print("[WARN] All LLMs failed - using basic scoring (translate only)")
        return _basic_scoring(articles)

    # ادغام نتایج
    analyzed = []
    for i, a in enumerate(articles):
        if i >= len(results):
            break
        r = results[i]
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

def _try_gemini(articles: list):
    """تلاش برای Gemini"""
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

    for model in [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=30)
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            results = json.loads(text)
            print(f"[INFO] Gemini {model} succeeded")
            return results
        except Exception as e:
            print(f"[WARN] Gemini {model}: {e}")
            continue
    return None

def _try_groq(articles: list):
    """تلاش برای Groq (جایگزین رایگان)"""
    if not GROQ_API_KEY:
        return None

    articles_text = _build_articles_text(articles)
    prompt = PROMPT + articles_text

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"}
            },
            timeout=60
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        results = json.loads(text)
        print(f"[INFO] Groq {GROQ_MODEL} succeeded")
        return results
    except Exception as e:
        print(f"[WARN] Groq: {e}")
        return None

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
            "hashtags": ["AI"],
            "admin_question": "",
            "status": "analyzed"
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
