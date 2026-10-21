"""
Fallback Translator — وقتی Gemini fail بشه
از Google Translate (رایگان) استفاده می‌کنه
"""
import requests

# اگر گوگل چند بار پشت‌سرهم جواب نداد، ادامه‌ی اجرا را با آن معطل نکن
_fail_streak = 0
_MAX_FAIL_STREAK = 3


def reset():
    """ابتدای هر اجرا شمارنده خطاها صفر شود"""
    global _fail_streak
    _fail_streak = 0


def google_translate(text: str, dest: str = "fa") -> str:
    """ترجمه با Google Translate (رایگان، بدون API Key)"""
    global _fail_streak

    if _fail_streak >= _MAX_FAIL_STREAK:
        return None
    if not text:
        return ""

    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": dest,
            "dt": "t",
            "q": text
        }
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        result = resp.json()
        translated = "".join([part[0] for part in result[0] if part[0]])
        _fail_streak = 0
        return translated
    except Exception as e:
        _fail_streak += 1
        print(f"[WARN] Google Translate failed ({_fail_streak}/{_MAX_FAIL_STREAK}): {e}")
        return None

def translate_with_fallback(title: str, summary: str) -> dict:
    """ترجمه با سیستم جایگزین"""
    title_fa = google_translate(title)
    if not title_fa:
        print("[FALLBACK] Translation failed completely")
        return None

    summary_fa = google_translate(summary[:1000]) if summary else ""

    print("[FALLBACK] Translation successful via Google Translate")
    return {
        "title_fa": title_fa,
        "summary_fa": summary_fa or ""
    }
