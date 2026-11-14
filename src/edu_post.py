"""
پست آموزشی هوش مصنوعی — فقط متن؛ ساخت هر ۶ ساعت، انتشار در ساعت‌های پربازدید

چطور کار می‌کند:
  • ساخت و انتشار جداست: روز به «اسلات‌های EDU_EVERY_HOURS ساعته» (پیش‌فرض ۶ → ۴ پست) تقسیم
    شده و اولین اجرا در هر اسلات جدید یک پست می‌سازد و در صف می‌گذارد؛ انتشار فقط در
    ساعت‌های پربازدید EDU_PUBLISH_HOURS (پیش‌فرض ۱۹ تا ۲۲ به وقت ایران، هر ساعت یک پست) انجام می‌شود.
    اگر ساخت شکست بخورد (Gemini خطا داد / خروجی بی‌کیفیت بود)، اجرای بعدی (ساعت بعد، همان
    اسلات) یک‌بار دیگر تلاش می‌کند؛ بعد از EDU_MAX_TRIES_PER_SLOT شکست آن اسلات رد می‌شود.
    پست ناقص هرگز منتشر نمی‌شود.
  • منبع ساخت: اول Gemini؛ اگر در دسترس نبود (مثلاً خطای 503) مدل‌های رایگان OpenRouter
    امتحان می‌شوند. فقط مدل‌های :free یا با قیمت صفر.
  • موضوع: ۱۲ دستهٔ ثابت به‌صورت چرخشی (پرامپت آماده، اشتباه رایج، مفهوم ساده، تولید تصویر، …)
    تا در طول زمان تنوع داشته باشد. عنوان‌های اخیر به مدل داده می‌شود تا تکرار نکند و عنوان
    مشابه عنوان‌های اخیر رد می‌شود.
  • قالب‌بندی را خودِ کد می‌سازد (نه مدل): خروجی JSON ساختاریافته، همه‌چیز escape می‌شود،
    پرامپت آماده داخل بلوک <pre> می‌آید تا با یک لمس کپی شود. هیچ عکس/ویدیو/فایلی تولید نمی‌شود.
  • کلید خاموش/روشن: دستور /edu on|off در ربات (state.edu_enabled) یا EDU_POST_ENABLED=false.
  • بودجهٔ Gemini از src/gemini_budget.py رعایت می‌شود؛ اگر Gemini کلید/بودجه ندارد مستقیم OpenRouter.
  • اولویت ثابت: Gemini ← مدل‌های flash دیگر ← OpenRouter رایگان. اولین مدلی که خروجی
    قابل‌قبول (validate) داد برنده است و بقیه صدا زده نمی‌شوند.
  • نسخهٔ متنی همان پست به کانال بله هم می‌رود (src/bale.py، فقط ارسال).
هیچ خطایی از این ماژول بیرون نمی‌آید و انتشار اخبار را مختل نمی‌کند.
"""
import json
import re
import time
from datetime import datetime, timedelta

import requests

from . import bale, cloudflare_kv, gemini_budget, quiz, storage
from .bot import send_message, send_poll
from .config import (
    CHANNEL_ID, DISPLAY_TZ_OFFSET_MIN, EDU_ENABLED, EDU_EVERY_HOURS,
    EDU_MAX_ATTEMPTS_PER_DAY, EDU_MAX_OUTPUT_TOKENS, EDU_MAX_REJECTS, EDU_MAX_TRIES_PER_SLOT, EDU_PUBLISH_HOURS, EDU_QUEUE_MAX,
    EDU_CATCHUP_MAX, EDU_POSTS_PER_DAY, EDU_QUIZ_PER_DAY, EDU_RECENT_KEEP, EDU_RECENT_PROMPT, EDU_RETRY_GAP_MIN,
    EDU_USE_BUILTIN_FALLBACK, DRY_RUN,
    GEMINI_API_KEY, OPENROUTER_API_KEY, OPENROUTER_APP_NAME, OPENROUTER_MAX_MODELS,
    OPENROUTER_MODELS, OPENROUTER_SITE_URL,
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

QUIZ_PROMPT = """You write ONE multiple-choice quiz question in PERSIAN (Farsi) for a Telegram
channel about AI news, based on the recent headlines below (or a practical AI concept
directly connected to them).

Recent headlines:
{news}

Rules:
- Exactly 4 options; exactly one is correct.
- The question must be answerable from general knowledge or from the headlines;
  do NOT invent numbers, dates, prices or model names that are not in the headlines.
- Persian, natural and short. No URLs, no markdown symbols (no *, #, `).
- "explanation" is one short Persian sentence explaining why the answer is right (max 200 chars).
- The question must be ABOUT THE NEWS, not generic trivia.

Return ONLY a JSON object:
{{
  "question": "string (Persian, 15-200 chars)",
  "options": ["string", "string", "string", "string"],
  "correct": 0,
  "explanation": "string"
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


# ───────────── پست‌های آمادهٔ محلی (بدون مدل) ─────────────
# چرا؟ اگر Gemini و مدل‌های رایگان OpenRouter هر دو خطا بدهند (429/503/تایم‌اوت) یا
# کلیدی تنظیم نشده باشد، صف خالی می‌ماند و آن شب هیچ پست آموزشی نمی‌رود. این متن‌ها
# از پیش نوشته و پاک‌سازی شده‌اند (بدون لینک و مارک‌داون) و فقط نقش «تور نجات» دارند.
BUILTIN_POSTS = [
    {
        "tag": "پرامپت_آماده",
        "title": "قالب پرامپت برای خلاصه‌کردن هر متن طولانی",
        "hook": "به‌جای «خلاصه کن»، به مدل بگو برای چه کسی و در چند کلمه خلاصه کند؛ نتیجه چند برابر کاربردی‌تر می‌شود.",
        "steps": [
            "مخاطب را مشخص کن: «برای مدیری که وقت ندارد».",
            "طول و قالب را بگو: «۵ بولت، هر کدام یک جمله».",
            "بگو چه چیزی حذف شود: «جزئیات فنی و مثال‌ها را نیاور».",
        ],
        "prompt": "این متن را برای [مخاطب] در [تعداد] بولت خلاصه کن. هر بولت یک جملهٔ کامل باشد. جزئیات [مورد نظر] را حذف کن و اگر عدد یا تصمیم مهمی بود، همان را نگه دار.\n\nمتن:\n[متن خودت را اینجا بگذار]",
        "tip": "اگر خروجی طولانی شد، بگو «نصفش کن»؛ کوتاه‌کردن تدریجی بهتر از بازنویسی از صفر است.",
        "hashtags": ["خلاصه_سازی", "پرامپت_نویسی"],
    },
    {
        "tag": "ترفند_چت‌بات",
        "title": "سه جمله‌ای که کیفیت جواب چت‌بات را عوض می‌کند",
        "hook": "اکثر مردم فقط سؤال می‌پرسند؛ اگر نقش، مخاطب و قالب را هم بدهی، جواب از «عمومی» به «قابل استفاده» می‌رسد.",
        "steps": [
            "نقش بده: «مثل یک ویراستار دقیق رفتار کن».",
            "مخاطب را بگو: «برای کسی که هیچ پیش‌زمینه‌ای ندارد».",
            "قالب را تعیین کن: «جدول دو ستونی، حداکثر ۶ ردیف».",
            "یک نمونهٔ دلخواه بده تا لحن را یاد بگیرد.",
        ],
        "prompt": "",
        "tip": "اگر جواب کلی بود، نگو «بهتر بگو»؛ بگو کدام قسمت کلی است.",
        "hashtags": ["چت_بات", "ترفند"],
    },
    {
        "tag": "اشتباه_رایج",
        "title": "اشتباه رایج: اجازه دادن به مدل برای حدس زدن",
        "hook": "مدل‌ها وقتی چیزی را نمی‌دانند، اغلب به‌جای گفتن «نمی‌دانم» یک جواب محتمل می‌سازند. یک جمله این را کم می‌کند.",
        "steps": [
            "در ابتدا بگو: «اگر مطمئن نیستی، صریح بگو نمی‌دانم».",
            "بخواه حدس‌ها را جدا کند: «کدام بخش‌ها قطعی نیست؟»",
            "برای عدد و تاریخ، خودت راستی‌آزمایی کن.",
        ],
        "prompt": "به این سؤال جواب بده. اگر بخشی از جواب قطعی نیست، همان‌جا بنویس «نامطمئن» و بگو برای مطمئن‌شدن چه چیزی لازم است.\n\nسؤال: [سؤال]",
        "tip": "«نامطمئن» را با لحن تشویقی بخواه؛ وگرنه مدل باز هم حدس می‌زند.",
        "hashtags": ["هالوسینیشن", "اعتبارسنجی"],
    },
    {
        "tag": "بهره‌وری",
        "title": "صورت‌جلسه در سه دقیقه با یک پرامپت",
        "hook": "یادداشت‌های پراکندهٔ جلسه را بده و خروجی ساختاریافته بگیر: تصمیم‌ها، مسئول‌ها و قدم بعدی.",
        "steps": [
            "یادداشت خام را همان‌طور که هست بفرست.",
            "سه بخش بخواه: تصمیم‌ها، اقدام‌ها با مسئول، سؤال‌های باز.",
            "بخواه جاهای خالی را «نامشخص» بگذارد، نه از خودش بسازد.",
        ],
        "prompt": "این یادداشت جلسه را به سه بخش تبدیل کن: ۱) تصمیم‌های قطعی ۲) اقدام‌ها با مسئول و موعد ۳) سؤال‌های باز. هر چیزی که در متن نبود، «نامشخص» بنویس و از خودت اضافه نکن.\n\n[یادداشت]",
        "tip": "خروجی را همان روز برای حاضران بفرست؛ اختلاف‌ها همان‌جا آشکار می‌شود.",
        "hashtags": ["صورت_جلسه", "بهره_وری"],
    },
    {
        "tag": "یادگیری_با_AI",
        "title": "یک مفهوم سخت را در سه سطح یاد بگیر",
        "hook": "یک مفهوم را در سه سطح بگیر: نوجوان، دانشجو، متخصص — و همان‌جا بفهم کجا گیر کرده‌ای.",
        "steps": [
            "بگو: «سه سطح توضیح بده: ۱۲ ساله، دانشجو، متخصص».",
            "بگو کدام سطح را نفهمیدی و چرا.",
            "بخواه با یک مثال روزمره دوباره بگوید.",
            "آخرش سه سؤال بده تا خودت را امتحان کنی.",
        ],
        "prompt": "مفهوم [مفهوم] را در سه سطح توضیح بده: (۱) برای نوجوان ۱۲ ساله (۲) برای دانشجوی مبتدی (۳) برای متخصص. بعد سه سؤال کوتاه بده که بفهمم یاد گرفته‌ام یا نه.",
        "tip": "اگر سطح سوم را نفهمیدی، مشکل تو نیست؛ بخواه اصطلاحات را یکی‌یکی تعریف کند.",
        "hashtags": ["یادگیری", "آموزش"],
    },
    {
        "tag": "تولید_تصویر",
        "title": "آناتومی یک پرامپت تصویر خوب",
        "hook": "«گربهٔ فضانورد» تصویر می‌دهد، اما «گربهٔ نارنجی فضانورد، نور سینمایی، پس‌زمینهٔ سیاه» تصویر درست می‌دهد.",
        "steps": [
            "سوژه و جزئیات ظاهری را بنویس.",
            "سبک و نور را اضافه کن: طبیعی، سینمایی، استودیویی.",
            "کادر و لنز را مشخص کن: نمای نزدیک یا واید.",
            "یک قید منفی بگذار: «بدون متن روی تصویر».",
        ],
        "prompt": "سوژه: [چه چیزی] — جزئیات: [رنگ و حال‌وهوا] — سبک: [عکاسی سینمایی] — نور: [نور طبیعی صبح] — کادر: [نمای نزدیک] — بدون متن روی تصویر.",
        "tip": "برای ثبات بین چند تصویر، همان پرامپت را نگه دار و فقط یک بخش را عوض کن.",
        "hashtags": ["تولید_تصویر", "پرامپت"],
    },
    {
        "tag": "برنامه‌نویسی_با_AI",
        "title": "دیباگ با AI بدون فرستادن کل پروژه",
        "hook": "به‌جای فرستادن کل کد، خطا و قطعهٔ کوچک را بفرست و بخواه فرض‌هایش را هم بگوید.",
        "steps": [
            "پیام خطا و بیست خط مرتبط را بفرست، نه کل مخزن.",
            "بخواه اول فرض‌هایش را لیست کند، بعد راه‌حل بدهد.",
            "دو راه‌حل با معایبشان بخواه.",
            "یک تست کوچک بخواه که باگ برنگردد.",
        ],
        "prompt": "این خطا و این قطعه کد را ببین. اول فرض‌هایت را لیست کن، بعد دو راه‌حل با معایب هر کدام بده و یک تست کوچک بنویس که این باگ دیگر برنگردد.\n\nخطا: [متن]\nکد: [قطعه]",
        "tip": "اگر راه‌حل اول کار نکرد، فرض‌های اشتباه را به مدل برگردان؛ معمولاً همان‌جا حل می‌شود.",
        "hashtags": ["برنامه_نویسی", "دیباگ"],
    },
    {
        "tag": "مفهوم_ساده",
        "title": "پنجرهٔ زمینه با یک مثال ساده",
        "hook": "مدل حافظهٔ دائمی ندارد؛ هر بار فقط می‌تواند چند صفحهٔ اخیر گفتگو را ببیند.",
        "steps": [
            "تصور کن میزی است که فقط چند برگه رویش جا می‌شود.",
            "با اضافه‌شدن برگهٔ جدید، قدیمی‌ترین می‌افتد.",
            "برای کار طولانی، خلاصهٔ مرحلهٔ قبل را نگه دار و دوباره بفرست.",
            "گاهی از مدل بخواه خلاصهٔ تصمیم‌ها را بدهد.",
        ],
        "prompt": "",
        "tip": "اگر مدل جزئیات اول گفتگو را فراموش کرد، تقصیر تو نیست؛ دوباره خلاصه‌اش کن.",
        "hashtags": ["مفهوم_ساده", "context"],
    },
    {
        "tag": "کاربرد_روزمره",
        "title": "خرید هوشمندتر با یک جدول مقایسه",
        "hook": "قبل از خرید، معیارهایت را بده و بخواه جدول مقایسه بسازد؛ تصمیم‌گیری چند دقیقه می‌شود.",
        "steps": [
            "معیارها را بنویس: قیمت، گارانتی، اندازه، مصرف.",
            "وزن هر معیار را مشخص کن.",
            "جدول و در آخر یک پیشنهاد با دلیل بخواه.",
            "قیمت‌های واقعی را خودت جای‌گذاری کن.",
        ],
        "prompt": "این گزینه‌ها را با معیارهای [معیارها] مقایسه کن و جدول بده. وزن معیارها: [وزن‌ها]. در آخر یک پیشنهاد بده و دلیلش را در دو جمله بگو. اگر داده‌ای نبود، جایش را خالی بگذار.\n\nگزینه‌ها: [الف، ب، ج]",
        "tip": "قیمت را خودت وارد کن؛ مدل قیمت روز را نمی‌داند.",
        "hashtags": ["خرید_هوشمند", "کاربرد_روزمره"],
    },
    {
        "tag": "ورک‌فلو",
        "title": "ورک‌فلوی سه‌مرحله‌ای: از ایده تا متن آماده",
        "hook": "یک کار بزرگ را به سه پرامپت تقسیم کن: ساختار، پیش‌نویس، ویرایش — نتیجه از یک پرامپت بلند بهتر است.",
        "steps": [
            "مرحلهٔ ۱: فقط ساختار و سرفصل‌ها را بخواه.",
            "مرحلهٔ ۲: هر بخش را جدا بنویس.",
            "مرحلهٔ ۳: بخواه مثل ویراستار سختگیر نقد و کوتاه کند.",
        ],
        "prompt": "مرحلهٔ ۱: برای موضوع [موضوع] و مخاطب [مخاطب] یک ساختار با ۴ سرفصل بده. مرحلهٔ ۲: بعد از تأیید من، هر سرفصل را در ۳ جمله بنویس. مرحلهٔ ۳: در پایان، متن را کوتاه و روان کن و جمله‌های تکراری را حذف کن.",
        "tip": "بین مرحله‌ها یک خط بازخورد بده؛ کیفیت کل کار بالا می‌رود.",
        "hashtags": ["ورک_فلو", "نویسندگی"],
    },
    {
        "tag": "نویسندگی_و_محتوا",
        "title": "لحن را با یک نمونه کپی کن",
        "hook": "به‌جای گفتن «رسمی‌تر بنویس»، یک پاراگراف نمونه بده؛ مدل لحن را دقیق‌تر می‌گیرد.",
        "steps": [
            "یک پاراگراف از متنی که لحنش را می‌پسندی بفرست.",
            "بگو «با همین لحن، این متن را بازنویسی کن».",
            "بعد بخواه فقط جمله‌های بلند را کوتاه کند.",
            "در آخر بپرس کدام جمله ضعیف است.",
        ],
        "prompt": "این نمونهٔ لحن است:\n[نمونه]\n\nحالا متن زیر را با همین لحن بازنویسی کن، بدون اضافه‌کردن مطلب تازه:\n[متن]",
        "tip": "یک نمونهٔ بد هم بده و بگو چه چیزی را نمی‌خواهی؛ نتیجه دقیق‌تر می‌شود.",
        "hashtags": ["نویسندگی", "لحن"],
    },
    {
        "tag": "تصمیم_و_تحلیل",
        "title": "قبل از تصمیم، از AI بخواه نقش مخالف را بازی کند",
        "hook": "به‌جای تأیید گرفتن، بخواه ضعیف‌ترین نقطهٔ نقشه‌ات را پیدا کند؛ این کار تصمیم را واقعاً بهتر می‌کند.",
        "steps": [
            "نقشه‌ات را در پنج جمله بنویس.",
            "بخواه سه ریسک اصلی و یک سؤال سخت مطرح کند.",
            "برای هر ریسک یک راه کاهش بخواه.",
            "در آخر بپرس «چه چیزی را ندیده‌ام؟»",
        ],
        "prompt": "من می‌خواهم [تصمیم] را انجام دهم. تو نقش یک منتقد تیزبین را بازی کن: سه ریسک اصلی، یک سؤال سخت که باید جواب بدهم، و برای هر ریسک یک راه کاهش بده. در پایان بگو چه چیزی را ممکن است ندیده باشم.",
        "tip": "جواب «همه‌چیز خوب است» یعنی پرامپتت نقدطلب نبوده؛ دوباره با نقش مخالف بخواه.",
        "hashtags": ["تصمیم_گیری", "تحلیل"],
    },
]


def builtin_item(ordinal: int, recent_titles):
    """یک پست آمادهٔ محلی (چرخشی، بدون تکرار عنوان‌های اخیر) → (post، tag)"""
    for i in range(len(BUILTIN_POSTS)):
        cand = BUILTIN_POSTS[(ordinal + i) % len(BUILTIN_POSTS)]
        if not too_similar(cand["title"], recent_titles):
            return dict(cand), cand["tag"]
    cand = BUILTIN_POSTS[ordinal % len(BUILTIN_POSTS)]
    return dict(cand), cand["tag"]


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


def _unwrap(data):
    """بعضی مدل‌ها JSON را در لیست یا کلید بیرونی می‌پیچند: [{...}] یا {"post": {...}}"""
    for _ in range(3):
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), None)
        elif isinstance(data, dict) and "title" not in data:
            inner = [v for v in data.values() if isinstance(v, (dict, list))]
            if len(inner) != 1:
                break
            data = inner[0]
        else:
            break
    return data if isinstance(data, dict) else None


def _step_text(item):
    """مرحله ممکن است رشته یا دیکشنری ({"step": "..."}) باشد"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in ("text", "step", "description", "title", "content"):
            if isinstance(item.get(k), str):
                return item[k]
        return next((v for v in item.values() if isinstance(v, str)), "")
    return ""


def check_post(data, recent_titles):
    """(post یا None، دلیل رد) — دلیل برای لاگ است تا بدانیم چرا خروجی مدل قبول نشد"""
    data = _unwrap(data)
    if data is None:
        return None, "not_an_object"
    title = _clean(data.get("title"), 80)
    hook = _clean(data.get("hook"), 240)
    tip = _clean(data.get("tip"), 200)
    prompt = str(data.get("prompt") or "")
    prompt = _URL_RE.sub("", prompt).replace("```", "").strip()[:750]
    raw_steps = data.get("steps") or []
    if isinstance(raw_steps, str):
        raw_steps = [x for x in re.split(r"\n+", raw_steps) if x.strip()]
    steps = [_clean(_step_text(x), 220) for x in raw_steps if _step_text(x).strip()][:5]
    raw_tags = data.get("hashtags") or []
    raw_tags = [raw_tags] if isinstance(raw_tags, str) else raw_tags
    tags = [re.sub(r"[^\w\u0600-\u06FF\u200c]", "", str(t)).strip("_") for t in raw_tags][:2]
    tags = [t for t in tags if t]

    if not title:
        return None, "missing_title"
    if not hook:
        return None, "missing_hook"
    if len(steps) < 2 and len(prompt) < 40:
        return None, f"too_thin(steps={len(steps)},prompt={len(prompt)})"
    ratio = _persian_ratio(title + " " + hook)
    if ratio < 0.5:
        return None, f"not_persian({ratio:.2f})"
    if too_similar(title, recent_titles):
        return None, "similar_to_recent_title"
    return {"title": title, "hook": hook, "steps": steps, "prompt": prompt, "tip": tip, "hashtags": tags}, ""


def validate(data, recent_titles):
    """خروجی مدل را پاک‌سازی و اعتبارسنجی می‌کند. None = بی‌کیفیت"""
    return check_post(data, recent_titles)[0]


def _validate_logged(data, recent_titles):
    """مثل validate ولی دلیل رد را چاپ می‌کند (برای عیب‌یابی لاگ Actions)"""
    post, why = check_post(data, recent_titles)
    if post is None:
        sample = ""
        d = _unwrap(data)
        if d:
            sample = f" title={str(d.get('title', ''))[:50]!r}"
        print(f"[EDU] output rejected: {why}{sample}")
    return post


def format_html(post: dict, category_tag: str) -> str:
    e = escape_html
    div = "─" * 20

    def build(prompt_text):
        parts = [f"🎓 <b>آموزش | {e(post['title'])}</b>", div, "", e(post["hook"])]
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
    lines = [f"🎓 آموزش | {post['title']}", "", post["hook"]]
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


# ───────────── OpenRouter (فقط مدل رایگان، fallback) ─────────────

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
POOL = 10  # اندازهٔ فهرست کاندید؛ تعداد درخواست واقعی با OPENROUTER_MAX_MODELS محدود است

# اگر فهرست زندهٔ مدل‌ها نیامد، این‌ها امتحان می‌شوند (شناسه‌ها ممکن است عوض شوند).
DEFAULT_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
]

_FREE_BAD_PARTS = (
    "vision", "image", "audio", "embed", "rerank", "tts", "whisper",
    "moderation", "guard", "safety",
)


def _model_rank(model_id: str) -> int:
    """مدل‌های قوی‌تر/شناخته‌شده‌تر اول امتحان می‌شوند"""
    mid = model_id.lower()
    order = ("llama-3.3-70b", "gemma-4", "gemma-3", "qwen", "nemotron",
             "mistral", "deepseek", "llama", "inkling")
    for i, key in enumerate(order):
        if key in mid:
            return i
    return len(order)


def _pick_free_models() -> list:
    """فهرست زندهٔ مدل‌های رایگان OpenRouter (fallback: کش، سپس فهرست پیش‌فرض)"""
    if OPENROUTER_MODELS:
        return OPENROUTER_MODELS[:max(1, OPENROUTER_MAX_MODELS)]

    free = []
    try:
        resp = requests.get(f"{OPENROUTER_BASE}/models", timeout=20)
        if resp.status_code == 200:
            for model in (resp.json().get("data") or []):
                mid = str(model.get("id") or "")
                if not mid or any(part in mid.lower() for part in _FREE_BAD_PARTS):
                    continue
                pricing = model.get("pricing") or {}
                prompt_price = str(pricing.get("prompt", "1")).strip()
                completion_price = str(pricing.get("completion", "1")).strip()
                is_free = mid.endswith(":free") or (
                    prompt_price in ("0", "0.0", "0.00")
                    and completion_price in ("0", "0.0", "0.00")
                )
                if is_free:
                    free.append(mid)
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] OpenRouter model list failed: {type(e).__name__}")

    if free:
        free = sorted(set(free), key=_model_rank)
        try:
            cloudflare_kv.put("openrouter_free_models", free[:10])
        except Exception:  # noqa: BLE001
            pass
        return free[:POOL]

    cached = cloudflare_kv.get("openrouter_free_models")
    if isinstance(cached, list) and cached:
        return [m for m in cached if isinstance(m, str)][:POOL]

    return DEFAULT_FREE_MODELS[:POOL]


def _call_openrouter(model: str, prompt: str, timeout: float = None):
    """
    یک درخواست به مدل رایگان OpenRouter → (JSON یا None، کد وضعیت، دلیل).
    دلیل "daily" یعنی سقف روزانهٔ مدل‌های رایگان حساب پر شده (امتحان مدل دیگر بی‌فایده است).
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": EDU_MAX_OUTPUT_TOKENS,
        "temperature": 0.9,
    }
    try:
        resp = requests.post(f"{OPENROUTER_BASE}/chat/completions",
                             headers=headers, json=payload, timeout=timeout or CALL_TIMEOUT)
        if resp.status_code != 200:
            body = str(getattr(resp, "text", "") or "")
            low = body.lower()
            reason = "daily" if ("free-models-per-day" in low or "per-day" in low or "per day" in low) else ""
            msg = re.sub(r"\s+", " ", body)[:110]
            print(f"[EDU] OpenRouter {model}: HTTP {resp.status_code}" + (f" — {msg}" if msg else ""))
            return None, resp.status_code, reason
        choices = resp.json().get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else ""
        return _extract_json(text), 200, ""
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] OpenRouter {model}: {type(e).__name__}")
        return None, 0, ""


def _vendor(model_id: str) -> str:
    return str(model_id).split("/", 1)[0].lower()


def _openrouter_generate(prompt: str, good=None, left=None):
    """
    اولین مدل رایگان OpenRouter که خروجی «قابل‌قبول» بدهد برنده است؛ بقیه صدا زده نمی‌شوند.
    good(data) → آیا خروجی قابل‌قبول است؟  left() → ثانیهٔ باقی‌ماندهٔ بودجهٔ زمانی.
    • اگر مدلی ۴۲۹/۵xx داد، مدل‌های هم‌خانواده (همان vendor، مثل دو Gemma گوگل که سهمیهٔ مشترک
      دارند) رد می‌شوند و مدل vendor دیگر امتحان می‌شود.
    • اگر سقف روزانهٔ رایگان حساب پر شده باشد، فوراً متوقف می‌شود.
    • حداکثر OPENROUTER_MAX_MODELS درخواست.
    """
    if not OPENROUTER_API_KEY:
        print("[EDU] OpenRouter key missing — fallback skipped")
        return None
    attempts = 0
    blocked = set()
    for model in _pick_free_models():
        if attempts >= max(1, OPENROUTER_MAX_MODELS):
            break
        if left is not None and left() <= 5:
            break
        if _vendor(model) in blocked:
            continue
        timeout = CALL_TIMEOUT if left is None else min(CALL_TIMEOUT, max(5, left()))
        attempts += 1
        _RUN["or_calls"] += 1
        data, code, reason = _call_openrouter(model, prompt, timeout)
        if reason == "daily":
            print("[EDU] OpenRouter daily free limit reached — stopping")
            break
        if code in RETRY_STATUS and code != 200:
            blocked.add(_vendor(model))
        if data is None:
            continue
        if good is not None and not good(data):
            print(f"[EDU] OpenRouter {model}: output rejected — next model")
            _RUN["rejected"] += 1
            if _RUN["rejected"] >= max(1, EDU_MAX_REJECTS):
                print(f"[EDU] {_RUN['rejected']} outputs rejected — stopping to save tokens")
                break
            continue
        _RUN.update(provider="openrouter", model=model)
        print(f"[EDU] OpenRouter success: {model}")
        return data
    return None


RETRY_STATUS = (0, 429, 500, 502, 503, 504)  # خطاهای موقتی سمت سرور/شبکه
BACKOFF_SECONDS = (6, 15)                     # فاصلهٔ تلاش‌های مجدد روی همان مدل
MAX_TOTAL_SECONDS = 150                       # سقف کل زمان ساخت یک پست (Gemini + OpenRouter، همهٔ تلاش‌ها)
CALL_TIMEOUT = 40
_sleep = time.sleep
_RUN = {"gemini_calls": 0, "or_calls": 0, "rejected": 0, "provider": "", "model": ""}  # گزارش آخرین generate


def _call(model: str, payload: dict, timeout: float = CALL_TIMEOUT):
    """یک درخواست؛ (dict یا None، کد وضعیت). کد ۰ = خطای شبکه/timeout/پاسخ نامعتبر"""
    try:
        resp = requests.post(f"{GEMINI_API_BASE}/models/{model}:generateContent",
                             params={"key": GEMINI_API_KEY}, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None, resp.status_code
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _extract_json(text), 200
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] Gemini error: {type(e).__name__}")
        return None, 0


def _alternatives(exclude: str, limit: int = 2) -> list:
    """مدل‌های flash دیگر (جدیدترین اول) برای وقتی مدل اصلی شلوغ/خراب است"""
    try:
        resp = requests.get(f"{GEMINI_API_BASE}/models", params={"key": GEMINI_API_KEY, "pageSize": 200}, timeout=15)
        if resp.status_code != 200:
            return []
        names = [m.get("name", "").replace("models/", "") for m in resp.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        bad = ("thinking", "lite", "8b", "embedding", "aqa", "tts", "image", "live", "vision", "audio", "robotics")

        def ver(n):
            m = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
            return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)

        flash = sorted((n for n in names if "flash" in n and n != exclude and not any(x in n for x in bad)),
                       key=ver, reverse=True)
        return flash[:limit]
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] model list failed: {type(e).__name__}")
        return []


def generate(category_desc: str, recent_titles: list, accept=None, use_gemini: bool = True):
    """
    ساخت JSON خام. ترتیب اولویت (ثابت):
      ۱) Gemini (مدل اصلی؛ روی خطای موقتی چند بار با فاصله)
      ۲) مدل‌های flash دیگر Gemini
      ۳) مدل‌های رایگان OpenRouter (به ترتیب رتبه)
    اولین مدلی که خروجی «قابل‌قبول» بدهد برنده است و بقیه اصلاً صدا زده نمی‌شوند.
    «قابل‌قبول» یعنی accept(data) (در _run همان validate) None برنگرداند. خروجی ناخوانا یا
    بی‌کیفیت یعنی مدل بعدی. کل زمان از MAX_TOTAL_SECONDS بیشتر نمی‌شود.
    """
    recent = "\n".join(f"- {t}" for t in recent_titles[-max(1, EDU_RECENT_PROMPT):]) or "(none yet)"
    prompt = PROMPT.format(topic=category_desc, recent=recent)
    return _run_models(prompt, _accepts(accept), use_gemini)


def _accepts(accept):
    """تابع accept را به یک تابع bool ساده تبدیل می‌کند."""
    def good(data):
        if data is None:
            return False
        if accept is None:
            return True
        try:
            return accept(data) is not None
        except Exception:  # noqa: BLE001
            return False
    return good


def generate_quiz(news_titles, accept=None, use_gemini: bool = True):
    """کوییز چهارگزینه‌ای مرتبط با خبرهای تازه (همان زنجیرهٔ مدل‌ها)."""
    news = "\n".join(f"- {t}" for t in (news_titles or [])[:8]) or "(خبر تازه‌ای نیست)"
    return _run_models(QUIZ_PROMPT.format(news=news), _accepts(accept), use_gemini)


def _run_models(prompt: str, good, use_gemini: bool = True):
    """
    اجرای زنجیرهٔ مدل‌ها روی یک پرامپت دلخواه (پست آموزشی یا کوییز).
    ترتیب اولویت: Gemini → مدل‌های flash دیگر → مدل‌های رایگان OpenRouter.
    """
    _RUN.update(gemini_calls=0, or_calls=0, rejected=0, provider="", model="")
    t0 = time.monotonic()

    def left():
        return MAX_TOTAL_SECONDS - (time.monotonic() - t0)

    if use_gemini and GEMINI_API_KEY:
        model = _resolve_model()
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.9,
                "maxOutputTokens": EDU_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
            },
        }
        code = 0
        for delay in (0,) + BACKOFF_SECONDS:
            if left() <= 5:
                break
            if delay:
                _sleep(delay)
            _RUN["gemini_calls"] += 1
            data, code = _call(model, payload, min(CALL_TIMEOUT, max(5, left())))
            if code == 200:
                if good(data):
                    _RUN.update(provider="gemini", model=model)
                    return data
                print(f"[EDU] Gemini {model}: output rejected")
                _RUN["rejected"] += 1
                break  # جواب داد ولی قابل‌قبول نبود؛ تکرار روی همان مدل فایده ندارد
            print(f"[EDU] Gemini {model}: HTTP {code}")
            if code not in RETRY_STATUS:
                break

        if code in (400, 404):
            _invalidate_model()  # مدل منقضی → اجرای بعد مدل تازه پیدا کند
        elif code in RETRY_STATUS or code == 200:
            for alt in _alternatives(model):
                if left() <= 5 or _RUN["rejected"] >= max(1, EDU_MAX_REJECTS):
                    break
                _RUN["gemini_calls"] += 1
                data, c = _call(alt, payload, min(CALL_TIMEOUT, max(5, left())))
                print(f"[EDU] Gemini fallback {alt}: HTTP {c}")
                ok_alt = c == 200 and good(data)
                if c == 200 and not ok_alt:
                    _RUN["rejected"] += 1
                if ok_alt:
                    try:
                        cloudflare_kv.put("gemini_model", alt)  # اجراهای بعد از همین شروع کنند
                    except Exception:  # noqa: BLE001
                        pass
                    _RUN.update(provider="gemini", model=alt)
                    return data
        print("[EDU] Gemini unavailable — trying free OpenRouter models")
    else:
        print("[EDU] Gemini skipped — trying free OpenRouter models")
    if _RUN["rejected"] >= max(1, EDU_MAX_REJECTS):
        print(f"[EDU] {_RUN['rejected']} outputs rejected — stopping to save tokens")
        return None
    return _openrouter_generate(prompt, good=good, left=left)


# ───────────── نقطهٔ ورود ─────────────

def maybe_post(state: dict, now=None, force: bool = False) -> dict:
    """
    هر اجرای ساعتی دو کار می‌کند (به‌ترتیب، هر دو بی‌خطر):
      ۱) ساخت: اگر اسلات ۶ ساعته جدید است، یک پست با مدل بساز و در «صف انتشار» بگذار.
      ۲) انتشار: اگر ساعت (وقت ایران) جزو EDU_PUBLISH_HOURS است و در این ساعت پستی نرفته،
         اولین پست صف را منتشر کن.
    state را برمی‌گرداند (تغییر نمی‌دهد).
    force=True (اجرای دستی): فقط ساخت، با نادیده‌گرفتن فاصلهٔ تلاش‌ها و بدون سوزاندن تلاش؛
    انتشار خارج از ساعت‌های تعیین‌شده هرگز انجام نمی‌شود.
    """
    try:
        if not _enabled(state):
            return state
        if DRY_RUN:
            est = cloudflare_kv.get(KEY, {})
            queue_n = len(est.get("queue") or []) if isinstance(est, dict) else 0
            print(f"[DRY] edu generation/publish skipped (queue={queue_n})")
            return state
        now_utc = now or datetime.utcnow()
        est = cloudflare_kv.get(KEY, {})
        est = est if isinstance(est, dict) else {}
        try:
            _generate(est, now_utc, force)
        except Exception as e:  # noqa: BLE001
            print(f"[EDU] generate error: {type(e).__name__}: {e}")
        try:
            _publish_due(est, now_utc)
        except Exception as e:  # noqa: BLE001
            print(f"[EDU] publish error: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] unexpected error: {type(e).__name__}: {e}")
    return state


def _enabled(state) -> bool:
    if not EDU_ENABLED or state.get("edu_enabled") is False:
        return False
    if state.get("paused"):
        return False
    return bool(CHANNEL_ID)


def _generate(est, now_utc, force=False):
    """ساخت یک پست برای اسلات فعلی و گذاشتن در صف (نه انتشار)"""
    slot, ordinal = current_slot(now_utc)
    queue = est.get("queue") if isinstance(est.get("queue"), list) else []
    if est.get("last_slot") == slot:
        return
    if len(queue) >= max(1, EDU_QUEUE_MAX):
        print(f"[EDU] Queue full ({len(queue)}) — not generating")
        return

    day = local_now(now_utc).date().isoformat()
    if not force and (est.get("usage", {}).get(day, {}).get("attempts", 0)) >= EDU_MAX_ATTEMPTS_PER_DAY:
        print(f"[EDU] Daily attempt cap reached ({EDU_MAX_ATTEMPTS_PER_DAY}) — not generating")
        return
    same_slot = est.get("tries_slot") == slot
    tries = (est.get("tries") or 0) if same_slot else 0
    if tries >= EDU_MAX_TRIES_PER_SLOT:
        return
    last_try = est.get("last_try_at") if same_slot else None
    if not force and tries and last_try:
        try:
            gap_min = (now_utc - datetime.fromisoformat(str(last_try).replace("Z", ""))).total_seconds() / 60
            if 0 <= gap_min < EDU_RETRY_GAP_MIN:
                print(f"[EDU] Last try {gap_min:.0f}m ago (gap {EDU_RETRY_GAP_MIN}m) — waiting")
                return
        except Exception:  # noqa: BLE001
            pass

    # Gemini فقط با کلید و سقف روزانهٔ مجاز؛ فاصلهٔ تلاش‌ها را خود پست آموزشی کنترل می‌کند
    gemini_ok = bool(GEMINI_API_KEY) and gemini_budget.can_call(min_interval_min=0)
    if not gemini_ok:
        print("[EDU] Gemini not available" + ("" if GEMINI_API_KEY else " (no key)") + " — using OpenRouter only")
        if not OPENROUTER_API_KEY:
            return

    tag, desc = pick_category(ordinal)
    recent = [r.get("title", "") for r in est.get("recent", []) if isinstance(r, dict)]
    raw = generate(desc, recent, accept=lambda r: _validate_logged(r, recent), use_gemini=gemini_ok)
    if gemini_ok and _RUN.get("gemini_calls"):
        gemini_budget.mark_called()
    post = validate(_unwrap(raw), recent) if raw is not None else None
    _note_usage(est, day, post is not None)

    if post is None:
        if not force:
            est.update({"tries_slot": slot, "tries": tries + 1, "last_try_at": now_utc.isoformat() + "Z"})
        cloudflare_kv.put(KEY, est)  # آمار مصرف هم ذخیره شود
        print(f"[EDU] No valid post for slot {slot} (try {tries + 1}/{EDU_MAX_TRIES_PER_SLOT}"
              + (", force: not counted)" if force else ")"))
        return

    via = f"{_RUN.get('provider') or '?'}/{_RUN.get('model') or '?'}"
    queue.append({"post": post, "tag": tag, "slot": slot, "via": via, "made_at": now_utc.isoformat() + "Z"})
    est["queue"] = queue
    est["last_slot"] = slot
    est["tries_slot"], est["tries"], est["last_try_at"] = slot, 0, None
    est["recent"] = (est.get("recent", []) + [{"title": post["title"], "tag": tag, "via": via,
                                               "at": now_utc.isoformat() + "Z"}])[-EDU_RECENT_KEEP:]
    cloudflare_kv.put(KEY, est)
    print(f"[EDU] Generated: {post['title'][:50]} ({tag}) via {via} — queue={len(queue)}")


def _note_usage(est, day, ok):
    """آمار مصرف هر روز (برای /edu): تلاش‌ها، درخواست‌ها به مدل‌ها، خروجی‌های ردشده (= توکن هدررفته)"""
    usage = est.setdefault("usage", {})
    d = usage.setdefault(day, {"attempts": 0, "ok": 0, "fail": 0, "calls": 0, "rejected": 0, "via": {}})
    d["attempts"] += 1
    d["ok" if ok else "fail"] += 1
    d["calls"] += int(_RUN.get("gemini_calls", 0)) + int(_RUN.get("or_calls", 0))
    d["rejected"] += int(_RUN.get("rejected", 0))
    if ok:
        via = f"{_RUN.get('provider') or '?'}/{_RUN.get('model') or '?'}"
        d["via"][via] = d["via"].get(via, 0) + 1
    for old in sorted(usage)[:-3]:  # فقط ۳ روز آخر
        del usage[old]


def _peek_queue(est):
    """اولین آیتم سالم صف، بدون برداشتن (آیتم‌های خراب حذف می‌شوند)."""
    queue = est.get("queue") if isinstance(est.get("queue"), list) else []
    while queue and not (isinstance(queue[0], dict) and isinstance(queue[0].get("post"), dict)):
        queue.pop(0)
    est["queue"] = queue
    return queue[0] if queue else None


def _pub_count(est, day):
    counts = est.get("pub_count") if isinstance(est.get("pub_count"), dict) else {}
    try:
        return counts, int(counts.get(day) or 0)
    except (TypeError, ValueError):
        return counts, 0


def _quiz_due(est, loc) -> bool:
    """آیا امروز کوییز نرفته است؟ (کوییز پست پنجم است: ۴ آموزشی + ۱ کوییز)"""
    if EDU_QUIZ_PER_DAY <= 0:
        return False
    return est.get("quiz_last_date") != loc.date().isoformat()


def _recent_news_titles(now_utc, limit: int = 8):
    """عنوان خبرهای واقعی دو روز اخیر، برای ساختن کوییز «مرتبط با اخبار»"""
    try:
        log = storage.load_published()
    except Exception:  # noqa: BLE001
        return []
    cutoff = now_utc - timedelta(days=2)
    out = []
    for p in reversed(log):
        if not isinstance(p, dict) or not p.get("published") or p.get("deleted"):
            continue
        if p.get("category") in ("edu", "digest", "paper", "quiz"):
            continue
        at = str(p.get("published_at") or "").replace("Z", "")
        try:
            when = datetime.fromisoformat(at)
        except ValueError:
            continue
        if when < cutoff:
            continue
        title = str(p.get("title_fa") or p.get("title") or "").strip()
        if title:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def _log_quiz(question, message_id=None):
    try:
        log = storage.load_published()
        entry = {"id": f"quiz-{local_now().date().isoformat()}", "title_fa": str(question)[:80],
                 "source": "آموزش AI", "category": "edu", "published": True,
                 "published_at": datetime.utcnow().isoformat() + "Z"}
        if message_id:
            entry["message_id"] = message_id
        log.append(entry)
        storage.save_published(log)
    except Exception as e:  # noqa: BLE001
        print(f"[EDU] quiz log failed: {type(e).__name__}")


def _send_quiz(est, now_utc) -> bool:
    """
    یک کوییز در روز. اول تلاش می‌کند سؤال را از خبرهای تازهٔ همان روز بسازد
    (مرتبط و جدید)، و اگر مدل‌ها خطا دادند یا خروجی بی‌کیفیت بود، از بانک
    آمادهٔ چرخشی استفاده می‌کند تا شب بدون کوییز نماند.
    """
    used = list(est.get("quiz_used") or [])
    q, via = None, ""
    news = _recent_news_titles(now_utc)
    if news:
        gemini_ok = bool(GEMINI_API_KEY) and gemini_budget.can_call(min_interval_min=0)
        try:
            raw = generate_quiz(news, accept=quiz.validate, use_gemini=gemini_ok)
            q = quiz.validate(raw)
            if q and gemini_ok and _RUN.get("gemini_calls"):
                gemini_budget.mark_called()
        except Exception as e:  # noqa: BLE001
            print(f"[EDU] quiz generation failed: {type(e).__name__}")
        via = "model"
    if not q:
        q, qid = quiz.pick(int(now_utc.timestamp()) // 3600, used)
        via = "bank"
    else:
        qid = f"gen-{local_now(now_utc).date().isoformat()}"

    res = send_poll(CHANNEL_ID, q["question"], q["options"], q["correct"], q.get("explanation"))
    if not (res and res.get("ok")):
        print(f"[EDU] quiz send failed: {res}")
        return False

    est["quiz_used"] = (used + [qid])[-20:]
    est["quiz_last_date"] = local_now(now_utc).date().isoformat()
    print(f"[EDU] Quiz published via {via}: {q['question'][:50]}")
    _log_quiz(q["question"], (res.get("result") or {}).get("message_id"))
    return True


def _publish_due(est, now_utc):
    """
    انتشار با سقف روزانه (EDU_POSTS_PER_DAY) و جبران تأخیر.

    چرا این‌طور؟ GitHub Actions اجرای زمان‌بندی‌شده را گاهی عقب می‌اندازد. قبلاً
    اگر ساعت ۱۹ از دست می‌رفت، آن پست تا آخر شب جبران نمی‌شد. حالا تعداد پست‌های
    امروز شمرده می‌شود و تا رسیدن به سقف ادامه دارد (حداکثر EDU_CATCHUP_MAX در
    یک اجرا). اگر صف خالی باشد (مدل‌ها شکست خوردند)، از پست آمادهٔ محلی استفاده
    می‌شود تا شب بدون پست آموزشی نماند.
    """
    loc = local_now(now_utc)
    hours = sorted(EDU_PUBLISH_HOURS)
    if not hours or loc.hour < hours[0] or loc.hour > hours[-1]:
        return

    day = loc.date().isoformat()
    target = max(1, EDU_POSTS_PER_DAY)
    counts, done = _pub_count(est, day)
    if done >= target:
        # متن‌ها تمام شده‌اند؛ کوییز مستقل است و اگر امروز نرفته، همین اجرا می‌رود
        if _quiz_due(est, loc) and _send_quiz(est, now_utc):
            cloudflare_kv.put(KEY, est)
        return

    key = f"{day}#{loc.hour}"
    behind = target - done
    remaining_slots = len([h for h in hours if h >= loc.hour])
    catching_up = behind > remaining_slots  # اجراهای قبلی از دست رفته‌اند
    if est.get("last_pub_key") == key and not catching_up:
        return  # در این ساعت قبلاً پست رفته (اجرای تکراری/force)

    batch = min(behind, max(1, EDU_CATCHUP_MAX)) if catching_up else 1
    sent = 0
    for _ in range(batch):
        item = _peek_queue(est)
        from_builtin = False
        if item is None:
            if not EDU_USE_BUILTIN_FALLBACK:
                break
            recent = [r.get("title", "") for r in est.get("recent", []) if isinstance(r, dict)]
            post, tag = builtin_item(done + sent, recent)
            item = {"post": post, "tag": tag, "slot": "builtin", "via": "builtin/local",
                    "made_at": datetime.utcnow().isoformat() + "Z"}
            from_builtin = True

        post, tag = item.get("post"), item.get("tag", "")
        try:
            text = format_html(post, tag)
        except Exception as e:  # noqa: BLE001
            print(f"[EDU] format failed: {type(e).__name__}: {e}")
            continue

        result = send_message(CHANNEL_ID, text)
        if not (result and result.get("ok")):
            where = "builtin" if from_builtin else "queue"
            print(f"[EDU] Telegram send failed: {result} — stays in {where}")
            break

        if from_builtin:
            recent_list = est.get("recent") if isinstance(est.get("recent"), list) else []
            recent_list.append({"title": post["title"], "tag": tag, "via": "builtin/local",
                                "at": datetime.utcnow().isoformat() + "Z"})
            est["recent"] = recent_list[-EDU_RECENT_KEEP:]
        else:
            est["queue"] = (est.get("queue") or [])[1:]
        sent += 1
        print(f"[EDU] Published: {post['title'][:50]} ({tag}) at {loc.hour}:00 — "
              f"{done + sent}/{target} today, queue left: {len(est.get('queue') or [])}")

        # لاگ انتشار (برای /stats) — خطا نباید چیزی را خراب کند
        try:
            log = storage.load_published()
            log.append({"id": f"edu-{item.get('slot', key)}", "title_fa": post["title"], "source": "آموزش AI",
                        "category": "edu", "published": True, "published_at": datetime.utcnow().isoformat() + "Z"})
            storage.save_published(log)
        except Exception as e:  # noqa: BLE001
            print(f"[EDU] log failed: {type(e).__name__}")

        bale.reset()
        bale.send_text(format_plain(post, tag))

    # کوییز پست پنجم است: مستقل از سقف ۴ پست متنی، یک‌بار در روز
    quiz_sent = _send_quiz(est, now_utc) if _quiz_due(est, loc) else False

    if sent or quiz_sent:
        if sent:
            est["last_pub_key"] = key
            counts[day] = done + sent
            est["pub_count"] = {k: counts[k] for k in sorted(counts)[-3:]}
        cloudflare_kv.put(KEY, est)
