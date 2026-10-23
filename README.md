# 🤖 AI News Bot — کانال تلگرام اخبار هوش مصنوعی

یک ربات تلگرام که به‌صورت خودکار اخبار مدل‌های هوش مصنوعی را از سطح اینترنت جمع‌آوری، به فارسی خلاصه و در کانال شما منتشر می‌کند.

## ویژگی‌ها

- 📰 **جمع‌آوری خودکار** از +19 منبع معتبر
- 🇮🇷 **خلاصه‌سازی فارسی** با Gemini — **بودجه‌دار**: حداکثر ۱ درخواست در ساعت و ۲۴ در روز
- 🧾 **ترجمه ساده** با Google Translate برای باقی اخبار (بدون مصرف Gemini)
- 🇮🇷 **اخبار فارسی هرگز به Gemini نمی‌روند** (پردازش محلی)
- 🔁 **ضدتکرار** — هر خبر فقط یک‌بار در کانال منتشر می‌شود
- 🧠 **دسته‌بندی هوشمند**: مهم/متوسط/کم + قطعی/احتمالی/شایعه
- ✅ **تایید شما**: اخبار مهم و غیرقطعی قبل از انتشار بررسی می‌شوند
- ⭐️ **ویژه**: قابلیت پین کردن خبر مهم
- ✏️ **ویرایش**: تغییر متن خبر قبل از انتشار
- 🤖 **انتشار خودکار**: اخبار مهم + منبع معتبر + قطعی
- 📡 **مسیر سریع**: انتشار فوری بدون هیچ درخواستی به Gemini
- 💰 **کاملاً رایگان**

## منطق انتشار (سه لایه)

```
                    ┌─────────────────┐
                    │   خبر جدید       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Auto-Reject    │
                    │  (کلمات مشکوک)   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───────┐ ┌───▼────┐ ┌──────▼──────┐
     │   Fast-Track    │ │ Gemini │ │   Trusted    │
     │  (بدون Gemini)  │ │ Analyze│ │  + Confirmed │
     └────────┬───────┘ └───┬────┘ └──────┬──────┘
              │              │              │
              ▼              ▼              ▼
         📡 انتشار      👀 تایید شما    ✅ خودکار
         (سریع)         (دکمه‌ها)        (بدون تایید)
```

### لایه ۱: انتشار خودکار (بدون تایید)
- ✅ منبع معتبر (OpenAI, Google AI, HuggingFace, MIT, Ars Technica)
- ✅ خبر قطعی (confirmed)
- ✅ مهمیت بالا (high)
- ✅ اطلاع به شما (بدون نیاز به تایید)

### لایه ۲: بررسی شما (تایید دستی)
- 🟡 منبع ناشناخته یا اعتبار متوسط
- 🟡 خبر غیرقطعی (likely/rumor)
- ❓ سوال LLM + دکمه تایید/رد/ویژه/ویرایش

### لایه ۳: مسیر سریع (Fast-Track)
- 📡 منابع فارسی → مستقیم فرمت + انتشار
- 📡 منابع انگلیسی → ترجمه ساده با Google Translate
- 🔍 فیلتر ساده: کلمات کلیدی AI
- ⏱️ بدون هیچ درخواستی به Gemini

### لایه ۴: رد خودکار
- ❌ کلمات مشکوک در عنوان (crypto, gambling, ...)
- ❌ منبع غیرقابل اعتبار
- ❌ خبر غیرمرتبط با AI

## بودجه Gemini (ضد بن شدن)

```
هر اجرا (هر ۱ ساعت):
  ├── ۱ درخواست به Gemini برای تحلیل ۱۰ خبر اول ← batch
  ├── بقیه اخبار → ترجمه ساده با Google Translate
  └── اگه بودجه تموم باشه → همه ترجمه ساده (کانال نمی‌خوابه)

محدودیت‌ها (در Cloudflare KV ذخیره می‌شن):
  • حداقل ۵۵ دقیقه فاصله بین دو درخواست
  • حداکثر ۲۴ درخواست در روز
  • اگه مدل منقضی بشه → خودکار مدل موجود پیدا می‌شه (۱ بار)
```

هیچ‌وقت روی چند مدل loop نمی‌زند و هیچ‌وقت بیش از یک درخواست در هر اجرا نمی‌فرستد.
اخبار فارسی (`lang == "fa"`) همیشه محلی پردازش می‌شوند و به Gemini نمی‌روند.

### محافظ‌های اجرای پایدار

| تنظیم | پیش‌فرض | کار |
|--------|---------|-----|
| `MAX_ARTICLES_PER_RUN` | 30 | سقف پردازش هر اجرا (جلوگیری از تایم‌اوت) |
| `MAX_REVIEWS_PER_RUN` | 5 | سقف پیام «در انتظار تایید» برای ادمین |
| `MAX_POSTS_PER_RUN` | 15 | سقف انتشار در **هر راند اجرا** (نه روزانه) |
| `MAX_POSTS_PER_DAY` | 0 (خاموش) | سقف امنیتی اختیاری روزانه |
| `COLLECT_INTERVAL_MIN` | 45 | حداقل فاصله بین دو جمع‌آوری |

> **نحوه انتشار در هر راند:** در ابتدای هر اجرا شمارنده پست صفر می‌شود و تا
> `MAX_POSTS_PER_RUN` پست (۱۵) به کانال می‌رود. اولویت: ویژه ← انتشار خودکار ←
> خبرهایی که خودت با دکمه تایید کرده‌ای. اگر به ۱۵ نرسید، مابقی از تاییدی‌های خودت
> پر می‌شود و اگر بیشتر باشد، بقیه در صف می‌ماند برای راند بعد.

اگر Google Translate سه بار پشت‌سرهم خطا بدهد، بقیهٔ اجرا معطل ترجمه نمی‌شود و خبر با
عنوان اصلی منتشر می‌شود (کانال هرگز نمی‌خوابد).

## معماری

```
GitHub Actions (هر ۱ ساعت)
  ├── Auto-Reject ← فیلتر کلمات مشکوک
  ├── Collect    ← RSS ۱۳+ منبع
  ├── Analyze    ← Gemini (۱ درخواست) + ترجمه ساده
  └── Publish    ← اخبار تایید/خودکار

Cloudflare Worker (۲۴/۷ همیشه فعال)
  ├── Webhook ← دستورات و دکمه‌های ادمین
  ├── Publish ← تایید فوری ادمین (بلافاصله در کانال)
  └── KV      ← وضعیت مشترک با Actions
```

## راه‌اندازی (۵ قدم)

### 1. ساخت ربات تلگرام
1. به [@BotFather](https://t.me/BotFather) پیام بدهید
2. `/newbot` → نام و یوزرنیم انتخاب کنید
3. **توکن** را کپی کنید

### 2. ساخت کانال تلگرام
1. کانال بسازید
2. ربات را به‌عنوان **ادمین** اضافه کنید (دسترسی ارسال پیام)
3. آیدی عددی کانال را پیدا کنید (از [@userinfobot](https://t.me/userinfobot))
4. آیدی عددی خودتان را هم از همان ربات بگیرید

### 3. ساخت Gemini API Key
1. به [Google AI Studio](https://aistudio.google.com/apikey) بروید
2. **Create API Key** بزنید
3. کی را کپی کنید

### 4. ساخت مخزن GitHub
1. یک **مخزن عمومی (Public)** بسازید (برای رایگان ماندن Actions)
2. پوشه `data/` و `.github/` را بسازید
3. همه فایل‌های پروژه را آپلود کنید

### 5. ساخت Cloudflare KV

1. وارد [Cloudflare Dashboard](https://dash.cloudflare.com) شوید → **Workers & Pages** → **KV**
2. **Create namespace** → نام: `ai-news-bot`
3. دو مقدار لازم دارید:
   - **Account ID** (از صفحه اصلی Workers)
   - **Namespace ID** (از صفحهٔ همان KV)

### 6. تنظیم Secrets در GitHub

مخزن → **Settings** → **Secrets and variables** → **Actions** → این‌ها را اضافه کنید:

| Secret | مقدار |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | توکن ربات |
| `TELEGRAM_CHANNEL_ID` | `@ai_news_bot_telegram` یا آیدی عددی |
| `TELEGRAM_ADMIN_ID` | آیدی عددی ادمین |
| `GEMINI_API_KEY` | کلید Gemini |
| `CF_ACCOUNT_ID` | Account ID کلادفلر |
| `CF_KV_NAMESPACE` | Namespace ID |
| `CF_API_TOKEN` | توکن API با دسترسی **Workers KV Storage → Edit** |

### 7. تنظیم Worker در Cloudflare

1. **Workers & Pages** → **Create** → **Worker** → نام: `ai-news-bot-worker`
2. کد `worker/index.js` را در **Edit code** جای‌گذاری کنید → **Save and Deploy**
3. **Settings → Variables and Secrets** → این‌ها را به‌صورت **Secret** اضافه کنید:
   - `BOT_TOKEN` — توکن ربات
   - `CHANNEL_ID` — آیدی کانال
   - `ADMIN_ID` — آیدی عددی ادمین
   - `WORKER_SECRET` — یک رشتهٔ تصادفی (برای محافظت از `/run`)
4. **Settings → Bindings** → **KV Namespace** → نام متغیر: `KV` (باید دقیقاً `KV` باشد) → همان namespace
5. آدرس Worker را کپی کنید (مثلاً `https://ai-news-bot-worker.xxx.workers.dev`)

### 8. اتصال Webhook

یک‌بار این آدرس را در مرورگر باز کنید:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<WORKER_URL>/webhook
```

باید `{"ok":true,...}` ببینید. سپس در ربات `/start` بزنید.

> اگر `WEBHOOK_SECRET` را در Worker تنظیم کردید، باید `&secret_token=<همان مقدار>` را هم به آدرس بالا اضافه کنید.

### 9. (اختیاری) اجرای مطمئن هر ساعت

GitHub گاهی اجرای زمان‌بندی‌شده را ۴-۵ ساعت عقب می‌اندازد. برای اجرای قطعی هر ساعت:

1. یک **GitHub Fine-grained Token** بسازید با دسترسی **Actions: Read and write** روی همین مخزن.
2. در Worker دو Secret اضافه کنید:
   - `GITHUB_TOKEN` — همان توکن
   - `GITHUB_REPO` — `MahdiHajiabadi675/ai-news-bot-telegram`
3. در Cloudflare → Worker → **Settings → Triggers → Cron Triggers** → `17 * * * *`

اگر این‌ها تنظیم نشوند، همان cron خود GitHub (`17 * * * *`) کار را انجام می‌دهد.

تمام! ✅ ربات هر ۱ ساعت اجرا می‌شود و Worker به‌صورت ۲۴/۷ فعال است.

## دستورات ربات

| دستور | توضیح |
|--------|---------|
| `/start` | شروع و نمایش منو |
| `/status` | وضعیت ربات |
| `/pending` | اخبار در انتظار تایید |
| `/pause` | توقف انتشار |
| `/resume` | ادامه انتشار |
| `/auto on\|off` | انتشار خودکار اخبار مهم |
| `/stats` | آمار انتشار |

## هزینه‌ها

| سرویس | هزینه |
|--------|--------|
| GitHub Actions (Public) | **رایگان** |
| Gemini API (Free tier) | **رایگان** |
| Telegram Bot API | **رایگان** |
| RSS Feeds | **رایگان** |
| **جمع** | **$0** |

## ساختار پروژه

```
ai-news-bot/
├── .github/workflows/pipeline.yml  # اجرای خودکار هر ۱ ساعت
├── src/
│   ├── config.py                   # تنظیمات + امتیاز منابع + بودجه Gemini
│   ├── gemini_budget.py            # محدودیت سخت درخواست‌های Gemini
│   ├── storage.py                  # مدیریت وضعیت (Cloudflare KV)
│   ├── collectors.py               # جمع‌آوری RSS
│   ├── analyzer.py                 # تحلیل Gemini (بودجه‌دار) + ترجمه ساده
│   ├── fast_track.py               # انتشار سریع بدون Gemini
│   ├── fallback_translator.py      # ترجمه با Google Translate
│   ├── bot.py                      # ربات مدیریت (پیام‌های ادمین)
│   └── publisher.py                # انتشار کانال
├── worker/
│   └── index.js                    # Cloudflare Worker (۲۴/۷)
├── data/                           # وضعیت (خودکار به‌روز می‌شود)
├── main.py                         # نقطه ورود
├── requirements.txt
└── README.md
```

## منابع اخبار

**معتبر (Trust 5):** OpenAI Blog, Google AI, DeepMind, Anthropic, Meta AI, HuggingFace, Microsoft AI

**معتبر (Trust 4):** MIT Tech Review, Ars Technica AI, TechCrunch AI, Papers with Code

**متوسط (Trust 3):** VentureBeat AI, The Verge AI, Wired AI, AI News, TechCrunch, The Verge, Ars Technica, دیجیاتو, زومگ, تک‌راسا

## امتیاز اعتبار منابع

| منبع | امتیاز | رفتار |
|------|--------|--------|
| OpenAI, Google AI, HuggingFace | 5/5 | خودکار |
| MIT, Ars Technica, TechCrunch | 4/5 | خودکار |
| VentureBeat, The Verge, Wired | 3/5 | بررسی شما |
| AI News, دیجیاتو, تک‌راسا | 3/5 | بررسی شما |

## منابع اخبار

برای اضافه کردن منبع جدید، کافیست به لیست `FEEDS` در `src/collectors.py` اضافه کنید:

```python
{"name": "منبع جدید", "url": "https://...", "lang": "en", "trusted": False, "fast_track": True}
```
