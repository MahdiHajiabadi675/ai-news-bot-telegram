"""
Translator — ترجمهٔ اخبار به فارسی (بدون Gemini)

زنجیرهٔ مترجم‌ها (رایگان، بدون API Key). اگر یکی خطا بدهد، بعدی امتحان می‌شود:
  1. Google Translate (gtx)
  2. MyMemory
  3. Lingva (پروکسی Google Translate)

نکته‌ها:
  • خطای هر مترجم جداگانه شمرده می‌شود؛ بعد از ۳ خطای پشت‌سرهم همان مترجم تا پایان
    اجرا کنار گذاشته می‌شود، ولی بقیهٔ مترجم‌ها کار می‌کنند (قبلاً با ۳ خطای گوگل،
    ترجمهٔ کل اجرا متوقف می‌شد و خبرهایی مثل MIT Tech Review ترجمه‌نشده می‌ماندند).
  • متن بلند تکه‌تکه (در مرز جمله) ترجمه می‌شود.
  • اگر همهٔ مترجم‌ها شکست بخورند، translate() مقدار None برمی‌گرداند و خبر با
    برچسب «ترجمه نشده» منتشر می‌شود.
"""
import re
import time
from urllib.parse import quote

import requests

_MAX_FAIL_STREAK = 3
_TIMEOUT = 8

# شمارندهٔ خطای پشت‌سرهم هر مترجم + کش درون‌اجرایی
_fail_streak = {}
_cache = {}


def reset():
    """ابتدای هر اجرا: خطاها و کش صفر شود"""
    _fail_streak.clear()
    _cache.clear()


# ───────────── مترجم‌ها ─────────────
# هر تابع: (text, src) → رشتهٔ ترجمه‌شده؛ در خطا Exception می‌دهد.

def _google(text: str, src: str) -> str:
    resp = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": src or "auto", "tl": "fa", "dt": "t", "q": text},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    out = "".join(part[0] for part in data[0] if part and part[0])
    if not out.strip():
        raise ValueError("empty google response")
    return out


def _mymemory(text: str, src: str) -> str:
    resp = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": f"{src if src and src != 'auto' else 'en'}|fa"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    out = (data.get("responseData") or {}).get("translatedText") or ""
    # وقتی سهمیه تمام شود، MyMemory متن هشدار برمی‌گرداند نه ترجمه
    if not out.strip() or "MYMEMORY WARNING" in out.upper() or data.get("responseStatus") not in (200, "200"):
        raise ValueError("mymemory quota/empty")
    return out


def _lingva(text: str, src: str) -> str:
    resp = requests.get(
        f"https://lingva.ml/api/v1/{src if src and src != 'auto' else 'auto'}/fa/{quote(text, safe='')}",
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    out = resp.json().get("translation") or ""
    if not out.strip():
        raise ValueError("empty lingva response")
    return out


# (نام، تابع، حداکثر طول هر تکه)
PROVIDERS = [
    ("google", _google, 1500),
    ("mymemory", _mymemory, 450),
    ("lingva", _lingva, 1200),
]


# ───────────── منطق ترجمه ─────────────

def _split_chunks(text: str, limit: int) -> list:
    """تقسیم متن به تکه‌های ≤ limit، ترجیحاً در مرز جمله"""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?؟])\s+", text)
    chunks, cur = [], ""
    for sent in sentences:
        while len(sent) > limit:  # جملهٔ خیلی بلند → برش سخت در مرز فاصله
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(sent[:cut])
            sent = sent[cut:].lstrip()
        if len(cur) + len(sent) + 1 > limit and cur:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def _translate_chunk(chunk: str, src: str):
    """یک تکه را با اولین مترجم سالم ترجمه می‌کند؛ وگرنه None"""
    for name, fn, _limit in PROVIDERS:
        if _fail_streak.get(name, 0) >= _MAX_FAIL_STREAK:
            continue
        try:
            out = fn(chunk, src)
            _fail_streak[name] = 0
            return out
        except Exception as e:
            _fail_streak[name] = _fail_streak.get(name, 0) + 1
            print(f"[WARN] translator {name} failed ({_fail_streak[name]}/{_MAX_FAIL_STREAK}): {e}")
            time.sleep(0.2)
    return None


def translate(text: str, src: str = "auto"):
    """متن را به فارسی ترجمه می‌کند. موفقیت → str، شکست کامل → None."""
    if not text or not text.strip():
        return ""
    key = (src, text)
    if key in _cache:
        return _cache[key]

    # کوچک‌ترین سقف بین مترجم‌های فعال: تکه‌ها برای همهٔ مترجم‌ها مجاز باشند
    active = [limit for name, _fn, limit in PROVIDERS if _fail_streak.get(name, 0) < _MAX_FAIL_STREAK]
    if not active:
        return None
    parts = []
    for chunk in _split_chunks(text, min(active)):
        out = _translate_chunk(chunk, src)
        if out is None:
            return None
        parts.append(out.strip())
    result = " ".join(parts)
    _cache[key] = result
    return result


def translate_article(title: str, summary: str, src: str = "auto") -> dict:
    """
    ترجمهٔ عنوان و خلاصه.
    برمی‌گرداند: {"title_fa", "summary_fa", "translated"}
      translated=False یعنی ترجمه نشد و title_fa/summary_fa همان متن اصلی‌اند.
    """
    title_fa = translate(title, src)
    if not title_fa:
        return {"title_fa": title, "summary_fa": (summary or "")[:400], "translated": False}

    summary_fa = translate((summary or "")[:1000], src) if summary else ""
    if summary and summary_fa is None:
        # عنوان ترجمه شد ولی خلاصه نه → خلاصهٔ اصلی با برچسب «ترجمه نشده»
        return {"title_fa": title_fa, "summary_fa": summary[:400], "translated": False}
    return {"title_fa": title_fa, "summary_fa": summary_fa or "", "translated": True}
