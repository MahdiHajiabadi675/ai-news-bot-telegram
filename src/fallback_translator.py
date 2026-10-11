"""
Fallback Translator — وقتی Gemini fail بشه
از Google Translate (رایگان) استفاده می‌کنه
"""
import requests

def google_translate(text: str, dest: str = "fa") -> str:
    """ترجمه با Google Translate (رایگان، بدون API Key)"""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": dest,
            "dt": "t",
            "q": text
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        translated = "".join([part[0] for part in result[0] if part[0]])
        return translated
    except Exception as e:
        print(f"[WARN] Google Translate failed: {e}")
        return None

def translate_with_fallback(title: str, summary: str) -> dict:
    """ترجمه با سیستم جایگزین"""
    title_fa = google_translate(title)
    summary_fa = google_translate(summary[:1000])

    if title_fa and summary_fa:
        print("[FALLBACK] Translation successful via Google Translate")
        return {
            "title_fa": title_fa,
            "summary_fa": summary_fa
        }
    else:
        print("[FALLBACK] Translation failed completely")
        return None
