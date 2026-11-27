"""
تست دودی (Smoke tests) — بدون نیاز به شبکه یا نصب وابستگی‌ها

اجرا:
    python tests/test_smoke.py

این تست‌ها روی منطق مهم تمرکز دارن:
  • مترجم چندمنبعی (زنجیرهٔ fallback، برچسب «ترجمه نشده»)
  • اعتبار پویای منابع و تایید چندمنبعی
  • طبقه‌بندی محلی (بدون Gemini)
  • قالب پیام (متن فارسی + اصل)، دکمهٔ پاک‌کردن صف
  • امتیازدهی، dedup و انتشار
"""
import os
import sys
import types
import datetime

# خروجی UTF-8 (ویندوز به‌صورت پیش‌فرض cp1252 است)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── مسیر پروژه ──
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── stub ماژول‌های خارجی (تا تست بدون نصب اجرا بشه) ──
for name in ("bs4", "feedparser", "requests"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["bs4"].BeautifulSoup = object

os.environ.setdefault("CF_ACCOUNT_ID", "test")
os.environ.setdefault("CF_KV_NAMESPACE", "test")
os.environ.setdefault("CF_API_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

# ── KV در حافظه ──
import src.cloudflare_kv as kv  # noqa: E402
_store = {}
kv.get = lambda key, default=None: _store.get(key, default)
kv.put = lambda key, value: _store.__setitem__(key, value)

import src.analyzer as analyzer  # noqa: E402
import src.gemini_budget as budget  # noqa: E402
import src.bot as bot  # noqa: E402
import src.classifier as classifier  # noqa: E402
import src.collectors as collectors  # noqa: E402
import src.formatting as formatting  # noqa: E402
import src.source_trust as source_trust  # noqa: E402
import src.translator as translator  # noqa: E402
import src.publisher as publisher  # noqa: E402
import src.digest as digest  # noqa: E402
import src.cluster as cluster  # noqa: E402
import src.quiz as quiz  # noqa: E402
import src.hn as hn  # noqa: E402
import src.arxiv_daily as arxiv_daily  # noqa: E402
import src.bluesky as bluesky  # noqa: E402
import src.hashtags as hashtags  # noqa: E402
import src.topics as topic_prefs  # noqa: E402
import src.storage as storage  # noqa: E402

_passed = 0


def check(label, condition):
    global _passed
    assert condition, f"FAILED: {label}"
    _passed += 1
    print(f"  ok - {label}")


# ───────────── dry-run: هیچ ارسال و هیچ نوشتنی ─────────────
# این بلوک عمداً همین‌جا (قبل از هر monkeypatch) است تا send_message واقعی تست شود.
print("Dry run:")
_orig_bot_dry = bot.DRY_RUN
_orig_storage_dry = storage.DRY_RUN
_orig_post = getattr(bot.requests, "post", None)
_dry_calls = []
bot.DRY_RUN = True
bot.requests.post = lambda *a, **k: _dry_calls.append(1)
_res = bot.send_message("@chan", "<b>سلام</b>")
check("dry-run: send_message درخواستی نمی‌فرستد و ok برمی‌گرداند",
      _dry_calls == [] and _res.get("ok") is True and _res.get("dry_run") is True)

_store.clear()
storage.DRY_RUN = True
storage.save_state({"daily_post_count": 3})
check("dry-run: هیچ کلیدی در KV نوشته نمی‌شود", _store == {})

storage.DRY_RUN = _orig_storage_dry
bot.DRY_RUN = _orig_bot_dry
if _orig_post is None:
    del bot.requests.post
else:
    bot.requests.post = _orig_post
check("dry-run: بعد از خاموش‌شدن، نوشتن عادی برمی‌گردد", storage.DRY_RUN is False)


def reset_budget():
    _store.clear()
    budget._called_this_process = False


def today():
    return datetime.datetime.utcnow().date().isoformat()


def minutes_ago(m):
    return (datetime.datetime.utcnow() - datetime.timedelta(minutes=m)).isoformat() + "Z"


# ───────────── بودجه Gemini ─────────────
print("Gemini budget:")
reset_budget()
check("اولین درخواست مجاز است", budget.can_call() is True)
budget.mark_called()
check("درخواست دوم در همان اجرا مسدود است", budget.can_call() is False)

reset_budget()
budget.mark_called()
budget._called_this_process = False
check("بلافاصله بعدش مسدود است", budget.can_call() is False)
_store["gemini_budget"]["last_call_at"] = minutes_ago(60)
check("بعد از ۶۰ دقیقه مجاز است", budget.can_call() is True)

reset_budget()
_store["gemini_budget"] = {"date": today(), "count": 999, "last_call_at": minutes_ago(300)}
check("سقف روزانه مسدود می‌کند", budget.can_call() is False)

reset_budget()
_store["gemini_budget"] = {"date": "2000-01-01", "count": 999, "last_call_at": minutes_ago(300)}
check("روز جدید شمارنده را صفر می‌کند", budget.can_call() is True)


# ───────────── مترجم ─────────────
print("Translator:")
calls = []


def make(name, fail=False, prefix=None):
    def fn(text, src):
        calls.append(name)
        if fail:
            raise RuntimeError("down")
        return (prefix or name) + ":" + text
    return fn


def set_providers(*specs):
    translator.PROVIDERS[:] = [(n, make(n, f), lim) for n, f, lim in specs]
    translator.reset()
    calls.clear()


set_providers(("google", False, 1500))
check("ترجمهٔ ساده", translator.translate("hello", "en") == "google:hello")

set_providers(("google", True, 1500), ("mymemory", False, 450))
check("گوگل خراب → مترجم بعدی", translator.translate("hello", "en") == "mymemory:hello")
for _ in range(5):
    translator.translate("again " + str(_), "en")
check("بعد از ۳ خطا، گوگل کنار گذاشته می‌شود", calls.count("google") == 3)
check("بقیهٔ اجرا با مترجم دوم ادامه می‌یابد", calls.count("mymemory") >= 6)

set_providers(("google", True, 1500), ("mymemory", True, 450))
check("همه خراب → None", translator.translate("hello", "en") is None)
r = translator.translate_article("Title", "Summary text", "en")
check("همه خراب → translated=False با متن اصلی",
      r["translated"] is False and r["title_fa"] == "Title")

set_providers(("google", False, 100))
long_text = " ".join(f"Sentence number {i} is here." for i in range(40))
out = translator.translate(long_text, "en")
check("متن بلند تکه‌تکه ترجمه می‌شود", calls.count("google") > 1 and out.count("google:") == calls.count("google"))
check("تکه‌ها از سقف بیشتر نیستند",
      all(len(c) <= 100 for c in translator._split_chunks(long_text, 100)))

set_providers(("google", False, 1500))
r = translator.translate_article("Title", "", "en")
check("خلاصهٔ خالی مشکلی ایجاد نمی‌کند", r["translated"] is True and r["summary_fa"] == "")


# ───────────── اعتبار پویای منابع ─────────────
print("Dynamic source trust:")
NOWT = datetime.datetime(2026, 1, 1)
stats = {}
base = source_trust.trust_of("OpenAI Blog", stats, NOWT)
check("بدون شواهد = پیش‌فرض اولیه", base == 5.0)
check("منبع ناشناخته = ۳", source_trust.trust_of("???", stats, NOWT) == 3.0)

st = {}
for _ in range(20):
    source_trust.record_decision(st, "دیجیاتو", "approve", now=NOWT)
check("منبع پیش‌فرض ۳ با ۲۰ تایید به انتشار خودکار می‌رسد",
      source_trust.trust_of("دیجیاتو", st, NOWT) >= 4.0)

st = {}
for _ in range(6):
    source_trust.record_decision(st, "OpenAI Blog", "reject", now=NOWT)
check("منبع ۵ با ۶ رد پیاپی از آستانهٔ خودکار پایین می‌آید",
      source_trust.trust_of("OpenAI Blog", st, NOWT) < 4.0)

st = {}
source_trust.record_decision(st, "Wired AI", "approve", now=NOWT)
t_now = source_trust.trust_of("Wired AI", st, NOWT)
t_later = source_trust.trust_of("Wired AI", st, NOWT + datetime.timedelta(days=300))
check("شواهد قدیمی زوال می‌یابند (برمی‌گردد به پیش‌فرض)", abs(t_later - 3.0) < abs(t_now - 3.0))

st = {}
source_trust.record_decision(st, "x", "feature", now=NOWT)
check("«ویژه» وزن بیشتری دارد", st["x"]["pos"] == 1.5)
before = dict(st["x"])
source_trust.record_decision(st, "x", "clear_queue", now=NOWT)
check("پاک‌کردن صف در آمار ثبت نمی‌شود", st["x"] == before)

arts = [
    {"id": "a", "source": "S1", "title": "OpenAI releases GPT-6 model with huge context window"},
    {"id": "b", "source": "S2", "title": "OpenAI launches new GPT-6 model, context window huge"},
    {"id": "c", "source": "S1", "title": "Nvidia earnings beat expectations again"},
    {"id": "d", "source": "S1", "title": "OpenAI releases GPT-6 model with huge context window"},
]
cc = source_trust.corroboration_counts(arts)
check("خبر یکسان در دو منبع → ۱ تاییدکنندهٔ مستقل", cc["a"] == 1 and cc["b"] == 1)
check("خبر نامرتبط تایید نمی‌شود", cc["c"] == 0)
check("تکرار در همان منبع، تایید مستقل حساب نمی‌شود", cc["b"] == 1 and cc["d"] == 1)
check("سقف پاداش تایید چندمنبعی", source_trust.with_corroboration(4.8, 5) == 5.0)


# ───────────── طبقه‌بندی محلی ─────────────
print("Classifier:")
check("'said' دیگر به‌عنوان AI شناخته نمی‌شود", classifier.is_ai_related("He said the maintainer left") is False)
check("کلمهٔ AI شناخته می‌شود", classifier.is_ai_related("New AI model from Meta") is True)
check("کلیدواژهٔ فارسی شناخته می‌شود", classifier.is_ai_related("پیشرفت هوش مصنوعی در ایران") is True)
check("«ماشین» (خودرو) AI نیست", classifier.is_ai_related("قیمت ماشین امروز") is False)

rel = classifier.ai_relevance
PASS = 3
check("خبر بدون کلمهٔ «AI» ولی دربارهٔ AI: نام شرکت/مدل کافی است",
      rel("Sam Altman says compute is the bottleneck") >= PASS)
check("نام محصول در عنوان کافی است", rel("Meta releases Llama 4 with longer context") >= PASS)
check("نام مدل در خلاصه کافی است", rel("Chrome adds a tab feature", "Uses Gemini to summarize") >= PASS)
check("Nvidia در عنوان کافی است", rel("Nvidia unveils new chips") >= PASS)
check("فارسی: نام مدل بدون «هوش مصنوعی»", rel("ایلان ماسک از گراک ۵ رونمایی کرد") >= PASS)
check("یک واژهٔ ضعیف تنها کافی نیست", rel("Why my robot vacuum keeps getting stuck") < PASS)
check("دو نشانهٔ ضعیف (عنوان + خلاصه) کافی است",
      rel("Startup raises $50M for robots", "humanoid machines for warehouses") >= PASS)
check("خبر غیر AI رد می‌شود", rel("Cloudflare outage hits thousands of sites") == 0)
check("«رای آینده» با «ای آی» اشتباه گرفته نمی‌شود", rel("رای آینده مردم") == 0)
check("تگ RSS امتیاز می‌دهد", rel("Someone made a thing", "", "Artificial Intelligence") >= PASS)

c = classifier.classify({"title": "OpenAI releases new GPT model", "summary": ""}, 5.0, 0)
check("انتشار مدل از منبع معتبر → model_release/high/confirmed",
      (c["category"], c["importance"], c["confidence"]) == ("model_release", "high", "confirmed"))
c = classifier.classify({"title": "OpenAI reportedly working on new model", "summary": ""}, 3.0, 0)
check("متن شایعه‌نما از منبع معمولی → rumor", c["confidence"] == "rumor")
c = classifier.classify({"title": "Some random AI thing", "summary": ""}, 3.0, 0)
check("منبع معمولی بدون تایید → likely", c["confidence"] == "likely")
c = classifier.classify({"title": "Some random AI thing", "summary": ""}, 3.0, 2)
check("دو منبع مستقل → confirmed", c["confidence"] == "confirmed")


# ───────────── آنالایزر محلی (بدون Gemini) ─────────────
def article(i, lang="en", source="OpenAI Blog"):
    return {
        "id": f"id{i}", "source": source, "lang": lang, "trusted": True,
        "title": f"title {i}", "link": "http://x", "summary": "s",
    }


print("Analyzer (local):")
items = analyzer.analyze_articles([article(1), article(2, source="دیجیاتو")], {})
check("همه اخبار تحلیل می‌شوند", len(items) == 2)
check("اعتبار پویای منبع روی آیتم ثبت می‌شود",
      items[0]["source_trust"] == 5.0 and items[1]["source_trust"] == 3.0)
check("پیش‌فرض اولیه روی آیتم ثبت می‌شود (برای Worker)", items[1]["source_prior"] == 3)

set_providers(("google", False, 1500))
it = analyzer.localize(dict(items[0]))
check("خبر انگلیسی ترجمه می‌شود", it["title_fa"] == "google:title 1" and it["translated"] is True)
fa = analyzer.localize({**items[1], "lang": "fa", "title": "عنوان فارسی"})
check("خبر فارسی دست‌نخورده می‌ماند", fa["title_fa"] == "عنوان فارسی" and fa["translated"] is True)

set_providers(("google", True, 1500))
it = analyzer.localize(dict(items[0]))
check("شکست ترجمه → translated=False و متن اصلی", it["translated"] is False and it["title_fa"] == "title 1")

# منبع MIT Tech Review (همیشه ترجمه می‌شود، مثل بقیه)
set_providers(("google", False, 1500))
mit = analyzer.analyze_articles([article(9, source="MIT Tech Review")], {})
mit = analyzer.localize(mit[0])
check("MIT Tech Review هم ترجمه می‌شود", mit["translated"] is True and mit["title_fa"].startswith("google:"))


# ───────────── قالب پیام ─────────────
print("Message format:")
item_en = {"title_fa": "عنوان", "summary_fa": "خلاصه", "title": "Title", "summary": "Original summary",
           "lang": "en", "translated": True, "source": "MIT Tech Review", "link": "http://x",
           "hashtags": ["AI"]}
msg = formatting.format_message(item_en)
check("متن اصلی زیر ترجمه می‌آید", msg.index("عنوان") < msg.index("متن اصلی") < msg.index("Original summary"))
check("متن اصلی در بلوک جمع‌شونده است", "<blockquote expandable>" in msg)

msg = formatting.format_message({**item_en, "lang": "fa"})
check("منبع فارسی بلوک اصلی ندارد", "متن اصلی" not in msg)

msg = formatting.format_message({**item_en, "translated": False, "title_fa": "Title", "summary_fa": "Original summary"})
check("ترجمه‌نشده → برچسب", "ترجمه نشده" in msg)
check("ترجمه‌نشده → بلوک اصلی تکراری نیست", msg.count("Original summary") == 1)

msg = formatting.format_message({k: v for k, v in item_en.items() if k not in ("translated",)})
check("آیتم قدیمی (بدون translated) ترجمه‌شده فرض می‌شود", "ترجمه نشده" not in msg)

huge = {**item_en, "summary": "x" * 9000, "summary_fa": "y" * 9000}
check("پیام بلند از سقف تلگرام بیشتر نمی‌شود", len(formatting.format_message(huge)) <= 4000)
check("HTML اسکیپ می‌شود", "&lt;" in formatting.format_message({**item_en, "title_fa": "a<b"}))


# ───────────── پاک‌کردن صف ─────────────
print("Clear queue:")
sent_admin = []
bot.send_message = lambda chat_id, text, **kw: (sent_admin.append((text, kw.get("reply_markup"))) or {"ok": True})
bot.ADMIN_ID = 42
_store["pending"] = [{"id": "p1", "source": "S"}, {"id": "p2", "source": "S"}]
_store["approved"] = [{"id": "a1"}]
_store["source_stats"] = {}
bot._ask_clear(42)
kb = sent_admin[-1][1]["inline_keyboard"]
datas = [b["callback_data"] for row in kb for b in row]
check("تایید پاک‌کردن: سه گزینه", datas == ["clrp", "clra", "clrx"])
check("تا تایید نشود چیزی پاک نمی‌شود", len(_store["pending"]) == 2)

bot._handle_callback({"id": "c", "data": "clrp", "from": {"id": 42}}, {})
check("«فقط صف تایید» approved را دست نمی‌زند", _store["pending"] == [] and len(_store["approved"]) == 1)
check("پاک‌کردن در اعتبار منبع ثبت نمی‌شود", _store["source_stats"] == {})

_store["pending"] = [{"id": "p1"}]
bot._handle_callback({"id": "c", "data": "clra", "from": {"id": 42}}, {})
check("«همه» هر دو صف را پاک می‌کند", _store["pending"] == [] and _store["approved"] == [])

_store["pending"] = [{"id": "p1"}]
bot._handle_callback({"id": "c", "data": "clrp", "from": {"id": 999}}, {})
check("غیرادمین نمی‌تواند صف را پاک کند", len(_store["pending"]) == 1)

bot._handle_callback({"id": "c", "data": "clrx", "from": {"id": 42}}, {})
check("انصراف چیزی را پاک نمی‌کند", len(_store["pending"]) == 1)

_store["pending"] = [{"id": "p9", "source": "Wired AI", "source_prior": 3}]
_store["approved"] = []
bot._handle_callback({"id": "c", "data": "reject:p9", "from": {"id": 42}}, {})
check("رد ادمین در آمار منبع ثبت می‌شود", _store["source_stats"]["Wired AI"]["neg"] == 1.0)
_store["pending"] = [{"id": "p10", "source": "Wired AI", "source_prior": 3,
                      "title_fa": "t", "summary_fa": "s", "importance": "medium", "confidence": "likely",
                      "hashtags": [], "link": "http://x", "score": 50}]
bot._handle_callback({"id": "c", "data": "approve:p10", "from": {"id": 42}}, {})
check("تایید ادمین هم ثبت می‌شود", _store["source_stats"]["Wired AI"]["pos"] == 1.0)

sent_admin.clear()
bot._send_review({"id": "r1", "title_fa": "t", "summary_fa": "s", "importance": "medium", "confidence": "likely",
                  "source": "Wired AI", "source_trust": 3.4, "hashtags": ["AI"], "link": "http://x", "score": 55,
                  "title": "Orig", "lang": "en", "translated": True, "corroborated_by": 2})
txt, kb = sent_admin[-1]
check("پیام بررسی: دکمهٔ پاک‌کردن کل صف", any(b.get("callback_data") == "clearq" for row in kb["inline_keyboard"] for b in row))
check("پیام بررسی: اعتبار پویا و تایید چندمنبعی", "3.4/5" in txt and "2 منبع" in txt)
check("پیام بررسی: عنوان اصلی", "Orig" in txt)

# ───────────── جمع‌آوری و سلامت فیدها ─────────────
print("Collector & feed health:")
real_feeds = collectors.FEEDS
collectors.FEEDS = [
    {"name": "Good", "url": "u1", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Broken", "url": "u2", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Empty", "url": "u3", "lang": "en", "trusted": True, "fast_track": False},
    {"name": "Persian", "url": "u4", "lang": "fa", "trusted": False, "fast_track": False},
    {"name": "Tagged", "url": "u5", "lang": "en", "trusted": False, "fast_track": False},
]


class _Resp:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass


def _fake_get(url, **kw):
    if url == "u2":
        raise RuntimeError("403 blocked")
    return _Resp(url.encode())


class _Parsed:
    def __init__(self, entries): self.entries = entries


def _fake_parse(content):
    if content == b"u1":
        return _Parsed([{"title": "OpenAI ships X", "link": "http://a/1", "summary": "s"},
                        {"title": "OpenAI ships Y", "link": "http://a/2", "summary": "s"}])
    if content == b"u3":
        return _Parsed([])
    if content == b"u5":
        return _Parsed([
            {"title": "Someone made a thing", "link": "http://t/1", "summary": "x",
             "tags": [{"term": "Artificial Intelligence"}]},
            {"title": "Samsung Galaxy review", "link": "http://t/2", "summary": "x",
             "tags": [{"term": "Phones"}]},
        ])
    if content == b"u4":
        return _Parsed([
            {"title": "هوش\u200cمصنوعی جدید گوگل", "link": "http://p/1", "summary": ""},
            {"title": "معرفی گوشی جدید سامسونگ", "link": "http://p/2", "summary": ""},
        ])
    return _Parsed([])


collectors.requests.get = _fake_get
collectors.feedparser.parse = _fake_parse
collectors._clean_html = lambda h: h or ""
got = collectors.collect(set())
hl = collectors.collect.health
check("فید سالم: ok و تعداد آیتم", hl["Good"]["ok"] and hl["Good"]["items"] == 2 and hl["Good"]["new"] == 2)
check("فید خراب: ok=False و علت خطا", hl["Broken"]["ok"] is False and "blocked" in hl["Broken"]["err"])
check("فید خالی (RSS نیست) خراب حساب می‌شود", hl["Empty"]["ok"] is False and hl["Empty"]["err"])
check("نیم‌فاصله در «هوش‌مصنوعی» رد نمی‌شود", hl["Persian"]["new"] == 1 and hl["Persian"]["filtered"] == 1)
check("جمع‌آوری با وجود فید خراب ادامه می‌یابد", len(got) == 4)
check("تگ RSS «AI» کافی است حتی اگر در عنوان نباشد", hl["Tagged"]["new"] == 1 and hl["Tagged"]["filtered"] == 1)
check("نمونهٔ ردشده‌ها برای عیب‌یابی ثبت می‌شود", hl["Tagged"]["filtered_titles"] == ["Samsung Galaxy review"])
check("فید اختصاصی AI (trusted) فیلتر نمی‌شود",
      collectors.collect.health["Good"]["filtered"] == 0)
check("اخبار تکراری (seen) دوباره جمع نمی‌شوند",
      len(collectors.collect({a["id"] for a in got})) == 0)
collectors.FEEDS = real_feeds

# ── مرتب‌سازی بر اساس تاریخ، سقف سن، فیدهای بزرگ ──
import time as _time
_now = datetime.datetime.utcnow()


def _ts(hours_ago):
    return _time.gmtime((_now - datetime.timedelta(hours=hours_ago)).timestamp())


# فید بزرگِ «قدیمی‌ترین اول» (مثل آرشیو ۱۳۰۰ آیتمی OpenAI): ۹۰۰ آیتم کهنه + ۳ آیتم تازه در انتها
big = [{"title": f"Old OpenAI post {i}", "link": f"http://o/{i}", "summary": "s",
        "published_parsed": _ts(24 * 400 + i)} for i in range(900)]
big += [{"title": f"Fresh OpenAI post {i}", "link": f"http://n/{i}", "summary": "s",
         "published_parsed": _ts(i + 1)} for i in range(3)]
ordered = collectors._newest_first(big)
check("فید «قدیمی‌ترین اول»: جدیدترین‌ها به ابتدا می‌آیند",
      [e["title"] for e in ordered[:3]] == ["Fresh OpenAI post 0", "Fresh OpenAI post 1", "Fresh OpenAI post 2"])

collectors.FEEDS = [{"name": "Big", "url": "ub", "lang": "en", "trusted": True, "fast_track": False},
                    {"name": "NoDate", "url": "ud", "lang": "en", "trusted": True, "fast_track": False}]
_feeds_data = {b"ub": big,
               b"ud": [{"title": "A", "link": "http://d/1", "summary": "s"},
                       {"title": "B", "link": "http://d/2", "summary": "s"}]}
collectors.feedparser.parse = lambda content: _Parsed(_feeds_data[content])
got = collectors.collect(set())
titles = {a["title"] for a in got if a["source"] == "Big"}
check("از فید ۹۰۳ آیتمی فقط خبرهای تازه جمع می‌شود", titles == {f"Fresh OpenAI post {i}" for i in range(3)})
check("خبرهای کهنه شمرده می‌شوند (نه جمع)", collectors.collect.health["Big"]["old"] > 0)
check("سن جدیدترین خبر فید ثبت می‌شود", collectors.collect.health["Big"]["newest"] is not None)
check("فید بدون تاریخ هم کار می‌کند", collectors.collect.health["NoDate"]["new"] == 2)
check("تاریخ خبر روی آیتم می‌ماند", any(a["published_at"] for a in got if a["source"] == "Big"))

stats_t = {"Gone": {"pos": 0.0, "neg": 0.0, "prior": 3}, "GoneButJudged": {"pos": 2.0, "neg": 0.0, "prior": 3}}
source_trust.sync_priors(stats_t, ["OpenAI Blog"])
check("منبع حذف‌شده بدون سابقه از stats پاک می‌شود", "Gone" not in stats_t)
check("منبع حذف‌شده با سابقهٔ تصمیم نگه داشته می‌شود", "GoneButJudged" in stats_t)

_store.clear()
bad = {"Good": {"ok": True, "items": 2, "new": 2, "filtered": 0, "err": None},
       "Broken": {"ok": False, "items": 0, "new": 0, "filtered": 0, "err": "boom"}}
alerts = [storage.update_feed_health(bad) for _ in range(4)]
check("هشدار فید خراب دقیقاً در خطای سوم", [a == ["Broken"] for a in alerts] == [False, False, True, False])
check("شمارندهٔ خطای پیاپی", _store["feed_health"]["Broken"]["fails"] == 4)
bad["Broken"]["ok"] = True
storage.update_feed_health(bad)
check("بعد از سالم‌شدن شمارنده صفر می‌شود", _store["feed_health"]["Broken"]["fails"] == 0)
rep = bot.feeds_report(storage.load_feed_health())
check("گزارش /feeds", "سلامت فیدها" in rep and "Broken" in rep)
check("گزارش خالی", "هنوز" in bot.feeds_report({}))

print("Run log:")
log = [
    {"t": "2026-10-25T06:17:00Z", "forced": False, "collected": True, "feeds_ok": 22, "feeds_total": 26,
     "fetched": 6, "processed": 6, "analyzed": 5, "auto": 3, "pending": 2, "low_score": 0,
     "rejected_keyword": 1, "published": 3, "queue_pending": 2, "queue_approved": 0},
    {"t": "2026-10-25T05:17:00Z", "forced": False, "skipped": True, "collected": False, "published": 0},
]
rr = bot.runs_report(log)
check("/runs: ساعت محلی (+۳:۳۰)", "10-25 09:47" in rr)
check("/runs: قیف", "جدید 6 → تحلیل 5" in rr and "🤖3" in rr and "👀2" in rr and "فید 22/26" in rr)
check("/runs: اجرای بدون جمع‌آوری مشخص است", "جمع‌آوری نشد" in rr)
check("/runs خالی", "هنوز" in bot.runs_report([]))


# ───────────── امتیازدهی و dedup ─────────────
print("Scoring & dedup:")
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("mainmod", os.path.join(ROOT, "main.py"))
mainmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mainmod)

high = mainmod.calculate_score({
    "source": "OpenAI Blog", "importance": "high",
    "confidence": "confirmed", "category": "model_release"})
dyn = mainmod.calculate_score({
    "source": "OpenAI Blog", "source_trust": 2.0, "importance": "high",
    "confidence": "confirmed", "category": "model_release"})
low = mainmod.calculate_score({
    "source": "دیجیاتو", "importance": "medium",
    "confidence": "likely", "category": "other"})
check("خبر مهم و معتبر امتیاز کامل می‌گیرد", high == 100)
check("خبر معمولی امتیاز کمتری می‌گیرد", low < high)
check("اعتبار پویای پایین، امتیاز را کم می‌کند", dyn < high)

check("ID نرمال‌شده پایدار است",
      collectors._generate_id("T", "http://A.com/x/?utm=1") ==
      collectors._generate_id("T", "http://a.com/x"))

print("Collection helpers:")
mix = [{"source": "A", "id": 1}, {"source": "A", "id": 2}, {"source": "A", "id": 3},
       {"source": "B", "id": 4}, {"source": "C", "id": 5}]
order = [a["id"] for a in collectors.interleave_by_source(mix)]
check("ترتیب نوبتی بین منابع", order == [1, 4, 5, 2, 3])
check("سقف ۳ تایی هر سه منبع را نگه می‌دارد",
      {a["source"] for a in collectors.interleave_by_source(mix)[:3]} == {"A", "B", "C"})
check("auto_reject: «crypto» رد می‌شود", mainmod.auto_reject([{"title": "Crypto giveaway now"}]) == [])
check("auto_reject: «cryptography» (امنیت AI) رد نمی‌شود",
      len(mainmod.auto_reject([{"title": "New cryptography attack on AI models"}])) == 1)

print("Item key:")
check("کلید از id", bot._item_key({"id": "x"}) == "x")
check("کلید از hash (سازگاری قدیمی)", bot._item_key({"hash": "y"}) == "y")
check("کلید خالی", bot._item_key({}) == "")


# ───────────── تصمیم جمع‌آوری (باگ اصلی) ─────────────
print("Collection decision:")
NOW = datetime.datetime.utcnow()


def ago(minutes):
    return (NOW - datetime.timedelta(minutes=minutes)).isoformat() + "Z"


check("اجرای دستی (force) همیشه جمع‌آوری می‌کند",
      mainmod._should_collect(True, ago(1), NOW) is True)
check("اولین اجرا (بدون زمان قبلی) جمع‌آوری می‌کند",
      mainmod._should_collect(False, None, NOW) is True)
check("اجرای زمانی بعد از بازه جمع‌آوری می‌کند",
      mainmod._should_collect(False, ago(240), NOW) is True)
check("اجرای زمانی در بازه جمع‌آوری نمی‌کند",
      mainmod._should_collect(False, ago(10), NOW) is False)
check("زمان خراب → جمع‌آوری می‌کند",
      mainmod._should_collect(False, "not-a-date", NOW) is True)
check("زمان آینده (اختلاف ساعت) → جمع‌آوری می‌کند",
      mainmod._should_collect(False, ago(-120), NOW) is True)


# ───────────── state: پیش‌فرض انتشار خودکار ─────────────
print("State defaults:")
_store["state"] = {"auto_publish": False}
st = storage.load_state()
check("مقدار قدیمی auto_publish نادیده گرفته می‌شود", st["auto_publish_enabled"] is True)

_store["state"] = {"auto_publish_enabled": False}
check("false قدیمی خودکار روشن می‌شود",
      storage.load_state()["auto_publish_enabled"] is True)

_store["state"] = {"auto_publish_enabled": False, "auto_publish_set_by_admin": True}
check("خاموش‌کردن دستی ادمین حفظ می‌شود",
      storage.load_state()["auto_publish_enabled"] is False)


# ───────────── انتشار: ضدتکرار و مقاوم‌بودن ─────────────
print("Publisher:")
sent = []
publisher.send_message = lambda chat_id, text, **kw: (sent.append(text) or {"ok": True})

_store["published"] = [{"id": "dup", "published": True}]
_store["approved"] = [{"id": "dup", "title_fa": "t", "summary_fa": "s",
                       "source": "x", "link": "http://x", "hashtags": []}]
pstate = {"daily_post_count": 0,
          "daily_post_date": datetime.datetime.utcnow().date().isoformat()}
publisher.publish_approved(pstate)
check("خبر تکراری دوباره منتشر نمی‌شود", sent == [])
check("خبر تکراری از صف حذف می‌شود", _store["approved"] == [])

_store["approved"] = [{"id": "fresh", "title_fa": "t", "summary_fa": "s",
                       "source": "x", "link": "http://x", "hashtags": []}]
publisher.publish_approved(pstate)
check("خبر جدید منتشر می‌شود", len(sent) == 1)

txt = publisher.format_message({"title_fa": "a<b", "summary_fa": None,
                                "source": "s", "link": "http://x?a=1&b=2",
                                "hashtags": []})
check("فرمت پیام با فیلد ناقص کرش نمی‌کند", "&lt;" in txt)

# ───────────── سقف انتشار در هر راند (MAX_POSTS_PER_RUN) ─────────────
print("Publish per-run limit:")
_store["approved"] = [{"id": f"p{i}", "title_fa": f"t{i}", "summary_fa": "s",
                       "source": "x", "link": "http://x", "hashtags": []}
                      for i in range(20)]
pstate_run = {"daily_post_count": 0,
              "daily_post_date": datetime.datetime.utcnow().date().isoformat()}
sent_run = []
publisher.send_message = lambda chat_id, text, **kw: (sent_run.append(text) or {"ok": True})
publisher.publish_approved(pstate_run)
check("از ۲۰ خبر فقط ۱۵ تا در یک راند منتشر می‌شود", len(sent_run) == 15)
check("بقیه در صف approved می‌مانند", len(_store["approved"]) == 5)

# ───────────── جذابی خبر و انتشار خودکار (رسمی / جذاب) ─────────────
print("Newsworthiness:")
from src.newsworthiness import newsworthiness, hot_threshold
from src.classifier import classify as _cls
from src.analyzer import analyze_articles as _analyze


def _hot(title, trust=3.0, others=0, summary=""):
    it = {"title": title, "summary": summary}
    it.update(_cls(it, trust, others))
    it["corroborated_by"] = others
    return newsworthiness(it, trust)


check("آستانه با اعتبار بالاتر پایین‌تر می‌شود", hot_threshold(5) < hot_threshold(3) < hot_threshold(1))
_r = _hot("OpenAI releases GPT-6 with 1M-token context", 4.0)
check("انتشار مدل بزرگ جذاب است", _r["score"] >= _r["threshold"])
_r = _hot("Anthropic raises $2 billion at $60 billion valuation")
check("سرمایهٔ میلیاردی جذاب است", _r["score"] >= _r["threshold"])
_r = _hot("The best AI chatbots of 2026, ranked", 4.0)
check("لیست «بهترین‌ها» جذاب نیست", _r["score"] < _r["threshold"])
_r = _hot("How to use ChatGPT to write emails", 4.0)
check("آموزش جذاب نیست", _r["score"] < _r["threshold"])
_r = _hot("Startup raises $20M for AI agents")
check("سرمایهٔ کوچک استارتاپ ناشناس خودکار نمی‌شود", _r["score"] < _r["threshold"])
_r = _hot("OpenAI model update", 3.0, others=2)
check("تایید چندمنبعی جذابیت را بالا می‌برد", _r["score"] >= _r["threshold"])
_r = _hot("معرفی مدل جدید دیپ‌سیک با رکورد تازه در بنچمارک")
check("خبر فارسی مهم هم جذاب است", _r["score"] >= _r["threshold"])
_r = _hot("DeepSeek reportedly working on new model")
check("شایعه جذاب حساب نمی‌شود", _r["score"] < _r["threshold"])
check("بازهٔ امتیاز ۰..۱۰۰", all(0 <= _hot(t)["score"] <= 100 for t in ["x", "OpenAI launches GPT-9 $5 trillion"]))

_an = _analyze([{"id": "a1", "source": "OpenAI Blog", "title": "OpenAI releases GPT-6", "link": "http://x",
                 "summary": "", "official": True, "lang": "en"}], {})
check("analyzer فیلدهای official/hot را می‌سازد",
      _an[0]["official"] is True and "hot" in _an[0] and "hot_threshold" in _an[0] and isinstance(_an[0]["hot_reasons"], list))
import importlib, src.collectors as _col
_FEEDS = importlib.reload(_col).FEEDS  # تست‌های قبلی FEEDS را موقتاً عوض کرده‌اند
_off = {f["name"] for f in _FEEDS if f.get("official")}
check("منابع رسمی: OpenAI/HuggingFace/Anthropic/DeepMind", {"OpenAI Blog", "HuggingFace", "Anthropic", "DeepMind"} <= _off)
check("رسانه‌ها رسمی نیستند", not ({"TechCrunch AI", "MIT Tech Review", "دیجیاتو"} & _off))
check("فیدها نام/آدرس یکتا و کلیدهای لازم دارند",
      len(_FEEDS) == len({f["name"] for f in _FEEDS}) == len({f["url"] for f in _FEEDS})
      and all({"name", "url", "lang"} <= set(f) for f in _FEEDS))
check("همهٔ منابع رسمی trustworthy هستند", all(f.get("trusted") for f in _FEEDS if f.get("official")))
check("منابع تازه (Mistral/Qwen/smol/Import AI) اضافه شده‌اند",
      {"Mistral AI", "Qwen Blog", "AI News", "Import AI"} <= {f["name"] for f in _FEEDS})

_sent_rv = []
bot.send_message = lambda chat_id, text, **kw: (_sent_rv.append(text) or {"ok": True})
bot.ADMIN_ID = bot.ADMIN_ID or "1"
bot._send_review({"id": "h1", "title_fa": "t", "summary_fa": "s", "importance": "medium", "confidence": "likely",
                  "source": "x", "link": "http://x", "hashtags": [], "hot": 42, "hot_threshold": 60.0,
                  "hot_reasons": ["بازیگر بزرگ"]})
check("پیام بازبینی جذابیت را نشان می‌دهد", any("جذابیت: 42/100" in t for t in _sent_rv))

# ───────────── آینهٔ بله ─────────────
print("Bale mirror:")
import importlib as _il
from src import bale as _bale
_il.reload(_bale)
_posts = []


class _R:
    def __init__(self, ok): self._ok = ok; self.text = "x"
    def json(self): return {"ok": self._ok}


_mode = {"ok": True, "raise": False}


def _fake_post(url, json=None, timeout=None):
    _posts.append((url, json))
    if _mode["raise"]:
        raise RuntimeError("blocked")
    return _R(_mode["ok"])


_bale.requests.post = _fake_post
_bitem = {"title_fa": "عنوان", "summary_fa": "خلاصه", "title": "Orig", "lang": "en", "source": "OpenAI Blog",
          "link": "http://x/1", "hashtags": ["هوش_مصنوعی"], "confidence": "confirmed"}

for k in ("BALE_BOT_TOKEN", "BALE_CHANNEL_ID"):
    os.environ.pop(k, None)
check("بدون توکن: خاموش و بدون درخواست", _bale.mirror(_bitem) is False and not _posts)
os.environ["BALE_BOT_TOKEN"] = "tok"; os.environ["BALE_CHANNEL_ID"] = "@bc"
_bale.reset()
check("با توکن: خبر به بله می‌رود", _bale.mirror(_bitem) is True and "tapi.bale.ai/bottok/sendMessage" in _posts[0][0])
check("متن ساده بدون parse_mode/HTML", "parse_mode" not in _posts[0][1] and "<" not in _posts[0][1]["text"]
      and "http://x/1" in _posts[0][1]["text"] and "عنوان" in _posts[0][1]["text"])
check("حداکثر طول پیام", len(_bale.format_bale_message({**_bitem, "summary_fa": "ا" * 9000})) <= 4000)
_mode["raise"] = True; _posts.clear(); _bale.reset()
check("خطای شبکه: False و بدون استثنا", _bale.mirror(_bitem) is False)
_bale.mirror(_bitem); _bale.mirror(_bitem); _bale.mirror(_bitem)
check("بعد از ۲ خطای پشت‌سرهم بقیه رد می‌شوند", len(_posts) == 2)
_bale.reset(); _mode["raise"] = False

_store["approved"] = [dict(_bitem, id="m1"), dict(_bitem, id="m2")]
_store["published"] = []
_posts.clear(); _mode["ok"] = False
publisher.send_message = lambda chat_id, text, **kw: {"ok": True}
_ps = {"daily_post_count": 0, "daily_post_date": datetime.datetime.utcnow().date().isoformat()}
publisher.publish_approved(_ps)
check("رد شدن بله: تلگرام منتشر شد و چیزی در صف نماند", len(_store["published"]) == 2 and _store["approved"] == [])
check("شکست بله هیچ ردی در published/approved نمی‌گذارد", "bale" not in str(_store["published"]).lower())
_mode["ok"] = True
for k in ("BALE_BOT_TOKEN", "BALE_CHANNEL_ID"):
    os.environ.pop(k, None)

# ───────────── Auto-publish command variants ─────────────
print("Auto command parsing:")
check("auto: /auto on", bot._parse_auto_choice("/auto on") is True)
check("auto: /auto off", bot._parse_auto_choice("/auto off") is False)
check("auto: /auto_on", bot._parse_auto_choice("/auto_on") is True)
check("auto: /auto-off", bot._parse_auto_choice("/auto-off") is False)
check("auto: /auto@Bot on", bot._parse_auto_choice("/auto@MyBot on") is True)
check("auto: auto:off", bot._parse_auto_choice("auto:off") is False)
check("auto: auto_publish:on", bot._parse_auto_choice("auto_publish:on") is True)
check("auto: bare /auto -> menu", bot._parse_auto_choice("/auto") is None)
check("auto: اعداد فارسی منو", bot._fa_num(123) == "۱۲۳")

# ───────────── پست آموزشی (Gemini + OpenRouter رایگان) ─────────────
print("Edu post:")
import datetime as _dt
from src import edu_post as _edu

# تست‌ها بدون شبکه: کلید OpenRouter را خالی می‌کنیم (تست‌های fallback جداگانه تنظیمش می‌کنند)
_edu.OPENROUTER_API_KEY = ""
# پیش‌فرض باید طولانی باشد؛ برای آزمودن ریاضیات اسلات، بعداً موقتاً ۲ می‌کنیم
from src import config as _cfg
check("edu: ساخت هر ۶ ساعت و انتشار ۱۹ تا ۲۲", _cfg.EDU_EVERY_HOURS == 6 and _cfg.EDU_PUBLISH_HOURS == (19, 20, 21, 22))
_DEFAULT_PUB_HOURS = _cfg.EDU_PUBLISH_HOURS
check("edu: سقف توکن پیش‌فرض کم است", _cfg.EDU_MAX_OUTPUT_TOKENS <= 1000)
_edu.EDU_EVERY_HOURS = 2
# کوییز در تست‌های زمان‌بندی خاموش است (شمارش پست‌ها قابل پیش‌بینی بماند)؛
# تست جداگانهٔ کوییز پایین‌تر آن را روشن می‌کند.
_edu.EDU_QUIZ_PER_DAY = 0
_edu.EDU_PUBLISH_HOURS = tuple(range(24))  # تست‌های جریان ساده: انتشار هر ساعتی مجاز؛ زمان‌بندی واقعی پایین‌تر تست می‌شود
# در تست‌های جریان، «تور نجات» محلی خاموش است تا شمارش پست‌ها قابل پیش‌بینی بماند؛
# تست جداگانه‌ای پایین‌تر رفتار واقعی (با fallback روشن) را می‌سنجد.
_edu.EDU_USE_BUILTIN_FALLBACK = False

_good = {"title": "چطور با یک پرامپت ایمیل کاری بهتر بنویسی", "hook": "ایمیل‌های کاری معمولاً طولانی و مبهم‌اند؛ با این روش در چند ثانیه شفاف می‌شوند.",
         "steps": ["موضوع و مخاطب ایمیل را بنویس", "لحن دلخواه را مشخص کن", "از مدل بخواه دو نسخه بدهد"],
         "prompt": "برای [مخاطب] یک ایمیل کوتاه و محترمانه دربارهٔ [موضوع] بنویس. لحن: [رسمی/دوستانه].",
         "tip": "اگر نتیجه کلی بود، یک مثال از لحن خودت بده.", "hashtags": ["ایمیل", "بهره‌وری"]}
_v = _edu.validate(dict(_good), [])
check("خروجی خوب معتبر است", _v is not None and len(_v["steps"]) == 3)
check("بدون عنوان/هوک رد می‌شود", _edu.validate({**_good, "title": ""}, []) is None and _edu.validate({**_good, "hook": ""}, []) is None)
check("بدون مرحله و بدون پرامپت رد می‌شود", _edu.validate({**_good, "steps": ["x"], "prompt": ""}, []) is None)
check("متن غیرفارسی رد می‌شود", _edu.validate({**_good, "title": "How to write emails", "hook": "Short english text only here"}, []) is None)
check("عنوان تکراری/مشابه رد می‌شود", _edu.validate(dict(_good), [_good["title"]]) is None)
check("غیر dict رد می‌شود", _edu.validate(None, []) is None and _edu.validate("x", []) is None)
_dirty = _edu.validate({**_good, "hook": "سلام **دنیا** https://evil.example/x ببین `کد`"}, [])
check("لینک و مارک‌داون پاک می‌شود", "http" not in _dirty["hook"] and "*" not in _dirty["hook"] and "`" not in _dirty["hook"])
_html = _edu.format_html(_v, "پرامپت_آماده")
check("قالب HTML: عنوان، pre، هشتگ، برچسب AI", "<b>" in _html and "<pre>" in _html and "#پرامپت_آماده" in _html and "تولیدشده با هوش مصنوعی" in _html)
_xss = _edu.validate({**_good, "prompt": "<script>alert(1)</script> متن [x] و کافی برای عبور از حداقل طول پرامپت اینجا"}, [])
check("HTML خطرناک escape می‌شود", "<script>" not in _edu.format_html(_xss, "t") and "&lt;script&gt;" in _edu.format_html(_xss, "t"))
check("طول پست زیر سقف تلگرام", len(_edu.format_html({**_v, "prompt": "ا" * 750}, "t")) <= 4000)
check("نسخهٔ متنی بله بدون تگ HTML", "<" not in _edu.format_plain(_v, "t") and "پرامپت آماده" in _edu.format_plain(_v, "t"))
check("JSON داخل بلاک کد استخراج می‌شود", _edu._extract_json('```json\n{"a": 1}\n```') == {"a": 1})
check("متن بدون JSON → None", _edu._extract_json("sorry I cannot") is None)

# اسلات‌ها: ۱۲ در روز، چرخش ۱۲ دسته
_t0 = _dt.datetime(2026, 10, 27, 20, 17)  # = 23:47 تهران
_s = {_edu.current_slot(_t0 + _dt.timedelta(hours=i))[0] for i in range(24)}
check("در ۲۴ ساعت دقیقاً ۱۲ اسلات داریم", len(_s) == 12 or len(_s) == 13)
_a = _edu.current_slot(_dt.datetime(2026, 10, 27, 5, 17))  # 08:47 تهران
_b = _edu.current_slot(_dt.datetime(2026, 10, 27, 6, 17))  # 09:47 تهران (همان اسلات)
_c = _edu.current_slot(_dt.datetime(2026, 10, 27, 7, 17))  # 10:47 تهران (اسلات بعد)
check("دو اجرای یک اسلات = یک شناسه", _a[0] == _b[0] and _a[0] != _c[0])
_cats = {_edu.pick_category(_edu.current_slot(_dt.datetime(2026, 10, 27, 0, 17) + _dt.timedelta(hours=2 * i))[1])[0] for i in range(12)}
check("۱۲ اسلات پشت‌سرهم همهٔ ۱۲ دسته را می‌پوشاند", len(_cats) == 12)

# با بازهٔ ۲۴ ساعته، اجراهای همان روز همه یک اسلات‌اند (فقط یک پست در روز)
_edu.EDU_EVERY_HOURS = 24
check("بازهٔ ۲۴ ساعته: یک اسلات در روز",
      _edu.current_slot(_dt.datetime(2026, 10, 27, 0, 17))[0] ==
      _edu.current_slot(_dt.datetime(2026, 10, 27, 10, 17))[0])
_edu.EDU_EVERY_HOURS = 2

import json as _json
# تاب‌آوری Gemini: ۵۰۳ → تلاش مجدد → مدل جایگزین
_orig_post, _orig_get = _edu.requests.post, _edu.requests.get
_edu._sleep = lambda s: None
_edu.GEMINI_API_KEY = "k"
_edu._resolve_model = lambda: "gemini-9-flash"
_saved_kv = {}
_edu.cloudflare_kv.put = lambda k, v: _saved_kv.__setitem__(k, v) or True


class _Resp:
    def __init__(self, code, body=None): self.status_code = code; self._b = body or {}; self.text = ""
    def json(self): return self._b


_OK_BODY = {"candidates": [{"content": {"parts": [{"text": _json.dumps(_good)}]}}]}
_models_body = {"models": [{"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
                           {"name": "models/gemini-8-flash", "supportedGenerationMethods": ["generateContent"]},
                           {"name": "models/gemini-8-flash-lite", "supportedGenerationMethods": ["generateContent"]}]}
_seq = {"calls": []}


def _mk_post(plan):
    def _post(url, json=None, params=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        _seq["calls"].append(model)
        code = plan.get(model, [200])
        c = code.pop(0) if len(code) > 1 else code[0]
        return _Resp(c, _OK_BODY if c == 200 else {})
    return _post


_edu.requests.get = lambda url, params=None, timeout=None: _Resp(200, _models_body)
_edu.requests.post = _mk_post({"gemini-9-flash": [503, 200]})
_seq["calls"].clear()
check("۵۰۳ موقتی: تلاش مجدد روی همان مدل موفق می‌شود", _edu.generate("x", [])["title"] == _good["title"] and _seq["calls"] == ["gemini-9-flash"] * 2)
_edu.requests.post = _mk_post({"gemini-9-flash": [503], "gemini-8-flash": [200]})
_seq["calls"].clear(); _saved_kv.clear()
_r = _edu.generate("x", [])
check("۵۰۳ پایدار: مدل جایگزین (غیر lite) جواب می‌دهد و ذخیره می‌شود",
      _r is not None and _seq["calls"][:3] == ["gemini-9-flash"] * 3 and _seq["calls"][-1] == "gemini-8-flash"
      and _saved_kv.get("gemini_model") == "gemini-8-flash" and "gemini-8-flash-lite" not in _seq["calls"])
_edu.requests.post = _mk_post({"gemini-9-flash": [503], "gemini-8-flash": [503]})
check("همه ۵۰۳: None بدون استثنا", _edu.generate("x", []) is None)
_edu.requests.post = _mk_post({"gemini-9-flash": [404]})
_seq["calls"].clear()
check("۴۰۴: بدون تلاش مجدد/جایگزین، None", _edu.generate("x", []) is None and _seq["calls"] == ["gemini-9-flash"])
# سقف زمانی: حتی اگر هر درخواست آویزان بماند، کل کار از MAX_TOTAL_SECONDS فراتر نمی‌رود
import time as _time
_clock = {"t": 0.0}
_real_mono = _edu.time.monotonic
_edu.time.monotonic = lambda: _clock["t"]
_edu._sleep = lambda s: _clock.__setitem__("t", _clock["t"] + s)


def _slow_post(url, json=None, params=None, timeout=None):
    _clock["t"] += timeout
    return _Resp(503)


_edu.requests.post = _slow_post
_edu.generate("x", [])
check("سقف کل زمان ساخت پست رعایت می‌شود", _clock["t"] <= _edu.MAX_TOTAL_SECONDS + _edu.CALL_TIMEOUT)
_edu.time.monotonic = _real_mono
_edu._sleep = lambda s: None
_edu.requests.post, _edu.requests.get = _orig_post, _orig_get

# ── fallback مدل‌های رایگان OpenRouter ──
import json as _json
_edu.OPENROUTER_API_KEY = "or-test"
_edu.OPENROUTER_MAX_MODELS = 3
_edu.EDU_MAX_OUTPUT_TOKENS = 700
_orig_or_post, _orig_or_get = _edu.requests.post, _edu.requests.get
_orig_alternatives = _edu._alternatives

# فقط مدل‌های :free یا با قیمت صفر انتخاب شوند
_edu.requests.get = lambda url, params=None, timeout=None: _Resp(200, {"data": [
    {"id": "vendor/paid-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
    {"id": "vendor/free-model:free", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "vendor/vision-free:free", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "vendor/zero-price", "pricing": {"prompt": "0", "completion": "0"}},
]})
_edu.OPENROUTER_MODELS = []
_picked = _edu._pick_free_models()
check("edu: OpenRouter فقط مدل رایگان",
      all("paid" not in m for m in _picked) and "vendor/free-model:free" in _picked)
check("edu: مدل تصویری OpenRouter حذف می‌شود", all("vision" not in m for m in _picked))
_edu.requests.get = _orig_or_get

# Gemini 503 → اولین مدل رایگان OpenRouter جواب می‌دهد
_edu.OPENROUTER_MODELS = ["vendor/free-model:free"]
_edu._alternatives = lambda model, limit=2: []
_or_calls = []


def _fallback_post(url, headers=None, json=None, params=None, timeout=None):
    if "generativelanguage" in str(url):
        return _Resp(503)
    _or_calls.append((str(url), json))
    return _Resp(200, {"choices": [{"message": {"content": _json.dumps(_good)}}]})


_edu.requests.post = _fallback_post
_result = _edu.generate("x", [])
check("edu: با ۵۰۳ از OpenRouter جواب می‌گیرد",
      isinstance(_result, dict) and _result.get("title") == _good["title"])
check("edu: فقط مدل رایگان OpenRouter صدا زده شد",
      bool(_or_calls)
      and _or_calls[0][1].get("model") == "vendor/free-model:free"
      and "paid" not in str(_or_calls[0][1].get("model")))
check("edu: سقف توکن fallback کم است", _or_calls[0][1].get("max_tokens") == 700)

# هر دو منبع خطا → None (بدون استثنا)
_edu.requests.post = lambda url, **kw: _Resp(503)
check("edu: وقتی هر دو منبع خطا دهند None برمی‌گردد", _edu.generate("x", []) is None)

# ── اولویت‌بندی و «اولین مدل تاییدشده برنده است» ──
_edu.GEMINI_API_KEY = "k"
_edu.OPENROUTER_API_KEY = "or-test"
_edu.OPENROUTER_MODELS = ["a/m1:free", "b/m2:free", "c/m3:free"]
_edu._alternatives = lambda model, limit=2: []
_bad_en = {**_good, "title": "How to write emails", "hook": "english only text here"}
_acc = lambda d: _edu.validate(d, [])


def _router(gemini=None, orouter=None):
    """gemini: لیست کد/خروجی؛ orouter: {مدل: خروجی یا None}"""
    seen = {"g": 0, "or": []}

    def post(url, headers=None, json=None, params=None, timeout=None):
        if "generativelanguage" in str(url):
            seen["g"] += 1
            kind = (gemini or [503])[min(seen["g"] - 1, len(gemini or [503]) - 1)]
            if kind == 200:
                return _Resp(200, _OK_BODY)
            if kind == "bad":
                return _Resp(200, {"candidates": [{"content": {"parts": [{"text": _json.dumps(_bad_en)}]}}]})
            if kind == "junk":
                return _Resp(200, {"candidates": [{"content": {"parts": [{"text": "no json here"}]}}]})
            return _Resp(kind)
        m = json["model"]
        seen["or"].append(m)
        out = (orouter or {}).get(m)
        if out is None:
            return _Resp(429)
        return _Resp(200, {"choices": [{"message": {"content": _json.dumps(out)}}]})
    return post, seen


_edu._sleep = lambda s: None
_p, _s = _router(gemini=[200], orouter={"a/m1:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("اولویت: Gemini جواب قابل‌قبول داد → OpenRouter اصلاً صدا زده نمی‌شود", _s["or"] == [] and _edu._RUN["provider"] == "gemini")
_p, _s = _router(gemini=["bad"], orouter={"a/m1:free": _good})
_edu.requests.post = _p
_r = _edu.generate("x", [], accept=_acc)
check("Gemini جواب بی‌کیفیت (غیرفارسی) داد → مدل بعدی OpenRouter", _r == _good and _s["or"] == ["a/m1:free"] and _s["g"] == 1)
_p, _s = _router(gemini=["junk"], orouter={"a/m1:free": _good})
_edu.requests.post = _p
check("Gemini متن بدون JSON داد → fallback (قبلاً None می‌شد)", _edu.generate("x", [], accept=_acc) == _good and _s["or"] == ["a/m1:free"])
_p, _s = _router(gemini=[503], orouter={"b/m2:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("اولین مدل تاییدشده OpenRouter برنده است؛ m3 صدا زده نمی‌شود", _s["or"] == ["a/m1:free", "b/m2:free"] and _edu._RUN["model"] == "b/m2:free")
_p, _s = _router(gemini=[503], orouter={"a/m1:free": _bad_en, "b/m2:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("مدل رایگانی که خروجی بی‌کیفیت داد رد می‌شود و بعدی امتحان می‌شود", _s["or"] == ["a/m1:free", "b/m2:free"])
_p, _s = _router(gemini=[503], orouter={})
_edu.requests.post = _p
check("همه شکست خوردند → None، فقط حداکثر ۳ مدل رایگان", _edu.generate("x", [], accept=_acc) is None and len(_s["or"]) == 3)
_p, _s = _router(gemini=[200], orouter={"a/m1:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc, use_gemini=False)
check("use_gemini=False: مستقیم OpenRouter و بدون تماس با Gemini", _s["g"] == 0 and _s["or"] == ["a/m1:free"])

# _run: بدون کلید Gemini یا با بودجهٔ تمام‌شده، OpenRouter کار می‌کند
_p, _s = _router(gemini=[200], orouter={"a/m1:free": {**_good, "title": "ترفند تازه برای نوشتن گزارش هفتگی با هوش مصنوعی"}})
_edu.requests.post = _p
_k2 = {}
_edu.cloudflare_kv.get = lambda k, d=None: _k2.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _k2.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_sent2 = []
_edu.send_message = lambda c, t, **kw: (_sent2.append(t) or {"ok": True})
_edu.bale.send_text = lambda t: True
_edu.bale.reset = lambda: None
_edu.storage.load_published = lambda: []
_edu.storage.save_published = lambda items: None
_edu.GEMINI_API_KEY = ""
_edu.maybe_post({}, _dt.datetime(2026, 10, 27, 5, 17))
check("بدون GEMINI_API_KEY: پست از OpenRouter منتشر می‌شود", len(_sent2) == 1 and _s["g"] == 0
      and _k2["edu_state"]["recent"][-1]["via"] == "openrouter/a/m1:free")
_edu.GEMINI_API_KEY = "k"
_edu.gemini_budget.can_call = lambda **kw: False
_k2.clear(); _sent2.clear()
_edu.maybe_post({}, _dt.datetime(2026, 10, 27, 5, 17))
check("بودجهٔ Gemini تمام شده: باز هم از OpenRouter پست می‌شود", len(_sent2) == 1 and _s["g"] == 0)
_edu.OPENROUTER_API_KEY = ""
_k2.clear(); _sent2.clear()
_edu.maybe_post({}, _dt.datetime(2026, 10, 27, 5, 17))
check("نه بودجهٔ Gemini نه کلید OpenRouter: هیچ‌کاری نمی‌کند", _sent2 == [])
_edu.OPENROUTER_API_KEY = "or-test"

# ── کنترل مصرف توکن: بعد از ۲ خروجی ردشده دیگر مدل بعدی صدا زده نمی‌شود ──
_edu.EDU_MAX_REJECTS = 2
_edu.OPENROUTER_MODELS = ["a/m1:free", "b/m2:free", "c/m3:free"]
_p, _s = _router(gemini=["bad"], orouter={"a/m1:free": _bad_en, "b/m2:free": _good})
_edu.requests.post = _p
_r = _edu.generate("x", [], accept=_acc)
check("یک ردشدهٔ Gemini + یک ردشدهٔ OpenRouter → توقف، مدل b صدا زده نمی‌شود", _r is None and _s["or"] == ["a/m1:free"] and _edu._RUN["rejected"] == 2)
_p, _s = _router(gemini=[503], orouter={"a/m1:free": _bad_en, "b/m2:free": _good})
_edu.requests.post = _p
_r = _edu.generate("x", [], accept=_acc)
check("با ۵۰۳ (بدون هزینهٔ توکن) و یک ردشده، مدل بعدی هنوز امتحان می‌شود", _r == _good and _edu._RUN["rejected"] == 1)

# ── ۴۲۹: مدل هم‌خانواده رد می‌شود؛ سقف روزانه فوراً متوقف می‌کند ──
_edu.OPENROUTER_MODELS = ["g/gemma-a:free", "g/gemma-b:free", "q/qwen:free"]
_p, _s = _router(gemini=[503], orouter={"q/qwen:free": _good})
_edu.requests.post = _p
_r = _edu.generate("x", [], accept=_acc)
check("بعد از ۴۲۹ مدل هم‌خانواده (همان vendor) رد می‌شود", _r == _good and _s["or"] == ["g/gemma-a:free", "q/qwen:free"])


def _daily_post(url, headers=None, json=None, params=None, timeout=None):
    if "generativelanguage" in str(url):
        return _Resp(503)
    _s2["or"].append(json["model"])
    r = _Resp(429)
    r.text = '{"error":{"message":"Rate limit exceeded: free-models-per-day"}}'
    return r


_s2 = {"or": []}
_edu.requests.post = _daily_post
_edu.generate("x", [], accept=_acc)
check("سقف روزانهٔ رایگان پر است → فقط یک درخواست و توقف", len(_s2["or"]) == 1)
_edu.OPENROUTER_MODELS = ["a/m1:free", "b/m2:free", "c/m3:free"]

# ── عیب‌یابی: پیچیده‌شدن خروجی و دلیل رد ──
check("خروجی لیست [ {…} ] باز می‌شود", _edu.validate([_good], []) is not None)
check("خروجی {post: {…}} باز می‌شود", _edu.validate({"post": _good}, []) is not None)
_ds = {**_good, "steps": [{"step": "اول کار را توضیح بده"}, {"text": "بعد نتیجه را بخواه"}]}
check("مرحله‌ها به‌صورت دیکشنری هم پذیرفته می‌شود", len(_edu.validate(_ds, [])["steps"]) == 2)
_ss = {**_good, "steps": "اول کار را توضیح بده\nبعد نتیجه را بخواه"}
check("مرحله‌ها به‌صورت یک رشتهٔ چندخطی هم پذیرفته می‌شود", len(_edu.validate(_ss, [])["steps"]) == 2)
check("دلیل رد: غیرفارسی", "not_persian" in _edu.check_post(_bad_en, [])[1])
check("دلیل رد: عنوان مشابه", _edu.check_post(_good, [_good["title"]])[1] == "similar_to_recent_title")
check("دلیل رد: خیلی کم‌محتوا", _edu.check_post({**_good, "steps": ["یک"], "prompt": ""}, [])[1].startswith("too_thin"))
check("دلیل رد: غیر آبجکت", _edu.check_post("متن", [])[1] == "not_an_object")

# پاک‌سازی
_edu.requests.post, _edu.requests.get = _orig_or_post, _orig_or_get
_edu._alternatives = _orig_alternatives
_edu.OPENROUTER_API_KEY = ""
_edu.OPENROUTER_MODELS = []
_edu.EDU_MAX_OUTPUT_TOKENS = 800

# جریان کامل با Gemini و تلگرام جعلی
import json as _json
_kvstore = {}
_edu.cloudflare_kv.get = lambda k, d=None: _kvstore.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _kvstore.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.GEMINI_API_KEY = "k"; _edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_edu.gemini_budget.can_call = lambda **kw: True
_edu.gemini_budget.mark_called = lambda: None
_tg = []
_edu.send_message = lambda chat, text, **kw: (_tg.append(text) or {"ok": True})
_bl = []
_edu.bale.send_text = lambda t: (_bl.append(t) or True)
_edu.bale.reset = lambda: None
_gem = {"out": dict(_good), "calls": 0}
_edu.generate = lambda desc, recent, **kw: (_gem.__setitem__("calls", _gem["calls"] + 1) or _gem["out"])
_edu.storage.load_published = lambda: []
_edu.storage.save_published = lambda items: _kvstore.__setitem__("published", items)
_T = _dt.datetime(2026, 10, 27, 5, 17)
_edu.maybe_post({}, _T)
check("پست در تلگرام و بله منتشر شد", len(_tg) == 1 and len(_bl) == 1 and _kvstore["edu_state"]["last_slot"])
_edu.maybe_post({}, _T + _dt.timedelta(hours=1))
check("اجرای دوم همان اسلات: پست تکراری نه", len(_tg) == 1 and _gem["calls"] == 1)
_gem["out"] = {**_good, "title": "عنوان کاملاً متفاوت دربارهٔ تولید تصویر با هوش مصنوعی"}
_edu.maybe_post({}, _T + _dt.timedelta(hours=2))
check("اسلات بعدی: پست دوم", len(_tg) == 2)
_n = len(_tg)
_edu.maybe_post({"paused": True}, _T + _dt.timedelta(hours=4))
_edu.maybe_post({"edu_enabled": False}, _T + _dt.timedelta(hours=4))
check("pause و /edu off پست نمی‌سازند", len(_tg) == _n)
_gem["out"] = None
_edu.maybe_post({}, _T + _dt.timedelta(hours=4))
_edu.maybe_post({}, _T + _dt.timedelta(hours=5))
_c1 = _gem["calls"]
_edu.maybe_post({}, _T + _dt.timedelta(hours=5, minutes=10))
check("شکست Gemini: حداکثر ۲ تلاش در اسلات و چیزی منتشر نمی‌شود", len(_tg) == _n and _gem["calls"] == _c1)
_gem["out"] = {**_good, "title": "ترفند چهارم برای نوشتن خلاصهٔ جلسه با هوش مصنوعی"}
_edu.maybe_post({}, _T + _dt.timedelta(hours=6))
check("اسلات بعدی بعد از شکست دوباره کار می‌کند", len(_tg) == _n + 1)
check("عنوان‌های اخیر نگه‌داری می‌شوند", len(_kvstore["edu_state"]["recent"]) == 3)
_edu.send_message = lambda chat, text, **kw: {"ok": False}
_gem["out"] = {**_good, "title": "موضوعی کاملاً تازه دربارهٔ برنامه‌ریزی روزانه با چت‌بات"}
_nb = len(_bl)
_edu.maybe_post({}, _T + _dt.timedelta(hours=8))
check("اگر تلگرام نرود: به بله نمی‌رود، در صف می‌ماند و منتشرشده ثبت نمی‌شود", len(_bl) == _nb and len(_kvstore["edu_state"]["queue"]) >= 1)
_edu.generate = lambda desc, recent, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
check("استثنای غیرمنتظره بیرون نمی‌آید", _edu.maybe_post({}, _T + _dt.timedelta(hours=10)) == {})

# ── فاصلهٔ بین تلاش‌ها و اجرای دستی (force) ──
print("Edu retry gap:")
_edu.requests.post, _edu.requests.get = _orig_or_post, _orig_or_get
_kg = {}
_edu.cloudflare_kv.get = lambda k, d=None: _kg.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _kg.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.GEMINI_API_KEY = "k"; _edu.OPENROUTER_API_KEY = ""; _edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_edu.gemini_budget.can_call = lambda **kw: True
_edu.gemini_budget.mark_called = lambda: None
_gn = {"n": 0}
_edu.generate = lambda desc, recent, **kw: (_gn.__setitem__("n", _gn["n"] + 1) or None)
_edu.send_message = lambda c, t, **kw: {"ok": True}
_G = _dt.datetime(2026, 10, 27, 5, 17)
_edu.maybe_post({}, _G)
_edu.maybe_post({}, _G + _dt.timedelta(minutes=6))
check("تلاش دوم بعد از ۶ دقیقه انجام نمی‌شود (فاصلهٔ حداقل)", _gn["n"] == 1 and _kg["edu_state"]["tries"] == 1)
_edu.maybe_post({}, _G + _dt.timedelta(minutes=6), force=True)
check("force فاصله را نادیده می‌گیرد ولی تلاش اسلات را نمی‌سوزاند", _gn["n"] == 2 and _kg["edu_state"]["tries"] == 1)
_edu.maybe_post({}, _G + _dt.timedelta(minutes=61))
check("بعد از فاصله تلاش بعدی انجام می‌شود", _gn["n"] == 3 and _kg["edu_state"]["tries"] == 2)
check("پیش‌فرض تلاش در اسلات ≥ ۶ و فاصله ≥ ۵۰ دقیقه", _cfg.EDU_MAX_TRIES_PER_SLOT >= 6 and _cfg.EDU_RETRY_GAP_MIN >= 50)

# ── ساخت هر ۶ ساعت، انتشار فقط ۱۹ تا ۲۲ (به وقت ایران) ──
print("Edu schedule (generate 6h, publish 19-22):")
_edu.EDU_EVERY_HOURS = 6
_edu.EDU_PUBLISH_HOURS = _DEFAULT_PUB_HOURS
_ks = {}
_edu.cloudflare_kv.get = lambda k, d=None: _ks.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _ks.__setitem__(k, _json.loads(_json.dumps(v))) or True
_sent3, _bl3 = [], []
_edu.send_message = lambda c, tx, **kw: (_sent3.append(tx) or {"ok": True})
_edu.bale.send_text = lambda tx: (_bl3.append(tx) or True)
_edu.storage.load_published = lambda: []
_edu.storage.save_published = lambda items: None
_edu.GEMINI_API_KEY = "k"; _edu.OPENROUTER_API_KEY = ""; _edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_titles = ["ترفند اول برای نوشتن ایمیل با هوش مصنوعی", "اشتباه رایج دوم در پرامپت‌نویسی چت‌بات", "مفهوم سوم یعنی توکن در مدل‌های زبانی", "تولید چهارم تصویر با توضیح دقیق صحنه"]
_gi = {"i": 0}


def _gen_stub(desc, recent, **kw):
    _gi["i"] += 1
    return {**_good, "title": _titles[(_gi["i"] - 1) % 4]}


_edu.generate = _gen_stub
_edu._RUN.update(provider="stub", model="m", gemini_calls=0)


def _tehran(h, m=47, day=28):  # ساعت محلی تهران → UTC naive
    return _dt.datetime(2026, 10, day, h, m) - _dt.timedelta(minutes=210)


# روز ۲۸ اکتبر: اجرای ساعتی از ۰۰:۴۷ تا ۲۳:۴۷
for _h in range(24):
    _edu.maybe_post({}, _tehran(_h))
_pub_hours = []
check("در یک روز دقیقاً ۴ پست ساخته شد (هر ۶ ساعت یکی) و ۴ پست منتشر شد", _gi["i"] == 4 and len(_sent3) == 4)
check("همهٔ پست‌ها برچسب «آموزش» در تیتر دارند", all(x.splitlines()[0].startswith("🎓 <b>آموزش |") for x in _sent3))
check("نسخهٔ بله هم همان ۴ پست را با برچسب دارد", len(_bl3) == 4 and all("آموزش |" in x.splitlines()[0] for x in _bl3))
check("صف پس از انتشار خالی است", _ks["edu_state"]["queue"] == [])

# زمان دقیق انتشار: بازسازی با روز تازه
_ks.clear(); _sent3.clear(); _bl3.clear(); _gi["i"] = 0
_when = []
_orig_send = _edu.send_message
_edu.send_message = lambda c, tx, **kw: (_when.append(_cur["h"]) or _sent3.append(tx) or {"ok": True})
_cur = {"h": 0}
for _h in range(24):
    _cur["h"] = _h
    _edu.maybe_post({}, _tehran(_h, day=29))
check("انتشار فقط در ساعت‌های ۱۹، ۲۰، ۲۱ و ۲۲ است", _when == [19, 20, 21, 22])
_edu.send_message = _orig_send

# اجرای دوم در همان ساعت (cron دوبل / دستی) پست تکراری نمی‌سازد
_ks.clear(); _sent3.clear(); _gi["i"] = 0
_edu.send_message = lambda c, tx, **kw: (_sent3.append(tx) or {"ok": True})
for _h in (0, 6, 12, 18):
    _edu.maybe_post({}, _tehran(_h, day=30))
check("قبل از ساعت ۱۹: ۴ پست در صف و هیچ‌کدام منتشر نشده", len(_ks["edu_state"]["queue"]) == 4 and _sent3 == [])
_edu.maybe_post({}, _tehran(19, 47, day=30))
_edu.maybe_post({}, _tehran(19, 59, day=30))
_edu.maybe_post({}, _tehran(19, 10, day=30), force=True)
check("در یک ساعت فقط یک پست (حتی با اجرای دوباره و force)", len(_sent3) == 1 and len(_ks["edu_state"]["queue"]) == 3)
check("اولین پست صف اول می‌رود (FIFO)", _titles[0] in _sent3[0])

# force خارج از ساعت انتشار منتشر نمی‌کند
_ks.clear(); _sent3.clear(); _gi["i"] = 0
_edu.maybe_post({}, _tehran(10, day=30), force=True)
check("force ساخت می‌کند ولی خارج از ساعت پربازدید منتشر نمی‌کند", _sent3 == [] and len(_ks["edu_state"]["queue"]) == 1)

# صف پر → ساخت متوقف
_edu.EDU_QUEUE_MAX = 1
_n0 = _gi["i"]
_edu.maybe_post({}, _tehran(12, day=30))
check("صف پر باشد ساخت جدید انجام نمی‌شود (صرفه‌جویی در مصرف مدل)", _gi["i"] == _n0)
_edu.EDU_QUEUE_MAX = 8

# pause / خاموش: نه ساخت نه انتشار
_n0 = _gi["i"]; _s0 = len(_sent3)
_edu.maybe_post({"paused": True}, _tehran(20, day=30))
_edu.maybe_post({"edu_enabled": False}, _tehran(20, day=30))
check("pause و /edu off هم ساخت و هم انتشار را متوقف می‌کنند", _gi["i"] == _n0 and len(_sent3) == _s0)

# شکست تلگرام: در صف می‌ماند و ساعت بعد منتشر می‌شود
_edu.send_message = lambda c, tx, **kw: {"ok": False}
_edu.maybe_post({}, _tehran(20, day=30))
_q = len(_ks["edu_state"]["queue"])
_edu.send_message = lambda c, tx, **kw: (_sent3.append(tx) or {"ok": True})
_edu.maybe_post({}, _tehran(21, day=30))
check("شکست ارسال: پست در صف می‌ماند و ساعت بعد (با جبران عقب‌افتادگی) می‌رود",
      len(_ks["edu_state"]["queue"]) == _q - 2 and len(_sent3) == _s0 + 2)

# Gemini: فاصلهٔ ۵۵ دقیقه‌ای بودجه برای پست آموزشی اعمال نمی‌شود
import src.gemini_budget as _gb
_seen_kw = {}
_orig_cc = _edu.gemini_budget.can_call
_edu.gemini_budget.can_call = lambda **kw: (_seen_kw.update(kw) or True)
_ks.clear(); _gi["i"] = 0
_edu.maybe_post({}, _tehran(0, day=31))
check("پست آموزشی can_call را با min_interval_min=0 صدا می‌زند", _seen_kw.get("min_interval_min") == 0)
_edu.gemini_budget.can_call = _orig_cc

# ── آمار مصرف و سقف روزانهٔ تلاش ──
print("Edu usage stats:")
_ku = {}
_edu.cloudflare_kv.get = lambda k, d=None: _ku.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _ku.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.EDU_PUBLISH_HOURS = (23,)
_edu.OPENROUTER_API_KEY = ""
_edu._RUN.update(gemini_calls=0, or_calls=0, rejected=0)


def _fail_gen(desc, recent, **kw):
    _edu._RUN.update(gemini_calls=3, or_calls=1, rejected=1, provider="", model="")
    return None


_edu.generate = _fail_gen
_edu.EDU_EVERY_HOURS = 6
_edu.EDU_RETRY_GAP_MIN = 0
_edu.EDU_MAX_TRIES_PER_SLOT = 50
_edu.EDU_MAX_ATTEMPTS_PER_DAY = 3
_U = _dt.datetime(2026, 11, 2, 1, 0)
for _i in range(5):
    _edu.maybe_post({}, _U + _dt.timedelta(minutes=_i))
_day = _edu.local_now(_U).date().isoformat()
_ud = _ku["edu_state"]["usage"][_day]
check("سقف روزانهٔ تلاش: بیش از ۳ بار ساخت نمی‌کند", _ud["attempts"] == 3 and _ud["fail"] == 3 and _ud["ok"] == 0)
check("آمار: درخواست‌ها و خروجی‌های ردشده شمرده می‌شود", _ud["calls"] == 12 and _ud["rejected"] == 3)
_edu.maybe_post({}, _U + _dt.timedelta(minutes=10), force=True)
check("force سقف روزانه را نادیده می‌گیرد ولی در آمار شمرده می‌شود", _ku["edu_state"]["usage"][_day]["attempts"] == 4)
_edu.EDU_MAX_ATTEMPTS_PER_DAY = 10
_ku.clear()
_edu.generate = lambda desc, recent, **kw: (_edu._RUN.update(gemini_calls=1, or_calls=0, rejected=0, provider="gemini", model="g-1") or _good)
_edu.maybe_post({}, _U)
check("موفقیت در آمار با نام مدل ثبت می‌شود", _ku["edu_state"]["usage"][_day]["via"] == {"gemini/g-1": 1} and _ku["edu_state"]["usage"][_day]["ok"] == 1)
_edu.EDU_MAX_TRIES_PER_SLOT = 6
_edu.EDU_RETRY_GAP_MIN = 56
_edu.EDU_PUBLISH_HOURS = _DEFAULT_PUB_HOURS


# ───────────── تضمین ۴ پست آموزشی در شب (تور نجات + جبران تأخیر) ─────────────
print("Edu nightly guarantee:")
_kb = {}
_edu.cloudflare_kv.get = lambda k, d=None: _kb.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _kb.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.EDU_PUBLISH_HOURS = _DEFAULT_PUB_HOURS
_edu.EDU_EVERY_HOURS = 6
_edu.EDU_POSTS_PER_DAY = 4
_edu.EDU_CATCHUP_MAX = 2
_edu.EDU_USE_BUILTIN_FALLBACK = True
_edu.EDU_MAX_TRIES_PER_SLOT = 6
_edu.EDU_RETRY_GAP_MIN = 0
_edu.GEMINI_API_KEY = "k"; _edu.OPENROUTER_API_KEY = ""; _edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_edu.gemini_budget.can_call = lambda **kw: True
_sent_b = []
_edu.send_message = lambda c, t, **kw: (_sent_b.append(t) or {"ok": True})
_edu.bale.send_text = lambda t: True
_edu.bale.reset = lambda: None
_edu.storage.load_published = lambda: []
_edu.storage.save_published = lambda items: None
_edu.generate = lambda desc, recent, **kw: None  # هر دو مدل شکست می‌خورند

for _h in range(19, 23):
    _edu.maybe_post({}, _tehran(_h, day=2))
check("بدون هیچ خروجی مدل: ۴ پست آموزشی محلی منتشر شد", len(_sent_b) == 4)
check("پست‌های محلی هم تیتر «آموزش» و هشتگ دارند",
      all(x.startswith("🎓 <b>آموزش |") and "#" in x for x in _sent_b))
_edu_state2 = _kb["edu_state"]
check("صف خالی و شمارندهٔ روز ۴ شد",
      _edu_state2["queue"] == [] and _edu_state2["pub_count"]["2026-10-02"] == 4)
check("عنوان‌های محلی تکراری نیستند", len({x.splitlines()[0] for x in _sent_b}) == 4)

# اگر فقط اجرای آخر شب برسد، باید جبران کند (نه صفر پست)
_kb.clear(); _sent_b.clear()
_edu.maybe_post({}, _tehran(22, day=3))
check("اجرای تک در ساعت ۲۲: ۲ پست جبرانی می‌رود", len(_sent_b) == 2)
_edu.maybe_post({}, _tehran(22, day=3))
_n_catch = len(_sent_b)
_edu.maybe_post({}, _tehran(22, day=3))
check("سقف روزانه: بعد از رسیدن به ۴ پست، بیشتر نمی‌رود",
      _n_catch == 4 and len(_sent_b) == 4)

# اگر مدل کار کند، پست مدل بر «تور نجات» اولویت دارد
_kb.clear(); _sent_b.clear()
_edu.generate = lambda desc, recent, **kw: {**_good, "title": "پست ساخته‌شده با مدل برای امشب"}
_edu.maybe_post({}, _tehran(19, day=4))
check("وقتی مدل کار می‌کند، پست مدل منتشر می‌شود نه پست محلی",
      len(_sent_b) == 1 and "پست ساخته‌شده با مدل" in _sent_b[0])


# ───────────── دایجست هفتگی ─────────────
print("Weekly digest:")
_dg = {}
digest.cloudflare_kv.get = lambda k, d=None: _dg.get(k, d)
digest.cloudflare_kv.put = lambda k, v: _dg.__setitem__(k, _json.loads(_json.dumps(v))) or True
_dg_sent = []
digest.send_message = lambda c, t, **kw: (_dg_sent.append(t) or {"ok": True, "result": {"message_id": 77}})
digest.bale.reset = lambda: None
digest.bale.send_text = lambda t: True
digest.pin_message = lambda mid: None
digest.CHANNEL_ID = "@c"
_now_dg = datetime.datetime.utcnow()


def _news(i, imp="medium", days=1, cat="industry"):
    return {"id": f"n{i}", "title_fa": f"خبر شمارهٔ {i}", "source": "منبع",
            "link": "http://example.com/x", "published": True, "category": cat,
            "importance": imp, "score": 90 - i,
            "published_at": (_now_dg - datetime.timedelta(days=days)).isoformat() + "Z"}


_log7 = [_news(i, "high" if i <= 3 else "medium") for i in range(1, 8)]
digest.storage.load_published = lambda: list(_log7)
digest.storage.save_published = lambda items: None
_fri = _dt.datetime(2026, 11, 13, 20, 0) - _dt.timedelta(minutes=210)  # جمعه ۲۰:۰۰ تهران
digest.maybe_post({}, _fri)
check("دایجست: جمعه شب یک پست جمع‌بندی می‌فرستد", len(_dg_sent) == 1)
check("دایجست: هفت خبر و هشتگ دارد",
      _dg_sent and all(f"خبر شمارهٔ {i}" in _dg_sent[0] for i in range(1, 8))
      and "جمع‌بندی هفته" in _dg_sent[0] and "#جمع‌بندی_هفته" in _dg_sent[0])
check("دایجست: هفته در KV ثبت می‌شود", "last_week" in _dg.get("digest_state", {}))
digest.maybe_post({}, _fri + _dt.timedelta(minutes=30))
check("دایجست: در همان هفته دوبار پست نمی‌شود", len(_dg_sent) == 1)
digest.maybe_post({}, _fri + _dt.timedelta(days=1))
check("دایجست: شنبه پست نمی‌شود", len(_dg_sent) == 1)

# کمتر از ۳ خبر → بدون جمع‌بندی
_dg.clear(); _dg_sent.clear()
digest.storage.load_published = lambda: [_news(1), _news(2)]
digest.maybe_post({}, _fri)
check("دایجست: با کمتر از ۳ خبر پست نمی‌شود", _dg_sent == [])

# پست آموزشی و خود دایجست وارد جمع‌بندی نمی‌شوند
_mix = [_news(i) for i in range(1, 5)] + [_news(9, cat="edu"), _news(10, cat="digest")]
check("دایجست: پست آموزشی و دایجست قبلی حساب نمی‌شوند",
      len(digest.recent_items(_mix, _now_dg)) == 4)

# خبر قدیمی‌تر از ۷ روز حساب نمی‌شود
_old = [_news(i) for i in range(1, 4)] + [_news(8, days=10)]
check("دایجست: خبر قدیمی‌تر از ۷ روز حذف می‌شود", len(digest.recent_items(_old, _now_dg)) == 3)

# پاسخ کوییزهای هفته
_quiz_log = [{"published": True, "category": "edu", "source": "آموزش AI",
              "published_at": _now_dg.isoformat() + "Z",
              "quiz": {"question": "سؤال کوییز هفته؟", "options": ["الف", "ب", "ج", "د"],
                       "correct": 2, "explanation": "چون ج"}}]
_qz_week = digest._recent_quizzes(_quiz_log, _now_dg)
check("دایجست: کوییزهای هفته خوانده می‌شوند", len(_qz_week) == 1)
_dg_txt = digest.format_digest([_news(i) for i in range(1, 5)], quizzes=_qz_week)
check("دایجست: پاسخ درست کوییزها اعلام می‌شود",
      "کوییزهای هفته" in _dg_txt and "✅ ج" in _dg_txt)


# ───────────── ادغام خبرهای تکراری ─────────────
print("Story clustering:")
_same_a = {"title": "OpenAI launches GPT-5 with a new reasoning mode", "source": "OpenAI Blog", "source_trust": 5}
_same_b = {"title": "OpenAI launches GPT-5 and brings a new reasoning mode", "source": "TechCrunch", "source_trust": 3,
           "link": "http://tc"}
_other = {"title": "Nvidia reports record data-center revenue", "source": "The Verge", "source_trust": 3}
_cl = cluster.merge_similar([dict(_same_a), dict(_same_b), dict(_other)])
check("خبر تکراری از دو منبع → یک آیتم", len(_cl) == 2)
check("منبع اصلی با بالاترین اعتبار انتخاب می‌شود",
      _cl[0]["source"] == "OpenAI Blog" and _cl[0]["also_sources"][0]["source"] == "TechCrunch")
check("خبر نامرتبط جدا می‌ماند", _cl[1]["source"] == "The Verge" and "also_sources" not in _cl[1])
_ver = cluster.merge_similar([
    {"title": "OpenAI releases GPT-5 today", "source": "A", "source_trust": 4},
    {"title": "OpenAI releases GPT-6 today", "source": "B", "source_trust": 4},
])
check("نسخهٔ متفاوت (GPT-5 و GPT-6) با هم ادغام نمی‌شوند", len(_ver) == 2)
check("شباهت عنوان‌های یکسان ۱ است", cluster.similarity("same title here", "same title here") == 1.0)

_msg_also = formatting.format_message({"title_fa": "عنوان", "summary_fa": "خلاصه", "source": "OpenAI",
                                       "link": "http://x", "hashtags": ["AI"],
                                       "also_sources": [{"source": "TechCrunch", "link": "http://tc"}]})
check("قالب پست: خط «همچنین در» اضافه می‌شود", "همچنین در: TechCrunch" in _msg_also)


# ───────────── هشتگ‌های هدفمند ─────────────
print("Targeted hashtags:")
_h = hashtags.build({"title": "OpenAI releases GPT-6 with new safety guardrails",
                     "summary": "", "category": "model_release"})
check("هشتگ موجودیت اول می‌آید", _h and _h[0] == "OpenAI")
check("هشتگ دسته و موضوع اضافه می‌شود",
      "مدل_جدید" in _h and "ایمنی_AI" in _h and "هوش_مصنوعی" in _h)
check("سقف ۴ هشتگ و بدون تکرار", len(_h) <= 4 and len(set(_h)) == len(_h))
check("هشتگ‌ها فقط حروف/عدد/زیرخط دارند",
      all(all(c.isalnum() or c == "_" or "\u0600" <= c <= "\u06FF" for c in t) for t in _h))
check("هشتگ موجودیت برای Qwen", hashtags.build({"title": "Qwen 3 is out", "category": "model_release"})[0] == "Qwen")
check("هشتگ برای خبر بی‌موضوع هم ساخته می‌شود",
      "هوش_مصنوعی" in hashtags.build({"title": "Market update", "category": "industry"}))


# ───────────── موضوعات مورد علاقه (اولویت، نه فیلتر) ─────────────
print("Topic preferences:")
_items_t = [
    {"title": "OpenAI ships a new safety filter", "summary": "", "source": "OpenAI Blog",
     "category": "model_release", "hashtags": ["ایمنی_AI"], "hot": 50, "hot_reasons": []},
    {"title": "Nvidia earnings beat estimates", "summary": "", "source": "CNBC",
     "category": "industry", "hashtags": [], "hot": 60, "hot_reasons": []},
]
_hits_t = topic_prefs.boost(_items_t, ["ایمنی"])
check("موضوع: خبر هم‌خوان نشانه و جذابیت می‌گیرد",
      _hits_t == 1 and _items_t[0].get("topic_hit") == "ایمنی" and _items_t[0]["hot"] == 62)
check("موضوع: خبرهای دیگر حذف نمی‌شوند",
      len(_items_t) == 2 and "topic_hit" not in _items_t[1])
check("موضوع: لیست تمیز، بدون تکرار و با سقف طول",
      topic_prefs.clean_list(["ایمنی", " ایمنی ", "", "x" * 60]) == ["ایمنی", "x" * 40])
check("موضوع: بدون موضوع هیچ تغییری نمی‌دهد", topic_prefs.boost(list(_items_t), []) == 0)

_base_item = {"source": "دیجیاتو", "importance": "medium", "confidence": "likely", "category": "other"}
_base_score = mainmod.calculate_score(dict(_base_item))
_boost_score = mainmod.calculate_score({**_base_item, "topic_hit": "ایمنی"})
check("امتیاز خبرِ موضوعدار ۱۰ واحد بیشتر است", _boost_score == _base_score + 10)


# ───────────── کوییز آموزشی ─────────────
print("Edu quiz:")
check("بانک کوییز حداقل ۴ سؤال با پاسخ درست معتبر دارد",
      len(quiz.QUIZ_BANK) >= 4 and all(0 <= q["correct"] < len(q["options"]) for q in quiz.QUIZ_BANK))
_q1, _id1 = quiz.pick(0, [])
_q2, _id2 = quiz.pick(0, [_id1])
check("کوییز چرخشی است و سؤال تکراری نمی‌دهد", _id1 != _id2)
check("همهٔ کوییزها id یکتا دارند", len({q["id"] for q in quiz.QUIZ_BANK}) == len(quiz.QUIZ_BANK))

_kqz = {}
_edu.cloudflare_kv.get = lambda k, d=None: _kqz.get(k, d)
_edu.cloudflare_kv.put = lambda k, v: _kqz.__setitem__(k, _json.loads(_json.dumps(v))) or True
_edu.EDU_PUBLISH_HOURS = _DEFAULT_PUB_HOURS
_edu.EDU_EVERY_HOURS = 6
_edu.EDU_POSTS_PER_DAY = 4
_edu.EDU_QUIZ_PER_DAY = 2
_edu.EDU_CATCHUP_MAX = 2
_edu.EDU_USE_BUILTIN_FALLBACK = False
_edu.GEMINI_API_KEY = "k"; _edu.OPENROUTER_API_KEY = ""; _edu.CHANNEL_ID = "@c"; _edu.EDU_ENABLED = True
_edu.gemini_budget.can_call = lambda **kw: True
_edu.generate = lambda desc, recent, **kw: None
_sent_q, _polls_q = [], []
_edu.send_message = lambda c, t, **kw: (_sent_q.append(t) or {"ok": True})
_edu.send_poll = lambda c, q, o, cidx, exc=None: (_polls_q.append((q, cidx)) or {"ok": True})
_bale_q = []
_edu.bale.send_text = lambda t: (_bale_q.append(t) or True)
_edu.bale.reset = lambda: None
_edu.storage.load_published = lambda: []
_edu.storage.save_published = lambda items: None
_queue4 = [{"post": dict(_good), "tag": "t", "slot": f"s{i}"} for i in range(4)]
_kqz["edu_state"] = {"queue": list(_queue4)}
for _h in range(19, 23):
    _edu.maybe_post({}, _tehran(_h, day=8))
check("شب با ۴ پست: ۴ پست متنی + ۲ کوییز (ساعت ۱۹ و ۲۲)",
      len(_sent_q) == 4 and len(_polls_q) == 2)
_quiz_bale = [t for t in _bale_q if "✅ پاسخ:" in t]
check("کوییزها با سؤال/گزینه/پاسخ به بله هم می‌روند", len(_quiz_bale) == 2)
check("کوییزِ منتشرشده پاسخ درست معتبر دارد", _polls_q and 0 <= _polls_q[0][1] < 4)
_edu.maybe_post({}, _tehran(22, day=8))
check("کوییز سوم در همان روز نمی‌آید", len(_polls_q) == 2)

# کوییز «مرتبط و جدید»: اول از خبرهای تازه ساخته می‌شود
_qmodel = {"question": "کدام مدل در خبر امروز معرفی شد؟",
           "options": ["مدل الف", "مدل ب", "مدل ج", "مدل د"],
           "correct": 2, "explanation": "در خبر به مدل ج اشاره شده بود."}
check("کوییز معتبر پذیرفته می‌شود", quiz.validate(_qmodel) is not None)
check("کوییز با ۳ گزینه رد می‌شود", quiz.validate({**_qmodel, "options": ["الف", "ب", "ج"]}) is None)
check("کوییز با ایندکس نامعتبر رد می‌شود", quiz.validate({**_qmodel, "correct": 7}) is None)
check("کوییز غیرفارسی رد می‌شود",
      quiz.validate({"question": "Which model was announced?", "options": ["a", "b", "c", "d"],
                     "correct": 0}) is None)

_orig_gen_quiz = _edu.generate_quiz
_edu.generate_quiz = lambda news, accept=None, use_gemini=True: dict(_qmodel)
_kqz.clear(); _sent_q.clear(); _polls_q.clear()
_edu.storage.load_published = lambda: [{"title_fa": "خبر تازهٔ امروز دربارهٔ مدل ج",
                                        "published": True, "category": "model_release",
                                        "published_at": _dt.datetime.utcnow().isoformat() + "Z"}]
_kqz["edu_state"] = {"queue": [dict(_queue4[0])]}
_edu.maybe_post({}, _tehran(19, day=9))
check("کوییز از خبرهای تازهٔ همان روز ساخته می‌شود",
      len(_polls_q) == 1 and _polls_q[0][0] == _qmodel["question"])

_edu.generate_quiz = lambda news, accept=None, use_gemini=True: None
_kqz.clear(); _sent_q.clear(); _polls_q.clear()
_kqz["edu_state"] = {"queue": [dict(_queue4[0])]}
_edu.maybe_post({}, _tehran(19, day=10))
check("اگر مدل کوییز نداد، از بانک آماده استفاده می‌شود",
      len(_polls_q) == 1 and _polls_q[0][0] != _qmodel["question"])
_edu.generate_quiz = _orig_gen_quiz


# ───────────── انتشار زمان‌بندی‌شده ─────────────
print("Scheduled publishing:")
# تست‌های edu/kv ذخیره‌سازی را به store محلی خودشان وصل کرده‌اند؛ برگردان.
kv.get = lambda key, default=None: _store.get(key, default)
kv.put = lambda key, value: _store.__setitem__(key, value)
_store["pending"] = [{"id": "s1", "source": "S", "title_fa": "خبر زمان‌بندی", "summary_fa": "x",
                      "importance": "medium", "hashtags": ["AI"], "link": "http://x"}]
_store["approved"] = []
bot._handle_callback({"id": "c", "data": "sched:2:s1", "from": {"id": 42}}, {})
_appr = _store["approved"]
check("sched: خبر از صف تایید به «آماده انتشار» با زمان آینده می‌رود",
      _store["pending"] == [] and len(_appr) == 1
      and _appr[0]["admin_action"] == "scheduled"
      and _appr[0]["schedule_at"] > datetime.datetime.utcnow().isoformat())

_psent = []
publisher.send_message = lambda c, t, **kw: (_psent.append(t) or {"ok": True})
publisher.bale.reset = lambda: None
publisher.bale.mirror = lambda it: None
publisher.storage.load_published = lambda: []
publisher.storage.save_published = lambda items: None
publisher.storage.save_approved = lambda items: _store.__setitem__("approved", items)
_now_iso2 = datetime.datetime.utcnow()
_future = {"id": "f1", "title_fa": "خبر آینده", "summary_fa": "x", "importance": "medium",
           "hashtags": ["AI"], "link": "http://x",
           "schedule_at": (_now_iso2 + datetime.timedelta(hours=3)).isoformat() + "Z"}
_past = {"id": "p1", "title_fa": "خبر گذشته", "summary_fa": "x", "importance": "medium",
         "hashtags": ["AI"], "link": "http://x",
         "schedule_at": (_now_iso2 - datetime.timedelta(hours=1)).isoformat() + "Z"}
_pstate = {"daily_post_count": 0, "daily_post_date": datetime.datetime.utcnow().date().isoformat()}
publisher.publish_approved(_pstate, [_future, _past])
check("publisher: خبر آینده در صف می‌ماند و خبر گذشته منتشر می‌شود",
      len(_psent) == 1 and "خبر گذشته" in _psent[0]
      and [i["id"] for i in _store["approved"]] == ["f1"])


# ───────────── سیگنال Hacker News ─────────────
print("Hacker News signal:")


class _HnResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


_orig_hn_get = hn.requests.get
hn.requests.get = lambda url, params=None, timeout=None: _HnResp(200, {"hits": [
    {"title": "OpenAI launches GPT-5 with a new reasoning mode", "points": 420,
     "num_comments": 180, "objectID": "111"},
    {"title": "Some completely unrelated story", "points": 900, "num_comments": 50, "objectID": "222"},
]})
_hn_item = {"title": "OpenAI launches GPT-5 with a new reasoning mode", "hot": 55,
            "hot_reasons": ["model"]}
_hn_other = {"title": "Nvidia reports record data-center revenue", "hot": 50, "hot_reasons": []}
_hn_fa = {"title": "خبر فارسی دربارهٔ هوش مصنوعی", "hot": 40}
_hn_out = hn.enrich([_hn_item, _hn_other, _hn_fa])
check("HN: خبر داغ با امتیاز/نظر و لینک شناسایی می‌شود",
      _hn_out[0]["hn"]["points"] == 420 and _hn_out[0]["hn"]["url"].endswith("111"))
check("HN: امتیاز جذابیت اضافه می‌شود و سقف ۱۰۰ رعایت می‌شود",
      _hn_out[0]["hot"] == 65 and "HN 420" in _hn_out[0]["hot_reasons"])
check("HN: خبر نامرتبط به HN وصل نمی‌شود", "hn" not in _hn_other)
check("HN: عنوان فارسی جست‌وجو نمی‌شود", "hn" not in _hn_fa)

hn.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down"))
_hn_fresh = {"title": "OpenAI launches GPT-5 with a new reasoning mode", "hot": 55}
check("HN: خطای شبکه نادیده گرفته می‌شود", hn.enrich([_hn_fresh])[0].get("hn") is None)
hn.requests.get = _orig_hn_get

_msg_hn = formatting.format_message({"title_fa": "عنوان", "summary_fa": "خلاصه", "source": "S",
                                     "link": "http://x", "hashtags": ["AI"],
                                     "hn": {"points": 420, "comments": 180}})
check("قالب پست: خط Hacker News اضافه می‌شود", "Hacker News: 420" in _msg_hn and "180 نظر" in _msg_hn)


# ───────────── مقالهٔ روز arXiv ─────────────
print("arXiv paper of the day:")
_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2611.00002v1</id>
    <title>A New Method for
      Efficient Inference</title>
    <summary>We propose a method that reduces compute.</summary>
    <published>2026-11-13T00:00:00Z</published></entry>
  <entry><id>http://arxiv.org/abs/2611.00001v1</id><title>Old Paper</title>
    <summary>Old abstract.</summary><published>2026-11-12T00:00:00Z</published></entry>
</feed>"""


class _ArxivResp:
    status_code = 200
    text = _ARXIV_XML

    def raise_for_status(self):
        return None


arxiv_daily.requests.get = lambda *a, **k: _ArxivResp()
_entries = arxiv_daily.fetch_entries()
check("arXiv: فید Atom درست خوانده می‌شود", len(_entries) == 2 and _entries[0]["id"] == "2611.00002v1")
check("arXiv: عنوان چندخطی یک‌خطی می‌شود", "Efficient Inference" in _entries[0]["title"])
check("arXiv: مقالهٔ تکراری انتخاب نمی‌شود",
      arxiv_daily.pick(_entries, [_entries[0]["id"]])["id"] == "2611.00001v1")

_ax = {}
arxiv_daily.cloudflare_kv.get = lambda k, d=None: _ax.get(k, d)
arxiv_daily.cloudflare_kv.put = lambda k, v: _ax.__setitem__(k, _json.loads(_json.dumps(v))) or True
_ax_sent = []
arxiv_daily.send_message = lambda c, t, **kw: (_ax_sent.append(t) or {"ok": True, "result": {"message_id": 5}})
arxiv_daily.bale.reset = lambda: None
arxiv_daily.bale.send_text = lambda t: True
arxiv_daily.storage.load_published = lambda: []
arxiv_daily.storage.save_published = lambda items: None
arxiv_daily.CHANNEL_ID = "@c"
arxiv_daily.translator.translate = lambda t, s="auto": f"ترجمه: {t[:30]}"
_t13 = _dt.datetime(2026, 11, 14, 13, 0) - _dt.timedelta(minutes=210)
arxiv_daily.maybe_post({}, _t13)
check("arXiv: در ساعت مقرر یک مقاله پست می‌شود", len(_ax_sent) == 1)
check("arXiv: پست عنوان، لینک و هشتگ دارد",
      "مقالهٔ روز" in _ax_sent[0] and "arxiv.org/abs/2611.00002v1" in _ax_sent[0]
      and "#مقاله_روز" in _ax_sent[0])
arxiv_daily.maybe_post({}, _t13 + _dt.timedelta(minutes=30))
check("arXiv: در همان روز دوبار پست نمی‌شود", len(_ax_sent) == 1)

_ax.clear(); _ax_sent.clear()
arxiv_daily.maybe_post({}, _t13 - _dt.timedelta(hours=3))
check("arXiv: قبل از ساعت مقرر پست نمی‌شود", _ax_sent == [])

_orig_tr = arxiv_daily.translator.translate
arxiv_daily.translator.translate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
arxiv_daily.maybe_post({}, _t13)
check("arXiv: شکست ترجمه → متن انگلیسی منتشر می‌شود",
      len(_ax_sent) == 1 and "A New Method" in _ax_sent[0])
arxiv_daily.translator.translate = _orig_tr


# ───────────── آینهٔ Bluesky ─────────────
print("Bluesky mirror:")


class _BskyResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


_orig_bsky_post = bluesky.requests.post
_bsky_calls = []


def _bsky_post(url, headers=None, json=None, timeout=None):
    method = "createSession" if str(url).endswith("createSession") else "createRecord"
    _bsky_calls.append((method, json, headers))
    if method == "createSession":
        return _BskyResp(200, {"accessJwt": "jwt", "did": "did:plc:x"})
    return _BskyResp(200, {"uri": "at://x"})


bluesky.requests.post = _bsky_post
os.environ.pop("BLUESKY_HANDLE", None)
os.environ.pop("BLUESKY_APP_PASSWORD", None)
check("Bluesky: بدون کلید خاموش است", bluesky.enabled() is False and bluesky.mirror({"title": "x"}) is False
      and _bsky_calls == [])

os.environ["BLUESKY_HANDLE"] = "me.bsky.social"
os.environ["BLUESKY_APP_PASSWORD"] = "app-pass"
_item_bsky = {"title_fa": "عنوان خبر", "summary_fa": "خلاصهٔ خبر", "link": "http://x"}
check("Bluesky: متن پست از عنوان/خلاصه/لینک ساخته می‌شود",
      "عنوان خبر" in bluesky.format_post(_item_bsky) and "http://x" in bluesky.format_post(_item_bsky))
check("Bluesky: متن بلند کوتاه می‌شود", len(bluesky.format_post({**_item_bsky, "summary_fa": "ی" * 900})) <= 280)
_bsky_long = bluesky.format_post({**_item_bsky, "summary_fa": "ی" * 900})
check("Bluesky: لینک حتی با خلاصهٔ بلند کامل می‌ماند",
      _bsky_long.endswith("http://x") and len(_bsky_long) <= 280)
_bsky_long_title = bluesky.format_post({"title_fa": "ع" * 400, "summary_fa": "خلاصه", "link": "http://example.com/a"})
check("Bluesky: لینک با عنوان بلند هم کامل می‌ماند",
      _bsky_long_title.endswith("http://example.com/a") and len(_bsky_long_title) <= 280)
check("Bluesky: بدون لینک هم از سقف رد نمی‌شود",
      len(bluesky.format_post({"title_fa": "ع" * 500, "summary_fa": "ی" * 500})) <= 280)
check("Bluesky: پست موفق True برمی‌گرداند", bluesky.mirror(_item_bsky) is True)
_bsky_names = [c[0] for c in _bsky_calls]
check(f"Bluesky: ابتدا login و بعد createRecord صدا زده می‌شود ({_bsky_names})",
      _bsky_names[-2:] == ["createSession", "createRecord"]
      and _bsky_calls[-1][2].get("Authorization") == "Bearer jwt")

_bsky_calls.clear()
bluesky.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
check("Bluesky: خطای شبکه بی‌صدا نادیده گرفته می‌شود", bluesky.mirror(_item_bsky) is False)
bluesky.requests.post = _orig_bsky_post
os.environ.pop("BLUESKY_HANDLE", None)
os.environ.pop("BLUESKY_APP_PASSWORD", None)


# ───────────── امنیت: ویرایش خبر فقط توسط ادمین ─────────────
print("Admin-only editing:")
# تست‌های قبلی cloudflare_kv.put را به store محلی خودشان وصل کرده‌اند؛ اینجا برگردان.
kv.get = lambda key, default=None: _store.get(key, default)
kv.put = lambda key, value: _store.__setitem__(key, value)
_store["pending"] = [{"id": "e1", "summary_fa": "متن اصلی", "_editing": True}]
bot.ADMIN_ID = 42
bot.send_message = lambda chat_id, text, **kw: {"ok": True}

bot._handle_message({"text": "متن جعلی توسط غریبه", "chat": {"id": 999}}, {})
check("غیرادمین نمی‌تواند متن خبر در صف را عوض کند",
      _store["pending"][0]["summary_fa"] == "متن اصلی" and _store["pending"][0]["_editing"] is True)

bot._handle_message({"text": "متن ویرایش‌شده توسط ادمین", "chat": {"id": 42}}, {})
check("ادمین همچنان می‌تواند ویرایش کند",
      _store["pending"][0]["summary_fa"] == "متن ویرایش‌شده توسط ادمین"
      and _store["pending"][0]["_editing"] is False)

_store["pending"] = []
bot._handle_message({"text": "/start", "chat": {"id": 999}}, {})
check("غیرادمین به دستورها هم دسترسی ندارد", len(_store["pending"]) == 0)


# ───────────── undo / ویرایش پست منتشرشده ─────────────
print("Undo & edit published:")
# تست‌های edu این توابع را جای‌گذاری کرده بودند؛ نسخهٔ واقعی را برگردان.
storage.load_published = lambda: _store.get("published", [])
storage.save_published = lambda items: (_store.__setitem__("published", items) or True)
bot.CHANNEL_ID = "@chan"
_admin_msgs = []
bot.send_message = lambda chat_id, text, **kw: (_admin_msgs.append((text, kw.get("reply_markup"))) or {"ok": True})
_deleted, _edited = [], []


def _fake_delete(message_id, chat_id=None):
    _deleted.append(message_id)
    return {"ok": True}


def _fake_edit(message_id, text, chat_id=None):
    _edited.append((message_id, text))
    return {"ok": True}


bot.delete_message = _fake_delete
bot.edit_message_text = _fake_edit
_now_iso = datetime.datetime.utcnow().isoformat() + "Z"
_store["published"] = [
    {"title_fa": "خبر قدیمی", "published": True, "message_id": 111, "published_at": _now_iso},
    {"title_fa": "خبر آخر", "summary_fa": "خلاصهٔ قبلی", "title": "Last", "lang": "en",
     "source": "S", "link": "http://x", "published": True, "message_id": 555, "published_at": _now_iso},
]

bot.ask_undo(42)
_kb = _admin_msgs[-1][1]["inline_keyboard"][0]
check("undo: تایید می‌خواهد و چیزی را قبل از تایید حذف نمی‌کند",
      [b["callback_data"] for b in _kb] == ["undo_yes", "undo_no"] and _deleted == [])
bot._handle_callback({"id": "c", "data": "undo_yes", "from": {"id": 42}}, {})
check("undo: آخرین پست از کانال حذف و در لاگ علامت می‌خورد",
      _deleted == [555] and _store["published"][-1]["deleted"] is True)
bot._handle_callback({"id": "c", "data": "undo_yes", "from": {"id": 42}}, {})
check("undo: پست حذف‌شده دوباره انتخاب نمی‌شود", _deleted == [555, 111])

_store["published"] = [
    {"title_fa": "خبر آخر", "summary_fa": "خلاصهٔ قبلی", "title": "Last", "lang": "en",
     "source": "S", "link": "http://x", "published": True, "message_id": 555, "published_at": _now_iso},
]
_state_edit = {}
check("editlast: حالت ویرایش را روشن می‌کند", bot.ask_edit_published(42, _state_edit) is True
      and _state_edit.get("_editing_pub") == 555)
bot._handle_message({"text": "خلاصهٔ کاملاً تازه توسط ادمین", "chat": {"id": 42}}, _state_edit)
check("editlast: متن کانال ویرایش و لاگ به‌روز می‌شود",
      _edited and _edited[-1][0] == 555 and "خلاصهٔ کاملاً تازه" in _edited[-1][1]
      and _store["published"][0]["summary_fa"].startswith("خلاصهٔ کاملاً تازه"))
check("editlast: حالت ویرایش پس از اعمال پاک می‌شود", _state_edit.get("_editing_pub") is None)

# پست قدیمی‌تر از ۴۸ ساعت قابل حذف نیست
_store["published"] = [{"title_fa": "کهنه", "published": True, "message_id": 9,
                        "published_at": "2020-01-01T00:00:00Z"}]
check("undo: پست قدیمی‌تر از ۴۸ ساعت انتخاب نمی‌شود", bot.last_published()[1] == -1)


# ───────────── سنجاق‌کردن (📌) ─────────────
print("Pin:")
_store["pending"] = [{"id": "pin1", "source": "S", "title_fa": "خبر مهم", "summary_fa": "x",
                      "importance": "high", "hashtags": ["AI"], "link": "http://x"}]
_store["approved"] = []
bot._handle_callback({"id": "c", "data": "pin:pin1", "from": {"id": 42}}, {})
check("pin: خبر تایید و با علامت سنجاق در صف انتشار می‌رود",
      _store["pending"] == [] and len(_store["approved"]) == 1
      and _store["approved"][0]["pin"] is True
      and _store["approved"][0]["admin_action"] == "pinned")

_pinned_ids = []
publisher.pin_message = lambda mid: _pinned_ids.append(mid)
publisher.send_message = lambda c, t, **kw: {"ok": True, "result": {"message_id": 321}}
publisher.storage.load_published = lambda: []
publisher.storage.save_published = lambda items: None
publisher.storage.save_approved = lambda items: None
publisher.publish_approved({"daily_post_count": 0,
                            "daily_post_date": datetime.datetime.utcnow().date().isoformat()},
                           [{"id": "pp", "title_fa": "خبر سنجاقی", "summary_fa": "x",
                             "importance": "high", "hashtags": ["AI"], "link": "http://x", "pin": True}])
check("publisher: خبر pin‌دار بعد از انتشار سنجاق می‌شود", _pinned_ids == [321])


# ───────────── قالب ایمن: لینک نامعتبر و HTML سالم ─────────────print("Safe message format:")
_bad = formatting.format_message({**item_en, "link": "javascript:alert(1)"})
check("لینک نامعتبر (javascript:) در پیام نمی‌آید",
      "javascript:" not in _bad and 'href=""' not in _bad)
_bad2 = formatting.format_message({**item_en, "link": 'http://x/"><b>evil'})
check("لینک حاوی کوتیشن/< کاملاً رد می‌شود (href ساخته نمی‌شود)",
      "<b>evil" not in _bad2 and 'href="' not in _bad2)
_long = formatting.format_message({**item_en, "summary_fa": "ی" * 9000, "summary": "x" * 9000,
                                   "hashtags": ["a" * 900]})
check("پیام بلند: زیر سقف و تگ‌های بسته",
      len(_long) <= 4000
      and _long.count("<b>") == _long.count("</b>")
      and _long.count("<blockquote") == _long.count("</blockquote>"))

_orig_sig = formatting.CHANNEL_SIGNATURE
formatting.CHANNEL_SIGNATURE = "@my_channel"
check("امضای کانال به انتهای پست اضافه می‌شود",
      formatting.format_message(item_en).rstrip().endswith("@my_channel"))
formatting.CHANNEL_SIGNATURE = ""
check("بدون تنظیم امضا، پست بدون امضاست", "@my_channel" not in formatting.format_message(item_en))
formatting.CHANNEL_SIGNATURE = _orig_sig


# ───────────── شکست KV باید دیده شود، نه نادیده ─────────────
print("KV failure handling:")
_orig_put = kv.put
try:
    kv.put = lambda k, v: False
    _raised = False
    try:
        storage.save_state({"daily_post_count": 1})
    except storage.KVError:
        _raised = True
    check("شکست نوشتن در KV استثنا می‌دهد (پست تکراری پیشگیری)", _raised)

    _raised2 = False
    try:
        storage.ensure_writable()
    except storage.KVError:
        _raised2 = True
    check("تست نوشتن preflight شکست را تشخیص می‌دهد", _raised2)
finally:
    kv.put = _orig_put

kv.put = lambda key, value: _store.__setitem__(key, value)
storage.save_state({"ok": True})
check("بعد از سالم‌شدن KV، نوشتن عادی کار می‌کند", _store.get("state") == {"ok": True})

print("Views, why-it-matters, brief:")
import src.views as _views  # noqa: E402
import src.why as _why  # noqa: E402
import src.brief as _brief  # noqa: E402

_view_html = (
    '<div data-post="chan/101"><span class="tgme_widget_message_views">1.2K</span></div>'
    '<div data-post="chan/102"><span class="tgme_widget_message_views">987</span></div>'
    '<div data-post="chan/103"><span>بدون بازدید</span></div>'
)
_view_map = _views.parse_views(_view_html)
check("بازدید: پارس ۱.۲K و عدد ساده", _view_map == {101: 1200, 102: 987})
check("بازدید: پسوند M", _views._to_int("2.5M") == 2_500_000)
check("بازدید: عدد فارسی", _views._to_int("۱٬۲۰۰") == 1200)

_now_v = datetime.datetime.utcnow()
_recent = _now_v.isoformat() + "Z"
_views_log = (
    [{"published": True, "category": "model_release", "views": 4000, "published_at": _recent} for _ in range(2)]
    + [{"published": True, "category": "other", "views": 1000, "published_at": _recent} for _ in range(2)]
)
_vstats = _views.stats(_views_log)
check("بازدید: میانگین کل و دسته", _vstats["count"] == 4 and _vstats["by_category"]["model_release"] == 4000)
_vitems = [{"category": "model_release", "hot": 50, "hot_reasons": []}]
_vn = _views.boost(_vitems, _views_log)
check("بازدید: دستهٔ پرخوان تقویت می‌شود", _vn == 1 and _vitems[0]["hot"] > 50)
check("بازدید: دلیل تقویت ثبت می‌شود", any("پرخوان" in r for r in _vitems[0]["hot_reasons"]))
_vtop = _views.top_report(_views_log, n=5)
check("بازدید: گزارش /top عنوان و عدد دارد", "پربازدید" in _vtop and "4.0K" in _vtop)

check("چرا مهم است: دستهٔ مدل", "گزینه" in _why.why_it_matters({"category": "model_release"}))
check("چرا مهم است: شایعه هشدار می‌دهد", "رسمی" in _why.why_it_matters({"category": "model_release", "confidence": "rumor"}))
check("چرا مهم است: دستهٔ ناشناخته خالی است", _why.why_it_matters({"category": "other"}) == "")
_why_msg = formatting.format_message({
    "title_fa": "خبر", "summary_fa": "خلاصه", "source": "OpenAI",
    "category": "model_release", "link": "https://x.com/a",
})
check("چرا مهم است: در پیام کانال می‌آید", "چرا مهم است" in _why_msg)

_now_b = datetime.datetime.utcnow()
_brief_items = [{
    "published": True, "category": "model_release", "title_fa": f"خبر {i}",
    "source": "منبع", "link": f"https://x.com/{i}",
    "published_at": _now_b.isoformat() + "Z", "importance": "high", "score": 80,
} for i in range(1, 7)]
_brief_pick = _brief.pick(_brief.recent_news(_brief_items, _now_b))
check("صبحانه: حداکثر ۵ خبر", len(_brief_pick) == 5)
_brief_txt = _brief.format_brief(_brief_pick)
check("صبحانه: عنوان و لینک دارد", "خبر" in _brief_txt and "https://x.com" in _brief_txt)
check("صبحانه: زیر سقف تلگرام", len(_brief_txt) <= 4000)

print("Quiz bank:")
check("کوییز: حداقل ۲۰ سؤال (چرخهٔ طولانی‌تر)", len(quiz.QUIZ_BANK) >= 20)
check("کوییز: همهٔ گزینه‌ها یکتا و ایندکس درست",
      all(len(set(q["options"])) == 4 and 0 <= q["correct"] < 4 for q in quiz.QUIZ_BANK))

# ─────────────────────────────────────────────────────────────
# Engagement loop v2: reactions, weekly poll, storylines, backup, tips, DMs
# ─────────────────────────────────────────────────────────────
import json
import tempfile

import src.weekly as weekly
import src.storylines as storylines
import src.backup as backup
import src.tips as tips
import src.subscribers as subscribers

print("Reactions:")
_store.clear()
_views._orig_reaction_dry = getattr(_views, "DRY_RUN", False)
_views.DRY_RUN = False
_views.REACTIONS_ENABLED = True
_total = _views.record_reactions(77, [
    {"type": {"emoji": "🔥"}, "total_count": 3},
    {"type": {"emoji": "❤️"}, "total_count": 1},
])
check("واکنش: جمع واکنش‌ها ذخیره می‌شود", _total == 4 and _store["reactions"]["77"]["total"] == 4)
_rx_total, _rx_top = _views.reaction_info({"message_id": 77})
check("واکنش: ایموجی غالب و تعداد", _rx_total == 4 and _rx_top == "🔥")
check("واکنش: در امتیاز محبوبیت وزن دارد", _views.engagement({"message_id": 77, "views": 5}) == 45)

_rx_log = [{"published": True, "message_id": 77, "title_fa": "خبر واکنشی", "views": 5,
            "category": "model_release", "published_at": datetime.datetime.utcnow().isoformat() + "Z"}]
_rx_top_txt = _views.top_report(_rx_log, n=3)
check("واکنش: /top ایموجی و تعداد را نشان می‌دهد", "🔥" in _rx_top_txt and "4" in _rx_top_txt)
check("واکنش: top_items بر اساس واکنش مرتب می‌کند", _views.top_items(_rx_log)[0]["message_id"] == 77)


class _JsonResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


_orig_bot_get = getattr(bot.requests, "get", None)
bot.requests.get = lambda *a, **k: _JsonResp({"ok": True, "result": [{
    "update_id": 909,
    "message_reaction_count": {"message_id": 88, "reactions": [
        {"type": {"emoji": "👍"}, "total_count": 6},
    ]},
}]})
_rx_state, _rx_handled = bot.poll_updates({})
if _orig_bot_get is None:
    del bot.requests.get
else:
    bot.requests.get = _orig_bot_get
check("واکنش: آپدیت message_reaction_count پردازش می‌شود",
      len(_rx_handled) == 1 and _rx_state["last_update_id"] == 909
      and _store["reactions"]["88"]["total"] == 6)

print("Weekly poll loop:")
_store.clear()
weekly.DRY_RUN = False
_orig_stop_poll = weekly.bot.stop_poll
weekly.bot.stop_poll = lambda mid: {"ok": True, "result": {"poll": {"options": [
    {"voter_count": 1}, {"voter_count": 6}, {"voter_count": 2}, {"voter_count": 0},
]}}}
_now_w = datetime.datetime.utcnow()
_store["weekly_state"] = {"poll": {
    "message_id": 55,
    "options": [
        {"category": "model_release", "text": "انتشار مدل جدید"},
        {"category": "tool_release", "text": "ابزار و محصول"},
        {"category": "research", "text": "پژوهش و مقاله"},
        {"category": "funding", "text": "سرمایه‌گذاری"},
    ],
    "closes_at": (_now_w - datetime.timedelta(hours=1)).isoformat() + "Z",
    "closed": False,
}}
_weekly_state = weekly.refresh(_now_w)
weekly.bot.stop_poll = _orig_stop_poll
check("نظرسنجی: بعد از زمان بسته می‌شود",
      _weekly_state["poll"]["closed"] is True and _weekly_state["poll"]["total_votes"] == 9)
check("نظرسنجی: برندهٔ رأی‌گیری ذخیره می‌شود",
      _weekly_state.get("result", {}).get("category") == "tool_release")
check("نظرسنجی: برنده تا روزهای بوست فعال است", weekly.winner_category(_now_w) == "tool_release")

_poll_items = [
    {"category": "tool_release", "hot": 50, "hot_reasons": []},
    {"category": "model_release", "hot": 50, "hot_reasons": []},
]
_poll_boosted = weekly.boost(_poll_items, _now_w)
check("نظرسنجی: فقط دستهٔ برنده بوست می‌گیرد",
      _poll_boosted == 1 and _poll_items[0]["hot"] == 56 and _poll_items[1]["hot"] == 50)
check("نظرسنجی: دلیل بوست ثبت می‌شود", any("مخاطبان" in r for r in _poll_items[0]["hot_reasons"]))

_poll_pick = _brief.pick([
    {"category": "model_release", "importance": "medium", "score": 95,
     "published_at": _now_w.isoformat() + "Z"},
    {"category": "tool_release", "importance": "medium", "score": 10,
     "published_at": _now_w.isoformat() + "Z"},
], limit=2)
check("نظرسنجی: صبحانه دستهٔ برنده را اول می‌گذارد",
      _poll_pick[0]["category"] == "tool_release")

_recap_log = _rx_log + [{"published": True, "category": "tool_release", "views": 900,
                          "title_fa": "خبر دوم", "published_at": _now_w.isoformat() + "Z"}]
_recap = weekly.format_recap(_recap_log, _recap_log, _now_w)
check("جمع‌بندی: عنوان و نوار روند دارد", "جمع‌بندی هفته" in _recap and "📊" in _recap)
check("جمع‌بندی: /weekly نتیجهٔ نظرسنجی را نشان می‌دهد", "انتخاب مخاطبان" in weekly.report(_now_w))

_expired = weekly.load_state()
_expired["result"]["expires_at"] = (_now_w - datetime.timedelta(days=1)).isoformat() + "Z"
weekly.save_state(_expired)
check("نظرسنجی: بعد از انقضا اثرش برداشته می‌شود", weekly.winner_category(_now_w) == "")
_expired["result"]["expires_at"] = (_now_w + datetime.timedelta(days=3)).isoformat() + "Z"
weekly.save_state(_expired)

print("Storylines:")
_store.clear()
storylines.CHANNEL_HANDLE = "@testchan"
storylines.CHANNEL_ID = "@testchan"
storylines.DRY_RUN = False
storylines.STORYLINES_ENABLED = True
_store["stories"] = {"openai": [{
    "id": "old-1", "title": "OpenAI GPT-5", "message_id": 11,
    "published_at": (_now_w - datetime.timedelta(days=2)).isoformat() + "Z",
}]}
_story_items = [{"id": "new-1", "title": "OpenAI releases a new model", "category": "model_release"}]
_story_links = storylines.enrich(_story_items, _now_w)
check("پیش‌زمینه: موجودیت‌ها استخراج می‌شوند", "openai" in storylines.extract({"title": "OpenAI news"}))
check("پیش‌زمینه: لینک پست قبلی اضافه می‌شود",
      _story_links == 1
      and _story_items[0]["background"][0]["link"] == "https://t.me/testchan/11")
# رگرسیون: «at» (datetime) نباید داخل آیتم بماند؛ آیتم در KV (pending/approved) ذخیره می‌شود
try:
    _story_json = __import__("json").dumps(_story_items, ensure_ascii=False)
except TypeError:
    _story_json = None
check("پیش‌زمینه: آیتم کاملاً JSON-پذیر است (باگ pending)", _story_json is not None)
check("پیش‌زمینه: فقط عنوان و لینک می‌ماند", set(_story_items[0]["background"][0]) == {"title", "link"})
_story_items2 = [{"id": "new-2", "title": "OpenAI releases another model", "category": "model_release"}]
_store["stories"] = {"openai": [{"id": "old-2", "title": "بدون تاریخ", "message_id": 12}]}
storylines.enrich(_story_items2, _now_w)
check("پیش‌زمینه: ردیف بدون تاریخ (datetime.min) هم JSON-پذیر است",
      __import__("json").dumps(_story_items2, ensure_ascii=False) and _story_items2[0]["background"][0]["link"].endswith("/12"))
storylines.record(_story_items[0], 22, _now_w)
_story_index = storylines.load_index()
check("پیش‌زمینه: پست جدید در تایم‌لاین ثبت می‌شود",
      any(r.get("message_id") == 22 for r in _story_index.get("openai", [])))

print("KV backup:")
_store.clear()
_store["state"] = {"daily_post_count": 2}
_store["published"] = [{"id": "x"}]
_store["unknown_key"] = "ignored"
_snap = backup.snapshot()
check("پشتیبان: کلیدهای مهم در snapshot هستند",
      _snap["keys"]["state"]["daily_post_count"] == 2 and _snap["keys"]["published"][0]["id"] == "x")
check("پشتیبان: کلید ناشناخته در snapshot نمی‌آید", "unknown_key" not in _snap["keys"])
_payload = json.loads(backup.payload_bytes().decode("utf-8"))
check("پشتیبان: فایل JSON معتبر است", _payload["format"] == "ai-news-bot-kv" and _payload["version"] == 1)
_backup_dir = tempfile.mkdtemp()
_backup_path = os.path.join(_backup_dir, "backup.json")
with open(_backup_path, "w", encoding="utf-8") as _f:
    json.dump({"keys": {"state": {"daily_post_count": 7}, "unknown_key": 1}}, _f)
_orig_backup_put = backup.cloudflare_kv.put
backup.cloudflare_kv.put = lambda k, v: (_store.__setitem__(k, v) or True)
_written = backup.restore(_backup_path)
backup.cloudflare_kv.put = _orig_backup_put
check("بازیابی: فقط کلیدهای شناخته‌شده نوشته می‌شوند",
      _written == 1 and _store["state"]["daily_post_count"] == 7
      and _store.get("unknown_key") == "ignored")

print("Reader tips:")
_store.clear()
tips.TIPS_ENABLED = True
tips.DRY_RUN = False
_ok, _msg, _tip = tips.add(901, "علی", "این یک سوژهٔ خبری تازه برای کانال است")
check("سوژه: متن معتبر ذخیره می‌شود", _ok is True and isinstance(_tip, dict) and tips.find(_tip["id"]))
_ok2, _msg2, _ = tips.add(901, "علی", "یک سوژهٔ دیگر در همان ساعت برای تست")
check("سوژه: محدودیت نرخ ساعتی اعمال می‌شود", _ok2 is False and "سهمیه" in _msg2)
check("سوژه: در صف بررسی ادمین است", len(tips.pending()) == 1)
check("سوژه: متن کاربر escape می‌شود",
      "<script>" not in tips.format_post({"user": "<x>", "text": "<script>alert(1)</script>"}))
_ok3, _msg3, _tip3 = tips.add(902, "مریم", "سوژهٔ دوم از کاربر دیگر برای تست")
check("سوژه: کاربر دیگر سهمیهٔ جدا دارد", _ok3 is True and _tip3 is not None)
tips.TIPS_MAX_PER_HOUR = 2
check("سوژه: تنظیم سقف ساعتی بیشتر رعایت می‌شود",
      tips.add(903, "سارا", "سوژهٔ اول با سقف دو در ساعت")[0] is True
      and tips.add(903, "سارا", "سوژهٔ دوم با سقف دو در ساعت")[0] is True
      and tips.add(903, "سارا", "سوژهٔ سوم باید رد شود")[0] is False)
tips.TIPS_MAX_PER_HOUR = 1
tips.mark(_tip["id"], "approved")
check("سوژه: بعد از بررسی از صف خارج می‌شود", all(t.get("id") != _tip["id"] for t in tips.pending()))

print("DM subscribers:")
_store.clear()
subscribers.DM_DIGEST_MAX = 2
check("اشتراک: افزودن کاربر جدید", subscribers.add(1001, "ali") is True)
check("اشتراک: افزودن دوباره تکراری نیست", subscribers.add(1001, "ali") is False)
check("اشتراک: شمارش درست است", subscribers.count() == 1)
check("اشتراک: سقف ظرفیت رعایت می‌شود",
      subscribers.add(1002, "b") is True and subscribers.add(1003, "c") is False)
check("اشتراک: حذف کار می‌کند",
      subscribers.remove(1001) is True and subscribers.remove(1001) is False and subscribers.count() == 1)

print("Publishing hygiene & translation retry:")
_store.clear()
publisher.QUIET_HOURS = "0-7"
check("بهداشت: ساعت سکوت تشخیص داده می‌شود",
      publisher._quiet_hours(datetime.datetime(2026, 1, 1, 22, 0)) is True)
check("بهداشت: خارج از ساعت سکوت مانعی نیست",
      publisher._quiet_hours(datetime.datetime(2026, 1, 1, 10, 0)) is False)
publisher.PUBLISH_MIN_GAP_MIN = 20
_gap_now = datetime.datetime.utcnow()
check("بهداشت: فاصلهٔ کوتاه بین راندها رد می‌شود",
      publisher._gap_ok({"last_publish_at": (_gap_now - datetime.timedelta(minutes=10)).isoformat() + "Z"}, _gap_now) is False)
check("بهداشت: فاصلهٔ کافی مجاز است",
      publisher._gap_ok({"last_publish_at": (_gap_now - datetime.timedelta(minutes=30)).isoformat() + "Z"}, _gap_now) is True)

publisher.SHARE_BUTTON_ENABLED = True
_views.CHANNEL_HANDLE = "@testchan"
_share = publisher._share_markup({"title": "خبر تست"}, 5)
_share_url = _share["inline_keyboard"][0][0]["url"] if _share else ""
check("اشتراک‌گذاری: دکمهٔ لینک پست کانال ساخته می‌شود",
      "t.me/share" in _share_url and "t.me/testchan/5" in __import__("urllib.parse", fromlist=["unquote"]).unquote(_share_url))

publisher.TRANSLATE_RETRY_MAX = 2
publisher.QUIET_HOURS = ""
publisher.PUBLISH_MIN_GAP_MIN = 0
_orig_localize = analyzer.localize
_orig_pub_send = publisher.send_message
_orig_pub_edit = publisher.edit_message_reply_markup
_orig_pub_story = publisher.storylines.record
_orig_bale_mirror = publisher.bale.mirror
_orig_bsky_mirror = publisher.bluesky.mirror
analyzer.localize = lambda item: None
_sent_tr = []
publisher.send_message = lambda c, t, **kw: (_sent_tr.append(t) or {"ok": True, "result": {"message_id": 1}})
publisher.edit_message_reply_markup = lambda *a, **k: {"ok": True}
publisher.storylines.record = lambda *a, **k: True
publisher.bale.mirror = lambda it: None
publisher.bluesky.mirror = lambda it: None
publisher.storage.load_published = lambda: []
publisher.storage.save_published = lambda items: None
_saved_tr = []
publisher.storage.save_approved = lambda items: _saved_tr.extend(items)

_tr_item = {"id": "tr-1", "title_fa": "خبر", "summary_fa": "خلاصه",
            "link": "https://x.com/a", "translated": False, "hold_translation": True}
_tr_state = {"daily_post_count": 0,
             "daily_post_date": datetime.datetime.utcnow().date().isoformat()}
publisher.publish_approved(_tr_state, [_tr_item])
check("ترجمه: شکست اول یک راند در صف می‌ماند",
      _tr_item.get("translate_tries") == 1 and _tr_item.get("hold_translation") is True
      and not _sent_tr and _saved_tr)
_tr_item["translated"] = True
publisher.publish_approved(_tr_state, [_tr_item])
check("ترجمه: بعد از موفقیت منتشر می‌شود",
      _tr_item.get("hold_translation") is False and len(_sent_tr) == 1
      and _tr_item.get("status") == "published")

analyzer.localize = _orig_localize
publisher.send_message = _orig_pub_send
publisher.edit_message_reply_markup = _orig_pub_edit
publisher.storylines.record = _orig_pub_story
publisher.bale.mirror = _orig_bale_mirror
publisher.bluesky.mirror = _orig_bsky_mirror


# ── سلامت ماژول‌ها، خطاگیر main، ساعت و شمارندهٔ KV ──
print("Module health:")
import main as _main  # noqa: E402
import src.modhealth as modhealth  # noqa: E402
import src.storage as _storage  # noqa: E402
from src import clock as _clock  # noqa: E402

modhealth.reset()
check("clock.utcnow: naive و نزدیک به ساعت واقعی",
      _clock.utcnow().tzinfo is None
      and abs((_clock.utcnow() - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds()) < 5)


def _boom(*a, **k):
    raise ValueError("خراب")


check("_safe: خطای ماژول اختیاری pipeline را نمی‌خواباند", _main._safe("x", _boom, default="d") == "d")
check("_safe: خطا ثبت می‌شود", modhealth.current()["x"]["ok"] is False and "ValueError" in modhealth.current()["x"]["err"])
check("_safe: نتیجهٔ موفق برمی‌گردد", _main._safe("y", lambda v: v + 1, 1) == 2 and modhealth.current()["y"]["ok"])


def _kverr(*a, **k):
    raise _storage.KVError("kv")


try:
    _main._safe("z", _kverr)
    _kv_passes = False
except _storage.KVError:
    _kv_passes = True
check("_safe: KVError عبور می‌کند (خطر پست تکراری)", _kv_passes)

_prev = {}
for _i in range(3):
    modhealth.reset()
    modhealth.fail("views", ValueError("x"))
    _prev, _al = modhealth.merge(_prev)
check("هشدار دقیقاً در سومین خطای پیاپی", _al == ["views"] and _prev["views"]["fails"] == 3)
modhealth.reset()
modhealth.fail("views", ValueError("x"))
_prev, _al = modhealth.merge(_prev)
check("بعد از آستانه دوباره هشدار نمی‌دهد", _al == [] and _prev["views"]["fails"] == 4)
modhealth.reset()
modhealth.ok("views")
_prev, _al = modhealth.merge(_prev)
check("موفقیت شمارندهٔ خطا را صفر می‌کند", _prev["views"]["fails"] == 0 and _prev["views"]["ok"] and _prev["views"]["last_ok"])
modhealth.reset()
_prev2, _ = modhealth.merge(_prev)
check("ماژولی که این اجرا نیامد سابقه‌اش حفظ می‌شود", "views" in _prev2)

modhealth.reset()
modhealth.fail("brief", RuntimeError("boom"))
_alerts = _storage.heartbeat({"stage": "waiting"})
_hb = _store.get("heartbeat", {})
check("heartbeat: وضعیت ماژول‌ها و شمارندهٔ KV ذخیره می‌شود",
      _hb.get("modules", {}).get("brief", {}).get("ok") is False and "kv" in _hb and _alerts == [])
modhealth.reset()

# ── بانک سؤال برای Worker ──
print("Quiz bank sync:")
_qb = quiz.bank_for_worker()
check("بانک Worker: همهٔ سؤال‌ها با ۴ گزینه و اندیس درست معتبر", len(_qb) == len(quiz.QUIZ_BANK) and all(len(q["o"]) == 4 and 0 <= q["c"] < 4 for q in _qb))
check("بانک Worker: id یکتا و برای callback_data کوتاه (<۴۰)", len({q["id"] for q in _qb}) == len(_qb) and all(len(q["id"]) < 40 and ":" not in q["id"] for q in _qb))
_store.pop("quiz_bank", None); _store.pop("quiz_bank_ver", None)
check("sync_bank: بار اول می‌نویسد", quiz.sync_bank() is True and _store["quiz_bank_ver"] == quiz.bank_version() and len(_store["quiz_bank"]) == len(_qb))
check("sync_bank: بار دوم بدون تغییر چیزی نمی‌نویسد", (lambda before: quiz.sync_bank() is False and _store["quiz_bank"] is before)(_store["quiz_bank"]))
_orig_bank = list(quiz.QUIZ_BANK)
quiz.QUIZ_BANK.append({**_orig_bank[0], "id": "zz-new"})
check("sync_bank: با تغییر بانک دوباره نوشته می‌شود", quiz.sync_bank() is True and any(q["id"] == "zz-new" for q in _store["quiz_bank"]))
quiz.QUIZ_BANK[:] = _orig_bank
quiz.sync_bank()

# ── 📣 تبلیغ دوره‌ای پلتفرم ──
print("Promo:")
from datetime import datetime as _dt, timedelta as _td  # noqa: E402
from src import promo  # noqa: E402

_sent = []
promo.send_message = lambda chat, text, **kw: (_sent.append((chat, text, kw)), {"ok": True, "result": {"message_id": 1}})[1]
promo.CHANNEL_ID = "@chan"
promo.SITE_URL = "https://u.github.io/repo/"
promo.BOT_USERNAME = "my_bot"
promo.PROMO_ENABLED = True
promo.DRY_RUN = False
promo.DISPLAY_TZ_OFFSET_MIN = 210
promo.PROMO_HOUR = 18
promo.PROMO_EVERY_DAYS = 3
promo.EDU_PUBLISH_HOURS = (19, 20, 21, 22)
# ۱۸:۱۰ تهران = ۱۴:۴۰ UTC
_t18 = _dt(2026, 11, 24, 14, 40)


def _reset_promo():
    _sent.clear()
    _store.pop("promo_state", None)


_reset_promo()
promo.maybe_post({}, now=_dt(2026, 11, 24, 9, 0))
check("خارج از ساعت مجاز: نمی‌فرستد", not _sent)
promo.maybe_post({}, now=_t18)
check("در ساعت ۱۸: می‌فرستد", len(_sent) == 1 and _sent[0][0] == "@chan")
_kb = _sent[0][2]["reply_markup"]["inline_keyboard"]
check("دکمه‌ها: سایت و ربات", _kb[0][0]["url"] == "https://u.github.io/repo/" and _kb[1][0]["url"] == "https://t.me/my_bot")
check("متن بدون لینک خام (بدون پیش‌نمایش)", "http" not in _sent[0][1])
check("حالت ذخیره شد", _store["promo_state"]["count"] == 1 and _store["promo_state"]["idx"] == 1)
promo.maybe_post({}, now=_t18 + _td(hours=1))
check("همان روز دوباره نمی‌فرستد", len(_sent) == 1)
promo.maybe_post({}, now=_t18 + _td(days=2))
check("بعد از ۲ روز هنوز زود است", len(_sent) == 1)
promo.maybe_post({}, now=_t18 + _td(days=3))
check("بعد از ۳ روز می‌فرستد", len(_sent) == 2)
check("قالب بعدی می‌چرخد", _sent[0][1] != _sent[1][1])
_seen_t = {promo.pick_template(i) for i in range(8)}
check("همهٔ قالب‌ها چرخشی‌اند", len(_seen_t) == len(promo.TEMPLATES))
check("قالب‌ها HTML متوازن و بدون لینک", all(t.count("<b>") == t.count("</b>") and "http" not in t for t in promo.TEMPLATES))

_reset_promo()
promo.maybe_post({}, now=_dt(2026, 11, 24, 15, 40))  # ۱۹:۱۰ تهران = ساعت پست آموزشی
check("ساعت پست آموزشی (۱۹): نمی‌آید", not _sent)
check("ساعت‌های مجاز فقط ۱۸", promo.allowed_hours() == [18])
promo.PROMO_HOUR = 20
check("اگر ساعت تبلیغ با پست آموزشی هم‌پوشان شود: خالی", promo.allowed_hours() == [])
promo.PROMO_HOUR = 17
check("PROMO_HOUR=17: ۱۷ و ۱۸ مجاز", promo.allowed_hours() == [17, 18])
promo.PROMO_HOUR = 18

_reset_promo()
promo.maybe_post({"paused": True}, now=_t18)
promo.maybe_post({"promo_enabled": False}, now=_t18)
check("pause و /promo off احترام گذاشته می‌شود", not _sent)
promo.PROMO_ENABLED = False
promo.maybe_post({}, now=_t18)
promo.PROMO_ENABLED = True
check("PROMO_ENABLED=false خاموش است", not _sent)
promo.SITE_URL = ""
promo.maybe_post({}, now=_t18)
promo.SITE_URL = "javascript:alert(1)"
promo.maybe_post({}, now=_t18)
promo.SITE_URL = "https://u.github.io/repo/"
check("بدون SITE_URL معتبر چیزی نمی‌رود", not _sent)
promo.DRY_RUN = True
promo.maybe_post({}, now=_t18)
promo.DRY_RUN = False
check("dry-run نمی‌فرستد", not _sent)

_reset_promo()
_st = promo.maybe_post({"promo_force": True}, now=_dt(2026, 11, 24, 9, 0))
check("/promo now: خارج از ساعت هم می‌فرستد و پرچم پاک می‌شود", len(_sent) == 1 and _st["promo_force"] is False)

_reset_promo()
promo.send_message = lambda *a, **k: {"ok": False}
promo.maybe_post({}, now=_t18)
check("شکست ارسال: حالت ذخیره نمی‌شود (دوباره تلاش می‌کند)", "promo_state" not in _store)
promo.send_message = lambda chat, text, **kw: (_sent.append((chat, text, kw)), {"ok": True})[1]

promo.BOT_USERNAME = ""
promo._bot_username = lambda: ""
_reset_promo()
promo.maybe_post({}, now=_t18)
check("بدون یوزرنیم ربات: فقط دکمهٔ سایت", len(_sent[0][2]["reply_markup"]["inline_keyboard"]) == 1)
promo.BOT_USERNAME = "my_bot"

# ── انتها‌به‌انتهای main.run: شکست نوشتن pending نباید خبر یا دکمه را گم کند ──
print("Pipeline e2e (KV failure safety):")
_orig = {k: getattr(_main, k) for k in ("collect", "poll_updates", "analyze_articles", "notify_admin_new", "_send_review",
                                         "send_message", "publish_approved")}
_notices = []
_ok_when_notified = []


def _e2e_item():
    return {
        "id": "e2e-1", "source": "TestSrc", "lang": "en", "trusted": True, "official": False, "fast_track": False,
        "title": "OpenAI releases GPT-9", "link": "https://e.test/1", "summary": "OpenAI released a new model today.",
        "title_fa": "OpenAI مدل GPT-9 را منتشر کرد", "summary_fa": "مدل جدید امروز منتشر شد.", "importance": "high",
        "confidence": "high", "category": "model_release", "published_at": None, "status": "new", "published": False,
        "translated": True, "source_trust": 3.0, "score": 10, "hot": 30, "hot_threshold": 60.0,
    }


def _setup_e2e(fail_pending):
    _store.clear()
    _store["state"] = {"auto_publish_enabled": False}
    _store["seen"] = ["old"]
    _notices.clear()
    _ok_when_notified.clear()
    _main.collect = lambda seen: [_e2e_item()]
    _main.poll_updates = lambda st: (st, [])
    _main.analyze_articles = lambda arts, stats: [dict(a) for a in arts]
    _main.send_message = lambda *a, **k: {"ok": True}
    _main.publish_approved = lambda st, items: st

    def _note(it):
        _notices.append(it["id"])
        _ok_when_notified.append(any(p.get("id") == it["id"] for p in _store.get("pending", [])))

    _main.notify_admin_new = _note
    _main._send_review = _note
    for mod, fn in ((_main.edu_post, "maybe_post"), (_main.digest, "maybe_post"), (_main.arxiv_daily, "maybe_post"),
                    (_main.brief, "maybe_post"), (_main.weekly, "maybe_post"), (_main.promo, "maybe_post")):
        setattr(mod, fn, lambda st, *a, **k: st)
    _main.backup.maybe_send = lambda st: None
    _main.hn.enrich = lambda items, limit=0: items
    _main.views.sync = lambda: None

    def _put(key, value):
        if fail_pending and key == "pending":
            return False
        _store[key] = value
    kv.put = _put


_setup_e2e(fail_pending=True)
try:
    _main.run(force=True)
    _raised = False
except (storage.KVError, SystemExit):  # run() خطای KV را FATAL می‌کند و با کد خروج ۱ تمام می‌شود
    _raised = True
check("e2e: شکست نوشتن pending → اجرا متوقف می‌شود (جلوگیری از پست تکراری)", _raised)
check("e2e: دکمهٔ ادمین برای خبری که ذخیره نشد فرستاده نمی‌شود", _notices == [])
check("e2e: خبرِ ذخیره‌نشده از seen برمی‌گردد تا دوباره جمع شود", "e2e-1" not in _store.get("seen", []) and "old" in _store["seen"])

_setup_e2e(fail_pending=False)
_main.run(force=True)
check("e2e: حالت عادی: خبر در pending ذخیره می‌شود", any(p.get("id") == "e2e-1" for p in _store.get("pending", [])))
check("e2e: اعلان ادمین فقط بعد از ذخیره‌شدن pending", _notices == ["e2e-1"] and _ok_when_notified == [True])
check("e2e: خبر در seen می‌ماند", "e2e-1" in _store["seen"])
check("e2e: آیتم pending کاملاً JSON-پذیر است", bool(__import__("json").dumps(_store["pending"], default=None)))

for _k, _v in _orig.items():
    setattr(_main, _k, _v)

print("KV JSON safety net:")
import json as _json2  # noqa: E402
from src import cloudflare_kv as _ckv  # noqa: E402
_dumped = _json2.loads(_json2.dumps({"a": _dt(2026, 11, 25, 6, 17), "d": _dt(2026, 1, 2).date(), "s": {"b", "a"}}, default=_ckv._json_fallback))
check("KV: datetime/date/set به مقدار JSON-امن تبدیل می‌شود", _dumped == {"a": "2026-11-25T06:17:00Z", "d": "2026-01-02", "s": ["a", "b"]})
try:
    _json2.dumps({"x": object()}, default=_ckv._json_fallback)
    _unk = False
except TypeError:
    _unk = True
check("KV: نوع ناشناخته همچنان خطا می‌دهد (پنهان نمی‌شود)", _unk)

# ── 📚 مسیر یادگیری ۳۰روزه ──
print("Learning path:")
from src import learn  # noqa: E402
_cw = learn.course_for_worker()
check("learn: ۳۰ درس و ۱۰ درس برای هر سطح", len(_cw) == 30 and all(sum(1 for x in _cw if x["lv"] == lv) == 10 for lv in learn.LEVELS))
check("learn: ترتیب سطح‌ها مبتدی→متوسط→پیشرفته", [x["lv"] for x in _cw] == ["beginner"] * 10 + ["intermediate"] * 10 + ["advanced"] * 10)
check("learn: id یکتا", len({x["id"] for x in _cw}) == 30)
check("learn: هر درس عنوان/متن/تمرین دارد و طول مناسب پیام تلگرام", all(x["t"] and 100 < len(x["b"]) < 900 and x["x"] for x in _cw))
check("learn: HTML خام در متن درس نیست", all("<" not in x["b"] + x["t"] + x["x"] for x in _cw))
_store.pop("learn_course", None); _store.pop("learn_course_ver", None)
check("sync_course: بار اول می‌نویسد", learn.sync_course() is True and len(_store["learn_course"]["lessons"]) == 30 and _store["learn_course"]["levels"] == learn.LEVELS)
check("sync_course: بار دوم چیزی نمی‌نویسد", (lambda before: learn.sync_course() is False and _store["learn_course"] is before)(_store["learn_course"]))
_orig_course = list(learn.COURSE)
learn.COURSE[0] = {**_orig_course[0], "title": "عنوان تغییرکرده"}
check("sync_course: با تغییر درس دوباره می‌نویسد", learn.sync_course() is True and _store["learn_course"]["lessons"][0]["t"] == "عنوان تغییرکرده")
learn.COURSE[:] = _orig_course
learn.sync_course()

print(f"\n{_passed} تست با موفقیت گذشت ✅")
