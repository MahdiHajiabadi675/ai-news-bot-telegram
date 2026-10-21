"""
تست دودی (Smoke tests) — بدون نیاز به شبکه یا نصب وابستگی‌ها

اجرا:
    python tests/test_smoke.py

این تست‌ها روی منطق مهم تمرکز دارن:
  • بودجه Gemini (ضد بن شدن)
  • سقف batch و ترجمه ساده برای بقیه
  • امتیازدهی و dedup
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
import src.collectors as collectors  # noqa: E402
import src.fallback_translator as translator  # noqa: E402
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


# ───────────── آنالایزر ─────────────
def article(i, lang="en"):
    return {
        "id": f"id{i}", "source": "OpenAI Blog", "lang": lang, "trusted": True,
        "title": f"title {i}", "link": "http://x", "summary": "s",
    }


print("Analyzer:")
# وقتی بودجه تمام است → ترجمه ساده، بدون هیچ درخواستی به Gemini
reset_budget()
_store["gemini_budget"] = {"date": today(), "count": 999, "last_call_at": minutes_ago(5)}
called = {"n": 0}
analyzer._try_gemini_once = lambda batch: called.__setitem__("n", called["n"] + 1)
items, meta = analyzer.analyze_articles([article(1)])
check("بودجه تمام → Gemini صدا زده نمی‌شود", called["n"] == 0)
check("بودجه تمام → meta.skipped_budget", meta["skipped_budget"] is True)
check("بودجه تمام → آیتم با برچسب fallback", items[0]["fallback"] is True)

# سقف batch: فقط N خبر به Gemini می‌رود، بقیه ترجمه ساده
reset_budget()
analyzer.GEMINI_BATCH_SIZE = 2
seen_batch = {}


def fake_gemini(batch):
    seen_batch["n"] = len(batch)
    return [{"i": i + 1, "relevant": True, "title_fa": f"t{i}", "summary_fa": "s"}
            for i in range(len(batch))]


analyzer._try_gemini_once = fake_gemini
items, meta = analyzer.analyze_articles([article(i) for i in range(5)])
check("حداکثر یک درخواست با N خبر", seen_batch["n"] == 2)
check("همه اخبار مسیردهی می‌شوند", len(items) == 5)
check("meta.gemini_used ثبت می‌شود", meta["gemini_used"] is True)
check("بقیه به‌عنوان ترجمه ساده شمرده می‌شوند", meta["fallback_count"] == 3)

# خطای Gemini → همه fallback، ولی پرچم failed
reset_budget()
analyzer._try_gemini_once = lambda batch: None
items, meta = analyzer.analyze_articles([article(1)])
check("خطای Gemini → gemini_failed", meta["gemini_failed"] is True)
check("خطای Gemini → همه ترجمه ساده", meta["fallback_count"] == 1)

# اخبار فارسی هرگز به Gemini نمی‌روند
reset_budget()
analyzer.GEMINI_BATCH_SIZE = 10
called["n"] = 0
analyzer._try_gemini_once = lambda batch: called.__setitem__("n", called["n"] + 1)
items, meta = analyzer.analyze_articles([article(1, lang="fa")])
check("خبر فارسی → Gemini صدا زده نمی‌شود", called["n"] == 0)
check("خبر فارسی → پردازش محلی", meta["persian_local"] == 1)
check("خبر فارسی → عنوان فارسی حفظ می‌شود", items[0]["title_fa"] == "title 1")


# ───────────── امتیازدهی و dedup ─────────────
print("Scoring & dedup:")
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("mainmod", os.path.join(ROOT, "main.py"))
mainmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mainmod)

high = mainmod.calculate_score({
    "source": "OpenAI Blog", "importance": "high",
    "confidence": "confirmed", "category": "model_release"})
low = mainmod.calculate_score({
    "source": "دیجیاتو", "importance": "medium",
    "confidence": "likely", "category": "other"})
check("خبر مهم و معتبر امتیاز کامل می‌گیرد", high == 100)
check("خبر معمولی امتیاز کمتری می‌گیرد", low < high)

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

print(f"\n{_passed} تست با موفقیت گذشت ✅")
