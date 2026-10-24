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
check("جمع‌آوری با وجود فید خراب ادامه می‌یابد", len(got) == 3)
check("اخبار تکراری (seen) دوباره جمع نمی‌شوند",
      len(collectors.collect({a["id"] for a in got})) == 0)
collectors.FEEDS = real_feeds

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

print(f"\n{_passed} تست با موفقیت گذشت ✅")
