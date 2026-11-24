#!/usr/bin/env python3
"""ساخت وب‌سایت ایستای آرشیو اخبار (برای GitHub Pages).

ورودی: JSON عمومی Worker  →  <SITE_API_URL>/api/news?full=1&limit=300
خروجی: پوشهٔ dist/ شامل
  • index.html           آخرین خبرها + جست‌وجو (سمت کلاینت، بدون سرور)
  • p/<slug>.html        صفحهٔ هر خبر (برای گوگل: عنوان، توضیح، JSON-LD)
  • c/<دسته>.html        صفحهٔ هر دسته
  • archive/<n>.html     آرشیو صفحه‌بندی‌شده
  • search.json، sitemap.xml، robots.txt، 404.html

فقط کتابخانهٔ استاندارد پایتون؛ هیچ کلید و هزینه‌ای لازم نیست.
متغیرهای محیطی:
  SITE_API_URL      آدرس Worker (الزامی)، مثل https://ai-news.example.workers.dev
  SITE_BASE_URL     آدرس نهایی سایت (پیش‌فرض: https://<owner>.github.io/<repo> از GITHUB_REPOSITORY)
  SITE_TITLE        عنوان سایت
  SITE_CHANNEL_URL  لینک کانال تلگرام (پیش‌فرض: از فیلد channel پاسخ API)
"""
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAGE_SIZE = 50
HOME_COUNT = 40

CATEGORY_LABELS = {
    "model_release": "انتشار مدل جدید",
    "benchmark": "بنچمارک و ارزیابی",
    "funding": "سرمایه‌گذاری و بازار",
    "research": "پژوهش و مقاله",
    "policy": "قانون و مقررات AI",
    "tool_release": "ابزار و محصول",
    "industry": "صنعت و شرکت‌ها",
    "other": "موضوع‌های عمومی",
    "edu": "آموزش",
    "digest": "خلاصه",
    "paper": "مقالهٔ روز",
    "brief": "صبحانهٔ هوش مصنوعی",
    "weekly": "جمع‌بندی هفته",
}
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


# ───────────────────────── کمکی‌ها ─────────────────────────

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def safe_url(u) -> str:
    u = str(u or "").strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else ""


def faNum(n) -> str:
    return str(n).translate(FA_DIGITS)


def clip(s, n) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def to_jalali(gy, gm, gd):
    """تبدیل میلادی به جلالی (الگوریتم استاندارد jalaali)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def parse_dt(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def fa_date(s) -> str:
    d = parse_dt(s)
    if not d:
        return ""
    jy, jm, jd = to_jalali(d.year, d.month, d.day)
    return faNum(f"{jd} {JALALI_MONTHS[jm - 1]} {jy}")


def norm_search(s) -> str:
    """همان یکسان‌سازی Worker (/ask) و جست‌وجوی سمت کلاینت."""
    s = str(s or "").lower()
    s = re.sub("[يى]", "ی", s).replace("ك", "ک")
    s = re.sub("[‌‏‎]", " ", s)
    s = re.sub("[ً-ٰٟ]", "", s)
    for i, d in enumerate("۰۱۲۳۴۵۶۷۸۹"):
        s = s.replace(d, str(i))
    for i, d in enumerate("٠١٢٣٤٥٦٧٨٩"):
        s = s.replace(d, str(i))
    return s


def cat_label(c) -> str:
    return CATEGORY_LABELS.get(str(c or ""), "") or ""


# ───────────────────────── داده ─────────────────────────

def fetch_news(api_url, limit=300, retries=3):
    url = api_url.rstrip("/") + f"/api/news?full=1&limit={limit}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-news-site-builder"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed: {last}")


def prepare_items(raw_items):
    """تمیزسازی، حذف بدون‌عنوان و ساخت slug یکتا و پایدار."""
    items, used = [], set()
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        title = clip(it.get("title"), 200)
        if not title:
            continue
        base = re.sub(r"[^A-Za-z0-9_-]", "", str(it.get("id") or ""))[:40]
        if not base:
            base = hashlib.sha1((str(it.get("link") or "") + title).encode("utf-8")).hexdigest()[:12]
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        items.append({
            "slug": slug, "title": title,
            "summary": clip(it.get("summary"), 600),
            "source": clip(it.get("source"), 60),
            "link": safe_url(it.get("link")),
            "category": re.sub(r"[^a-z_]", "", str(it.get("category") or "other")) or "other",
            "published_at": it.get("published_at") or "",
            "tags": [re.sub(r"[^\w]", "", str(t))[:30] for t in (it.get("tags") or [])[:6] if t],
            "message_id": it.get("message_id") if isinstance(it.get("message_id"), int) else None,
        })
    items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return items


# ───────────────────────── قالب ─────────────────────────

CSS = """
:root{--bg:#fafafa;--fg:#1b1f24;--muted:#5b6672;--card:#fff;--line:#e4e8ec;--accent:#0a66c2;--chip:#eef3f8}
@media (prefers-color-scheme:dark){:root{--bg:#0f1317;--fg:#e8edf2;--muted:#9aa7b4;--card:#171d23;--line:#27313a;--accent:#6cb4ff;--chip:#1e2730}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.9 Vazirmatn,Tahoma,"Segoe UI",system-ui,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:760px;margin:0 auto;padding:0 16px}
header.top{border-bottom:1px solid var(--line);background:var(--card)}
header.top .wrap{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 16px;flex-wrap:wrap}
.brand{font-weight:700;font-size:1.1rem;color:var(--fg)}
.btn{display:inline-block;padding:6px 14px;border-radius:999px;background:var(--accent);color:#fff!important;font-size:.9rem}
main{padding:20px 0 40px}h1{font-size:1.5rem;line-height:1.5;margin:.2em 0 .6em}
.search{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg);font:inherit}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.chip{padding:3px 12px;border-radius:999px;background:var(--chip);color:var(--fg);font-size:.85rem}
.chip.on{background:var(--accent);color:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:12px 0}
.card h2{font-size:1.05rem;margin:0 0 4px;line-height:1.7}.card p{margin:.3em 0;color:var(--muted);font-size:.92rem}
.meta{color:var(--muted);font-size:.82rem}
article.post .summary{font-size:1.05rem;white-space:pre-line}
.tags{margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;color:var(--muted);font-size:.85rem}
.pager{display:flex;gap:10px;justify-content:center;margin-top:24px;flex-wrap:wrap}
footer{border-top:1px solid var(--line);padding:18px 0;color:var(--muted);font-size:.85rem;text-align:center}
.empty{text-align:center;color:var(--muted);padding:40px 0}
"""


class Site:
    def __init__(self, base_url, title, channel_url):
        self.base = base_url.rstrip("/")
        self.title = title
        self.channel = safe_url(channel_url)

    def url(self, path):
        return f"{self.base}/{path.lstrip('/')}" if self.base else "/" + path.lstrip("/")

    def rel(self, depth, path):
        return ("../" * depth) + path

    def layout(self, *, title, desc, path, body, depth=0, extra_head="", canonical=True):
        full_title = title if title == self.title else f"{title} | {self.title}"
        chan = f'<a class="btn" href="{esc(self.channel)}" rel="noopener">عضویت در کانال</a>' if self.channel else ""
        canon = f'<link rel="canonical" href="{esc(self.url(path))}">' if canonical and self.base else ""
        og = (f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">'
              f'<meta property="og:type" content="website"><meta property="og:locale" content="fa_IR">')
        return (
            '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(full_title)}</title><meta name=\"description\" content=\"{esc(clip(desc, 160))}\">"
            f"{canon}{og}{extra_head}<style>{CSS}</style></head><body>"
            f'<header class="top"><div class="wrap"><a class="brand" href="{self.rel(depth, "index.html")}">🤖 {esc(self.title)}</a>{chan}</div></header>'
            f'<main><div class="wrap">{body}</div></main>'
            f'<footer><div class="wrap">آرشیو خودکار اخبار هوش مصنوعی · منبع هر خبر در صفحهٔ آن آمده است</div></footer>'
            "</body></html>"
        )

    def card(self, it, depth):
        label = cat_label(it["category"])
        meta = " · ".join(x for x in [fa_date(it["published_at"]), esc(it["source"]), esc(label)] if x)
        summ = f"<p>{esc(clip(it['summary'], 180))}</p>" if it["summary"] else ""
        return (f'<div class="card"><h2><a href="{self.rel(depth, "p/" + it["slug"] + ".html")}">{esc(it["title"])}</a></h2>'
                f'{summ}<div class="meta">{meta}</div></div>')

    def chips(self, items, depth, active=None):
        counts = {}
        for it in items:
            counts[it["category"]] = counts.get(it["category"], 0) + 1
        out = [f'<a class="chip{" on" if active is None else ""}" href="{self.rel(depth, "index.html")}">همه</a>']
        for c, n in sorted(counts.items(), key=lambda x: -x[1]):
            lab = cat_label(c)
            if lab:
                out.append(f'<a class="chip{" on" if c == active else ""}" href="{self.rel(depth, "c/" + c + ".html")}">{esc(lab)} ({faNum(n)})</a>')
        return '<div class="chips">' + "".join(out) + "</div>"

    # ── صفحه‌ها ──
    def index(self, items):
        shown = items[:HOME_COUNT]
        more = ""
        if len(items) > HOME_COUNT:
            more = f'<div class="pager"><a class="btn" href="archive/1.html">آرشیو کامل ({faNum(len(items))} خبر)</a></div>'
        listing = "".join(self.card(i, 0) for i in shown) or '<div class="empty">هنوز خبری منتشر نشده است.</div>'
        body = (f"<h1>{esc(self.title)}</h1>"
                '<input id="q" class="search" type="search" placeholder="جست‌وجو در اخبار… (مثلاً OpenAI یا مدل متن‌باز)" autocomplete="off">'
                f'{self.chips(items, 0)}<div id="results" hidden></div><div id="list">{listing}{more}</div>'
                "<script>" + SEARCH_JS + "</script>")
        return self.layout(title=self.title, desc="آرشیو و جست‌وجوی اخبار هوش مصنوعی به زبان فارسی", path="index.html", body=body)

    def category(self, cat, items, all_items):
        lab = cat_label(cat) or cat
        listing = "".join(self.card(i, 1) for i in items[:200])
        body = f"<h1>{esc(lab)}</h1>{self.chips(all_items, 1, cat)}{listing}"
        return self.layout(title=lab, desc=f"اخبار دستهٔ «{lab}» در حوزهٔ هوش مصنوعی", path=f"c/{cat}.html", body=body, depth=1)

    def archive(self, n, total_pages, page_items):
        nav = []
        if n > 1:
            nav.append(f'<a class="btn" href="{n - 1}.html">صفحهٔ قبل</a>')
        if n < total_pages:
            nav.append(f'<a class="btn" href="{n + 1}.html">صفحهٔ بعد</a>')
        body = (f"<h1>آرشیو اخبار — صفحهٔ {faNum(n)}</h1>" + "".join(self.card(i, 1) for i in page_items)
                + f'<div class="pager">{"".join(nav)}</div>')
        return self.layout(title=f"آرشیو اخبار — صفحهٔ {faNum(n)}", desc="آرشیو کامل اخبار هوش مصنوعی", path=f"archive/{n}.html", body=body, depth=1)

    def post(self, it, related):
        label = cat_label(it["category"])
        desc = it["summary"] or it["title"]
        meta = " · ".join(x for x in [fa_date(it["published_at"]), esc(label)] if x)
        actions = []
        if it["link"]:
            host = re.sub(r"^https?://(www\.)?", "", it["link"]).split("/")[0]
            actions.append(f'<a class="btn" href="{esc(it["link"])}" rel="noopener nofollow" target="_blank">منبع اصلی: {esc(it["source"] or host)}</a>')
        if self.channel and it["message_id"]:
            actions.append(f'<a class="chip" href="{esc(self.channel.rstrip("/"))}/{it["message_id"]}" rel="noopener">دیدن در کانال</a>')
        tags = ""
        if it["tags"]:
            tags = '<div class="tags">' + "".join(f"<span>#{esc(t)}</span>" for t in it["tags"]) + "</div>"
        rel_html = ""
        if related:
            rel_html = "<h3>خبرهای مرتبط</h3>" + "".join(self.card(r, 1) for r in related)
        body = (f'<article class="post"><h1>{esc(it["title"])}</h1><div class="meta">{meta}</div>'
                f'<p class="summary">{esc(it["summary"])}</p><p style="display:flex;gap:10px;flex-wrap:wrap">{"".join(actions)}</p>{tags}</article>{rel_html}')
        ld = {
            "@context": "https://schema.org", "@type": "NewsArticle", "headline": it["title"], "inLanguage": "fa",
            "description": clip(desc, 200), "datePublished": it["published_at"] or None,
            "mainEntityOfPage": self.url(f"p/{it['slug']}.html") if self.base else None,
        }
        ld = {k: v for k, v in ld.items() if v}
        extra = '<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>"
        return self.layout(title=it["title"], desc=desc, path=f"p/{it['slug']}.html", body=body, depth=1, extra_head=extra)

    def not_found(self):
        return self.layout(title="صفحه پیدا نشد", desc="این صفحه وجود ندارد", path="404.html", canonical=False,
                           body=f'<div class="empty"><h1>۴۰۴</h1><p>این صفحه پیدا نشد. <a href="{esc(self.url("index.html"))}">بازگشت به صفحهٔ اصلی</a></p></div>')


# جست‌وجوی سمت کلاینت: همان یکسان‌سازی فارسی؛ نتیجه فقط از search.json (بدون ارسال متن جایی)
SEARCH_JS = r"""
(function(){
var q=document.getElementById('q'),res=document.getElementById('results'),list=document.getElementById('list'),data=null,busy=false;
function norm(s){return String(s||'').toLowerCase().replace(/[يى]/g,'ی').replace(/ك/g,'ک').replace(/[‌‏‎]/g,' ').replace(/[ً-ٰٟ]/g,'').replace(/[۰-۹]/g,function(d){return '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)}).replace(/[٠-٩]/g,function(d){return '٠١٢٣٤٥٦٧٨٩'.indexOf(d)})}
function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function run(){var t=norm(q.value).split(/[^\p{L}\p{N}]+/u).filter(function(x){return x.length>1||/\d/.test(x)});
 if(!t.length){res.hidden=true;list.hidden=false;return}
 var hits=[];data.forEach(function(d,i){var f=0,sc=0;t.forEach(function(w){var s=(d.q.indexOf(w)>=0?1:0)+(d.t.indexOf(w)>=0?2:0);if(s){f++;sc+=s}});if(f>=Math.ceil(t.length/2))hits.push([sc,i])});
 hits.sort(function(a,b){return b[0]-a[0]||a[1]-b[1]});
 list.hidden=true;res.hidden=false;
 res.innerHTML=hits.length?hits.slice(0,40).map(function(h){var d=data[h[1]];return '<div class="card"><h2><a href="p/'+esc(d.s)+'.html">'+esc(d.h)+'</a></h2><div class="meta">'+esc(d.d)+(d.c?' · '+esc(d.c):'')+'</div></div>'}).join(''):'<div class="empty">چیزی پیدا نشد.</div>'}
q.addEventListener('input',function(){if(data){run();return}if(busy)return;busy=true;
 fetch('search.json').then(function(r){return r.json()}).then(function(j){data=j;run()}).catch(function(){res.hidden=false;res.innerHTML='<div class="empty">جست‌وجو در دسترس نیست.</div>'})});
})();
"""


# ───────────────────────── ساخت ─────────────────────────

def build(items, out: Path, base_url="", title="اخبار هوش مصنوعی", channel_url=""):
    site = Site(base_url, title, channel_url)
    if out.exists():
        shutil.rmtree(out)
    for d in ("p", "c", "archive"):
        (out / d).mkdir(parents=True)

    def write(rel, text):
        (out / rel).write_text(text, encoding="utf-8")

    write("index.html", site.index(items))
    write("404.html", site.not_found())
    write(".nojekyll", "")

    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    for cat, lst in by_cat.items():
        if cat_label(cat):
            write(f"c/{cat}.html", site.category(cat, lst, items))

    pages = max(1, -(-len(items) // PAGE_SIZE))
    for n in range(1, pages + 1):
        write(f"archive/{n}.html", site.archive(n, pages, items[(n - 1) * PAGE_SIZE: n * PAGE_SIZE]))

    for it in items:
        related = [r for r in by_cat.get(it["category"], []) if r["slug"] != it["slug"]][:4]
        write(f"p/{it['slug']}.html", site.post(it, related))

    search = [{"s": i["slug"], "h": i["title"], "t": norm_search(i["title"]), "q": norm_search(" ".join([i["summary"], i["source"], " ".join(i["tags"]), cat_label(i["category"])])),
               "d": fa_date(i["published_at"]), "c": cat_label(i["category"])} for i in items]
    write("search.json", json.dumps(search, ensure_ascii=False, separators=(",", ":")))

    urls = ["index.html"] + [f"c/{c}.html" for c in by_cat if cat_label(c)] + [f"archive/{n}.html" for n in range(1, pages + 1)] + [f"p/{i['slug']}.html" for i in items]
    if site.base:
        sm = ['<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        by_slug = {f"p/{i['slug']}.html": i["published_at"] for i in items}
        for u in urls:
            lm = (parse_dt(by_slug.get(u)) or None)
            sm.append(f"<url><loc>{esc(site.url(u if u != 'index.html' else ''))}</loc>" + (f"<lastmod>{lm.date().isoformat()}</lastmod>" if lm else "") + "</url>")
        sm.append("</urlset>")
        write("sitemap.xml", "".join(sm))
        write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site.url('sitemap.xml')}\n")
    else:
        write("robots.txt", "User-agent: *\nAllow: /\n")
    return len(items)


def main():
    api = os.environ.get("SITE_API_URL", "").strip()
    if not api:
        print("SITE_API_URL تنظیم نشده (آدرس Worker). در GitHub: Settings → Variables → Actions.", file=sys.stderr)
        return 2
    data = fetch_news(api)
    items = prepare_items(data.get("items"))
    base = os.environ.get("SITE_BASE_URL", "").strip()
    if not base and os.environ.get("GITHUB_REPOSITORY"):
        owner, repo = os.environ["GITHUB_REPOSITORY"].split("/", 1)
        base = f"https://{owner.lower()}.github.io/{repo}"
    channel = os.environ.get("SITE_CHANNEL_URL", "").strip()
    if not channel and data.get("channel"):
        channel = f"https://t.me/{data['channel']}"
    out = Path(os.environ.get("SITE_OUT", "dist"))
    n = build(items, out, base, os.environ.get("SITE_TITLE", "").strip() or "اخبار هوش مصنوعی", channel)
    print(f"[SITE] built {n} posts → {out}")
    if n == 0:
        print("[SITE] هشدار: هیچ خبری از API نیامد", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
