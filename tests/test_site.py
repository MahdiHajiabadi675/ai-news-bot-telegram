"""تست ساخت وب‌سایت ایستا (بدون شبکه)."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
import build  # noqa: E402

passed = 0


def check(name, cond):
    global passed
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  ok - {name}")


print("Jalali date:")
check("نوروز ۱۴۰۵", build.to_jalali(2026, 3, 21) == (1405, 1, 1))
check("۳ آذر ۱۴۰۵", build.to_jalali(2026, 11, 24) == (1405, 9, 3))
check("نوروز ۱۴۰۴", build.to_jalali(2025, 3, 21) == (1404, 1, 1))
check("کبیسه: ۲۹ اسفند ۱۳۹۹", build.to_jalali(2021, 3, 20) == (1399, 12, 30))
check("fa_date فارسی", build.fa_date("2026-11-24T10:00:00Z") == "۳ آذر ۱۴۰۵")
check("fa_date نامعتبر → خالی", build.fa_date("garbage") == "")

print("Prepare items:")
raw = [
    {"id": "a1", "title": "خبر <b>اول</b>", "summary": "خلاصه", "source": "S", "link": "https://e.com/1", "category": "research",
     "published_at": "2026-11-23T10:00:00Z", "tags": ["AI", "a b!"], "message_id": 7},
    {"id": "a1", "title": "خبر تکراری‌شناسه", "link": "javascript:alert(1)", "category": "../evil", "published_at": "2026-11-24T10:00:00Z"},
    {"id": "", "title": "بدون شناسه", "link": "https://e.com/3", "published_at": "2026-11-22T10:00:00Z"},
    {"title": ""},
    "junk",
    {"id": "x/../../y", "title": "شناسهٔ خطرناک", "published_at": "2026-11-21T10:00:00Z"},
]
items = build.prepare_items(raw)
slugs = [i["slug"] for i in items]
check("بدون‌عنوان و غیر dict حذف می‌شود", len(items) == 4)
check("slug یکتا", len(set(slugs)) == 4 and "a1" in slugs and "a1-2" in slugs)
check("slug فقط نویسه‌های امن", all(__import__("re").fullmatch(r"[A-Za-z0-9_-]+", s) for s in slugs))
check("مرتب‌سازی جدید به قدیم", items[0]["published_at"] >= items[-1]["published_at"])
check("لینک غیر http حذف می‌شود", any(i["link"] == "" for i in items))
check("دستهٔ مخرب پاک‌سازی می‌شود", all("/" not in i["category"] and "." not in i["category"] for i in items))

print("Build:")
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "dist"
    n = build.build(items, out, "https://u.github.io/repo", "اخبار", "https://t.me/chan")
    check("تعداد خبر", n == 4)
    for f in ("index.html", "404.html", "search.json", "sitemap.xml", "robots.txt", ".nojekyll", "archive/1.html", "c/research.html"):
        check(f"فایل {f}", (out / f).exists())
    check("صفحهٔ هر خبر", all((out / "p" / f"{s}.html").exists() for s in slugs))
    post = (out / "p" / "a1.html").read_text(encoding="utf-8")
    check("XSS: عنوان escape می‌شود", "<b>اول</b>" not in post and "&lt;b&gt;" in post)
    check("لینک منبع و rel=nofollow", 'href="https://e.com/1"' in post and "nofollow" in post)
    check("لینک پست کانال", "https://t.me/chan/7" in post)
    check("JSON-LD معتبر", '"@type": "NewsArticle"' in post)
    check("canonical", 'rel="canonical" href="https://u.github.io/repo/p/a1.html"' in post)
    check("لینک‌های داخلی نسبی (کار روی /repo/)", 'href="../index.html"' in post and 'href="/' not in post.replace('href="//', ""))
    bad = (out / "p" / "a1-2.html").read_text(encoding="utf-8")
    check("لینک خطرناک در صفحه نیست", "javascript:" not in bad)
    idx = (out / "index.html").read_text(encoding="utf-8")
    check("صفحهٔ اصلی: جست‌وجو و فهرست", 'id="q"' in idx and "p/a1.html" in idx and "عضویت در کانال" in idx)
    sm = (out / "sitemap.xml").read_text(encoding="utf-8")
    check("sitemap: همهٔ صفحه‌ها و آدرس کامل", "https://u.github.io/repo/p/a1.html" in sm and "https://u.github.io/repo/</loc>" in sm)
    check("robots: sitemap", "Sitemap: https://u.github.io/repo/sitemap.xml" in (out / "robots.txt").read_text())
    sj = json.loads((out / "search.json").read_text(encoding="utf-8"))
    check("search.json: فیلدهای لازم", len(sj) == 4 and all({"s", "h", "t", "q", "d", "c"} <= set(x) for x in sj))
    check("search.json: یکسان‌سازی ی/ک", build.norm_search("كتاب ي") == "کتاب ی")

print("Pagination & empty:")
many = [{"id": f"i{k}", "title": f"خبر {k}", "published_at": f"2026-11-{(k % 28) + 1:02d}T10:00:00Z"} for k in range(120)]
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "d"
    build.build(build.prepare_items(many), out)
    check("۱۲۰ خبر = ۳ صفحهٔ آرشیو", all((out / "archive" / f"{k}.html").exists() for k in (1, 2, 3)) and not (out / "archive" / "4.html").exists())
    check("صفحهٔ اول لینک بعدی دارد", "2.html" in (out / "archive" / "1.html").read_text(encoding="utf-8"))
    check("صفحهٔ اصلی لینک آرشیو کامل دارد", "archive/1.html" in (out / "index.html").read_text(encoding="utf-8"))
    check("بدون base: sitemap ساخته نمی‌شود ولی robots هست", not (out / "sitemap.xml").exists() and (out / "robots.txt").exists())
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "e"
    build.build([], out)
    check("بدون خبر: صفحهٔ اصلی پیام می‌دهد و خطا نمی‌دهد", "هنوز خبری منتشر نشده" in (out / "index.html").read_text(encoding="utf-8"))

print("Fetch:")
os.environ.pop("SITE_API_URL", None)
check("بدون SITE_API_URL کد خروج ۲", build.main() == 2)

print(f"\n{passed} تست سایت با موفقیت گذشت ✅")
