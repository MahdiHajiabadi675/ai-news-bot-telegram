import json
import requests
from .config import GEMINI_API_KEY, GEMINI_MODEL

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Fallback models in order
FALLBACK_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash"]

PROMPT = """You are an AI news analyst for a Persian Telegram channel about AI models.

Analyze EACH article below. For each one output a JSON object with these fields:
- relevant: boolean — is this about AI models, LLMs, AI tools, AI research, or major AI industry news?
- title_fa: string — compelling Persian title (max 100 chars)
- summary_fa: string — engaging Persian summary (2-3 sentences, ~200 chars)
- importance: "high" | "medium" | "low"
  * high = major model release, breakthrough, billion-dollar funding, big policy
  * medium = notable update, research paper, feature launch
  * low = minor news, opinion pieces, incremental updates
- confidence: "confirmed" | "likely" | "rumor"
  * confirmed = from official source (OpenAI, Google, Meta, etc.)
  * likely = reliable reporting but not official
  * rumor = unverified leak or speculation
- category: "model_release" | "benchmark" | "funding" | "research" | "policy" | "tool_release" | "industry" | "other"
- hashtags: array of 3-5 hashtags (mix Persian & English, no # prefix)
- admin_question: string — if confidence != "confirmed", a short Persian question for the channel admin

IMPORTANT: The "source_trust" field indicates if the source is trusted (official) or not.
For trusted sources, set confidence to "confirmed" unless clearly a rumor.
For untrusted sources, be more conservative with confidence.

Output ONLY a JSON array, no other text.

Articles:
"""

def analyze_articles(articles: list) -> list:
    if not articles:
        return []

    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY not set")
        return []

    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"\n--- Article {i+1} ---\n"
        articles_text += f"Source: {a['source']} ({a['lang']}) | Trusted: {a.get('trusted', False)}\n"
        articles_text += f"Title: {a['title']}\n"
        articles_text += f"Summary: {a['summary'][:800]}\n"

    payload = {
        "contents": [{
            "parts": [{
                "text": PROMPT + articles_text
            }]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json"
        }
    }

    results = None
    models_to_try = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            resp = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            results = json.loads(text)
            print(f"[INFO] Gemini model {model} succeeded")
            break
        except Exception as e:
            print(f"[WARN] Gemini {model}: {e}")
            continue

    if not results:
        print("[WARN] All Gemini models failed - using basic scoring")
        # Fallback: basic scoring without LLM analysis
        analyzed = []
        for a in articles:
            analyzed.append({
                "id": a.get("id", a.get("hash", "")),
                "source": a["source"],
                "lang": a["lang"],
                "trusted": a.get("trusted", False),
                "title": a["title"],
                "link": a["link"],
                "summary": a["summary"],
                "title_fa": a["title"] if a["lang"] == "fa" else a["title"],
                "summary_fa": a["summary"][:400],
                "importance": "medium",
                "confidence": "likely",
                "category": "other",
                "hashtags": ["AI"],
                "admin_question": "",
                "status": "analyzed"
            })
        return analyzed

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
