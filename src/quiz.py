"""
کوییز آموزشی — یکی از پست‌های شبانه به‌شکل نظرسنجی تلگرام (type=quiz).

چرا؟ پست‌های آموزشی خوانده می‌شوند ولی تعامل کمی می‌گیرند. یک کوییز چهارگزینه‌ای
با «پاسخ درست» و «توضیح» بعد از انتخاب، هم یادگیری را می‌سنجد و هم مخاطب را
برمی‌گرداند. برای کانال، نظرسنجی تلگرام رایگان است و نیازی به مدل/کلید ندارد.

محتوا در `QUIZ_BANK` است (قابل ویرایش). هر کوییز یک `id` دارد و اخیراً استفاده‌شده‌ها
در `edu_state.quiz_used` نگه داشته می‌شوند تا تکرار نشوند.
"""

import re

QUIZ_BANK = [
    {
        "id": "prompt-specific",
        "question": "کدام پرامپت احتمالاً جواب بهتری می‌دهد؟",
        "options": [
            "یک ایمیل بنویس",
            "برای مدیر، یک ایمیل سه‌خطی رسمی دربارهٔ تعویق جلسهٔ فردا بنویس",
            "ایمیل خوب بنویس",
            "ایمیل رسمی است یا دوستانه؟",
        ],
        "correct": 1,
        "explanation": "هرچه مخاطب، طول، لحن و موضوع دقیق‌تر باشد، جواب قابل‌استفاده‌تر است. «ایمیل بنویس» مدل را مجبور می‌کند حدس بزند.",
    },
    {
        "id": "context-window",
        "question": "«پنجرهٔ زمینه» (context window) در مدل‌های زبانی یعنی چه؟",
        "options": [
            "حافظهٔ دائمی مدل از همهٔ گفتگوها",
            "حداکثر حجم متنی که مدل در یک نوبت می‌تواند ببیند",
            "سرعت پاسخ‌دهی مدل",
            "تعداد زبان‌هایی که مدل می‌فهمد",
        ],
        "correct": 1,
        "explanation": "مدل حافظهٔ دائمی ندارد؛ در هر نوبت فقط بخشی از متن (پنجرهٔ زمینه) را می‌بیند و بقیه از دست می‌رود.",
    },
    {
        "id": "hallucination",
        "question": "بهترین راه کم‌کردن «حدس‌زدن» مدل چیست؟",
        "options": [
            "پرسیدن سؤال کوتاه‌تر",
            "گفتن صریح «اگر مطمئن نیستی بگو نمی‌دانم» و بررسی عدد/تاریخ",
            "تکرار همان سؤال تا جواب درست بیاید",
            "استفاده از کلمات مودبانه‌تر",
        ],
        "correct": 1,
        "explanation": "اجازهٔ گفتن «نمی‌دانم» و جدا کردن بخش‌های قطعی از حدسی، از ساختن جواب محتمل ولی غلط جلوگیری می‌کند.",
    },
    {
        "id": "few-shot",
        "question": "«few-shot» در پرامپت‌نویسی چه کاری می‌کند؟",
        "options": [
            "تعداد توکن‌های خروجی را کم می‌کند",
            "با دادن چند نمونه، قالب و لحن دلخواه را به مدل نشان می‌دهد",
            "مدل را مجبور به پاسخ کوتاه می‌کند",
            "زبان پاسخ را تغییر می‌دهد",
        ],
        "correct": 1,
        "explanation": "چند مثال ورودی/خروجی بهترین راه انتقال قالب است؛ مدل الگو را از نمونه‌ها می‌گیرد.",
    },
    {
        "id": "image-prompt",
        "question": "برای گرفتن تصویر دقیق‌تر، کدام مورد مهم‌تر است؟",
        "options": [
            "نوشتن جملهٔ طولانی‌تر",
            "مشخص‌کردن سوژه، نور، سبک و کادر",
            "استفاده از کلمات احساسی",
            "تکرار یک کلمهٔ کلیدی",
        ],
        "correct": 1,
        "explanation": "تصویر از جزئیات ساخته می‌شود: چه چیزی، با چه نوری، در چه سبکی و با چه کادری.",
    },
    {
        "id": "chain",
        "question": "برای نوشتن یک متن بلند، کدام روش بهتر است؟",
        "options": [
            "یک پرامپت خیلی بلند با همهٔ جزئیات",
            "تقسیم به مراحل: ساختار، پیش‌نویس، ویرایش",
            "چند بار پرسیدن همان پرامپت",
            "نوشتن متن توسط خودت و فقط ترجمه با AI",
        ],
        "correct": 1,
        "explanation": "کارهای بزرگ با زنجیرهٔ پرامپت‌ها (ورک‌فلو) نتیجهٔ منظم‌تری می‌دهند تا یک درخواست غول‌آسا.",
    },
]


def pick(ordinal: int, used=()):
    """یک کوییز چرخشی که اخیراً استفاده نشده → (quiz، id)"""
    used = set(used or ())
    for i in range(len(QUIZ_BANK)):
        q = QUIZ_BANK[(ordinal + i) % len(QUIZ_BANK)]
        if q["id"] not in used:
            return q, q["id"]
    q = QUIZ_BANK[ordinal % len(QUIZ_BANK)]
    return q, q["id"]


# ───────────── اعتبارسنجی کوییزِ ساخته‌شده با مدل ─────────────
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_MD_RE = re.compile(r"[*`#]{1,}")
_PERSIAN_RE = re.compile(r"[؀-ۿ]")


def _persian_ratio(text) -> float:
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _PERSIAN_RE.match(c)) / len(letters)


def _clean(text, limit) -> str:
    text = _URL_RE.sub("", str(text or ""))
    text = _MD_RE.sub("", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def validate(data):
    """
    خروجی مدل را به کوییز قابل‌انتشار تبدیل می‌کند؛ None = بی‌کیفیت.

    شرط‌ها: ۴ گزینهٔ یکتا، ایندکس درست در بازه، متن فارسی، سؤال ۱۵..۲۲۰ کاراکتر،
    بدون لینک/مارک‌داون. «توضیح» اختیاری است.
    """
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), None)
    if isinstance(data, dict) and "question" not in data:
        inner = [v for v in data.values() if isinstance(v, (dict, list))]
        if len(inner) == 1:
            data = inner[0]
    if not isinstance(data, dict):
        return None

    question = _clean(data.get("question"), 220)
    raw_options = data.get("options")
    if isinstance(raw_options, str):
        raw_options = [x for x in re.split(r"\n+", raw_options) if x.strip()]
    if not isinstance(raw_options, list):
        return None
    options = [_clean(o, 90) for o in raw_options]
    options = [o for o in options if o]
    if len(options) != 4:
        return None
    if len({o.lower() for o in options}) != 4:
        return None

    try:
        correct = int(data.get("correct"))
    except (TypeError, ValueError):
        return None
    if not 0 <= correct <= 3:
        return None

    if not (15 <= len(question) <= 220):
        return None
    if _persian_ratio(question) < 0.5 or _persian_ratio(" ".join(options)) < 0.5:
        return None

    explanation = _clean(data.get("explanation"), 200)
    return {"question": question, "options": options, "correct": correct,
            "explanation": explanation}

