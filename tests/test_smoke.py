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
import src.storage as storage  # noqa: E402

_passed = 0


def check(label, condition):
    global _passed
    assert condition, f"FAILED: {label}"
    _passed += 1
    print(f"  ok - {label}")


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
check("edu: بازهٔ پیش‌فرض طولانی است (۲۴ ساعت)", _cfg.EDU_EVERY_HOURS == 24)
check("edu: سقف توکن پیش‌فرض کم است", _cfg.EDU_MAX_OUTPUT_TOKENS <= 1000)
_edu.EDU_EVERY_HOURS = 2

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
_edu.OPENROUTER_MODELS = ["or/m1:free", "or/m2:free", "or/m3:free"]
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
_p, _s = _router(gemini=[200], orouter={"or/m1:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("اولویت: Gemini جواب قابل‌قبول داد → OpenRouter اصلاً صدا زده نمی‌شود", _s["or"] == [] and _edu._RUN["provider"] == "gemini")
_p, _s = _router(gemini=["bad"], orouter={"or/m1:free": _good})
_edu.requests.post = _p
_r = _edu.generate("x", [], accept=_acc)
check("Gemini جواب بی‌کیفیت (غیرفارسی) داد → مدل بعدی OpenRouter", _r == _good and _s["or"] == ["or/m1:free"] and _s["g"] == 1)
_p, _s = _router(gemini=["junk"], orouter={"or/m1:free": _good})
_edu.requests.post = _p
check("Gemini متن بدون JSON داد → fallback (قبلاً None می‌شد)", _edu.generate("x", [], accept=_acc) == _good and _s["or"] == ["or/m1:free"])
_p, _s = _router(gemini=[503], orouter={"or/m2:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("اولین مدل تاییدشده OpenRouter برنده است؛ m3 صدا زده نمی‌شود", _s["or"] == ["or/m1:free", "or/m2:free"] and _edu._RUN["model"] == "or/m2:free")
_p, _s = _router(gemini=[503], orouter={"or/m1:free": _bad_en, "or/m2:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc)
check("مدل رایگانی که خروجی بی‌کیفیت داد رد می‌شود و بعدی امتحان می‌شود", _s["or"] == ["or/m1:free", "or/m2:free"])
_p, _s = _router(gemini=[503], orouter={})
_edu.requests.post = _p
check("همه شکست خوردند → None، فقط حداکثر ۳ مدل رایگان", _edu.generate("x", [], accept=_acc) is None and len(_s["or"]) == 3)
_p, _s = _router(gemini=[200], orouter={"or/m1:free": _good})
_edu.requests.post = _p
_edu.generate("x", [], accept=_acc, use_gemini=False)
check("use_gemini=False: مستقیم OpenRouter و بدون تماس با Gemini", _s["g"] == 0 and _s["or"] == ["or/m1:free"])

# _run: بدون کلید Gemini یا با بودجهٔ تمام‌شده، OpenRouter کار می‌کند
_p, _s = _router(gemini=[200], orouter={"or/m1:free": {**_good, "title": "ترفند تازه برای نوشتن گزارش هفتگی با هوش مصنوعی"}})
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
      and _k2["edu_state"]["recent"][-1]["via"] == "openrouter/or/m1:free")
_edu.GEMINI_API_KEY = "k"
_edu.gemini_budget.can_call = lambda: False
_k2.clear(); _sent2.clear()
_edu.maybe_post({}, _dt.datetime(2026, 10, 27, 5, 17))
check("بودجهٔ Gemini تمام شده: باز هم از OpenRouter پست می‌شود", len(_sent2) == 1 and _s["g"] == 0)
_edu.OPENROUTER_API_KEY = ""
_k2.clear(); _sent2.clear()
_edu.maybe_post({}, _dt.datetime(2026, 10, 27, 5, 17))
check("نه بودجهٔ Gemini نه کلید OpenRouter: هیچ‌کاری نمی‌کند", _sent2 == [])
_edu.OPENROUTER_API_KEY = "or-test"

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
_edu.gemini_budget.can_call = lambda: True
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
check("اگر تلگرام نرود: چیزی ثبت یا به بله ارسال نمی‌شود", len(_bl) == _nb and _kvstore["edu_state"]["last_slot"] != _edu.current_slot(_T + _dt.timedelta(hours=8))[0])
_edu.generate = lambda desc, recent, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
check("استثنای غیرمنتظره بیرون نمی‌آید", _edu.maybe_post({}, _T + _dt.timedelta(hours=10)) == {})

print(f"\n{_passed} تست با موفقیت گذشت ✅")
