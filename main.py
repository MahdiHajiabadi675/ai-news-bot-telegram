#!/usr/bin/env python3
"""
AI News Bot — Telegram channel for AI model news
Free tier: GitHub Actions + Cloudflare Worker/KV + Telegram Bot API
(ترجمه با src/translator.py؛ Gemini فعلاً کنار گذاشته شده)
"""
from src import clock
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# --dry-run باید قبل از import ماژول‌های src اعمال شود، چون config یک‌بار در import خوانده می‌شود.
if "--dry-run" in sys.argv:
    os.environ["DRY_RUN"] = "1"

sys.path.insert(0, str(Path(__file__).parent))

from src import storage
from src.storage import set_stage
from src.config import (
    DRY_RUN, COLLECT_INTERVAL_MIN, FAST_TRACK_ENABLED,
    HN_ENABLED, HN_MAX_ITEMS,
    AUTO_PUBLISH_ENABLED, AUTO_REJECT_THRESHOLD, AUTO_TRUST_THRESHOLD, HOT_OFFICIAL_MIN,
    MAX_ARTICLES_PER_RUN, MAX_REVIEWS_PER_RUN,
    TRUST_SCORES, REJECT_KEYWORDS, CATEGORY_WEIGHTS, ADMIN_ID,
    SCORE_SOURCE_WEIGHT, SCORE_IMPORTANCE_WEIGHT, SCORE_CONFIDENCE_WEIGHT, SCORE_CATEGORY_WEIGHT,
    VIEWS_ENABLED
)
from src.collectors import collect, FEEDS, interleave_by_source
from src.analyzer import analyze_articles, localize
from src import source_trust
from src import cluster
from src import hn
from src import topics as topic_prefs
from src.fast_track import process_fast_track
from src.bot import poll_updates, _send_review, notify_admin_new, send_message
from src.publisher import publish_approved
from src import edu_post
from src import digest
from src import arxiv_daily
from src import brief
from src import views
from src import weekly
from src import storylines
from src import backup
from src import translator
from src import modhealth
from src import cloudflare_kv
from src.config import TRANSLATE_RETRY_MAX


def calculate_score(item: dict) -> int:
    """محاسبه اسکور خبر (0-100)"""
    # اعتبار پویای منبع (در analyze_articles محاسبه می‌شود)؛ بدون آن، پیش‌فرض اولیه
    trust = item.get("source_trust")
    if trust is None:
        trust = TRUST_SCORES.get(item["source"], 3)
    source_score = (trust / 5) * SCORE_SOURCE_WEIGHT

    importance_map = {"high": 1.0, "medium": 0.6, "low": 0.3}
    importance_score = importance_map.get(item["importance"], 0.5) * SCORE_IMPORTANCE_WEIGHT

    confidence_map = {"confirmed": 1.0, "likely": 0.6, "rumor": 0.2}
    confidence_score = confidence_map.get(item["confidence"], 0.5) * SCORE_CONFIDENCE_WEIGHT

    category_weight = CATEGORY_WEIGHTS.get(item["category"], 0.3)
    category_score = category_weight * SCORE_CATEGORY_WEIGHT

    total = int(source_score + importance_score + confidence_score + category_score)
    # موضوعات مورد علاقهٔ ادمین: اولویت، نه فیلتر (بقیهٔ خبرها هم می‌آیند)
    if item.get("topic_hit"):
        total += topic_prefs.SCORE_BONUS
    return min(total, 100)


def _should_collect(force, last_collect, now=None, interval_min=None):
    """آیا الان باید اخبار را جمع‌آوری کرد؟"""
    now = now or clock.utcnow()
    interval_min = COLLECT_INTERVAL_MIN if interval_min is None else interval_min

    if force:
        return True
    if not last_collect:
        return True

    try:
        last_dt = datetime.fromisoformat(str(last_collect).replace("Z", ""))
        elapsed_min = (now - last_dt).total_seconds() / 60
        # elapsed منفی (ساعت ناهماهنگ) → جمع‌آوری کن
        return elapsed_min < 0 or elapsed_min >= interval_min
    except Exception as e:
        print(f"[WARN] Bad last_collect_time ({e}) — collecting anyway")
        return True


def _safe(name, fn, *args, default=None, **kwargs):
    """اجرای یک ماژول اختیاری؛ خرابی‌اش pipeline (و انتشار خبر) را نمی‌خواباند.

    KVError عمداً عبور می‌کند: شکست نوشتن در KV یعنی خطر پست تکراری و اجرا باید متوقف شود.
    نتیجه در modhealth ثبت می‌شود و در /health و /dashboard دیده می‌شود.
    """
    try:
        out = fn(*args, **kwargs)
    except storage.KVError:
        raise
    except Exception as e:  # noqa: BLE001
        modhealth.fail(name, e)
        return default
    modhealth.ok(name)
    return out


def run(force=False):
    """نقطهٔ ورود پایپ‌لاین.

    اگر نوشتن در Cloudflare KV شکست بخورد، اجرا با کد خطا متوقف می‌شود؛ ادامه دادن
    یعنی خبر در تلگرام منتشر شود ولی ثبت نشود و اجرای بعدی دوباره آن را بفرستد.
    """
    try:
        _run(force=force)
    except storage.KVError as e:
        print(f"[FATAL] {e}")
        print("[FATAL] Pipeline aborted to avoid duplicate channel posts.")
        sys.exit(1)


def _run(force=False):
    print(f"[{clock.utcnow().isoformat()}] Pipeline started")
    if DRY_RUN:
        print("[DRY-RUN] هیچ پیامی ارسال نمی‌شود و هیچ چیزی در KV نوشته نمی‌شود؛ فقط پیش‌نمایش.")
    translator.reset()
    modhealth.reset()
    # آمار «قیف» همین اجرا برای /runs: چند خبر پیدا شد و کجا کم شد
    rl = {"t": clock.utcnow().isoformat() + "Z", "forced": bool(force), "collected": False}

    # 0. KV باید قابل نوشتن باشد، وگرنه هیچ چیز ثبت نمی‌شود ولی تلگرام پست می‌گیرد
    storage.ensure_writable()

    # 1. Load state
    state = storage.load_state()
    approved_to_publish = None

    # هر راند اجرا بودجه انتشار مستقل دارد (سقف MAX_POSTS_PER_RUN)
    # شمارنده در ابتدای هر اجرا صفر می‌شود تا هر اجرا تا ۱۵ پست برود
    state["daily_post_count"] = 0
    state["daily_post_date"] = clock.utcnow().date().isoformat()
    storage.save_state(state)

    # 2. Poll bot updates (admin actions)
    state, updates = poll_updates(state)
    if updates:
        print(f"[INFO] Processed {len(updates)} bot updates")

    # 2.5 بازدید پست‌ها (رایگان، از پیش‌نمایش عمومی کانال) — قبل از هر نوشتن دیگری
    #     تا احتمال تداخل با انتشار اخبار کمتر شود. هرگز اجرا را متوقف نمی‌کند.
    if VIEWS_ENABLED:
        _safe("views", views.sync)

    # 2.6 بستن نظرسنجی هفتهٔ قبل و ثبت برنده (قبل از امتیازدهی همین اجرا)
    _safe("weekly_refresh", weekly.refresh)

    # 3. Collect new articles (if interval passed or forced)
    now = clock.utcnow()
    last_collect = state.get("last_collect_time")
    should_collect = _should_collect(force, last_collect, now)

    if should_collect:
        print("[INFO] Collection: yes")
    else:
        print(f"[INFO] Collection: skipped (last: {last_collect})")
        rl["skipped"] = True

    if should_collect and not state.get("paused"):
        state = set_stage(state, "collecting")
        storage.save_state(state)
        print("[INFO] Collecting articles...")
        seen = storage.load_seen()
        articles = collect(seen)
        _health = getattr(collect, "health", None) or {}
        rl.update(collected=True, feeds_ok=sum(1 for h in _health.values() if h.get("ok")),
                  feeds_total=len(_health), fetched=len(articles))

        # سلامت فیدها: ذخیره در KV (برای /feeds) + هشدار برای فیدی که چند بار پیاپی خراب است
        health = getattr(collect, "health", None)
        if health:
            try:
                for name in storage.update_feed_health(health):
                    if ADMIN_ID:
                        err = health[name].get("err") or "بدون آیتم"
                        send_message(ADMIN_ID, f"⚠️ فید «{name}» {storage.FEED_FAIL_ALERT_AT} بار پیاپی خراب بوده:\n{err}\n\nوضعیت همهٔ فیدها: /feeds")
            except Exception as e:
                print(f"[WARN] feed health failed: {e}")

        # سقف پردازش در هر اجرا (جلوگیری از تایم‌اوت و اسپم تلگرام).
        # اول نوبتی بین منابع مرتب می‌شود تا منابع آخر لیست (فارسی‌ها) حذف نشوند.
        articles = interleave_by_source(articles)
        if len(articles) > MAX_ARTICLES_PER_RUN:
            print(f"[INFO] Limiting to {MAX_ARTICLES_PER_RUN} of {len(articles)} new articles")
            articles = articles[:MAX_ARTICLES_PER_RUN]

        rl["processed"] = len(articles)
        if articles:
            collected_articles = articles
            articles = auto_reject(articles)
            rl["rejected_keyword"] = len(collected_articles) - len(articles)

            # Rejected items are final; do not inspect them on every run.
            for article in collected_articles:
                if article not in articles:
                    news_id = article.get("id", article.get("hash", ""))
                    if news_id:
                        seen.add(news_id)
            storage.save_seen(seen)

            if FAST_TRACK_ENABLED:
                fast_items = [a for a in articles if a.get("fast_track")]
                if fast_items:
                    print("[INFO] Processing fast-track...")
                    published_fast = process_fast_track(fast_items)
                    # fast_track persists the daily counter independently.
                    # Refresh it before the regular publisher enforces the limit.
                    fast_state = storage.load_state()
                    state["daily_post_date"] = fast_state.get("daily_post_date")
                    state["daily_post_count"] = fast_state.get("daily_post_count", 0)
                    for item in published_fast:
                        news_id = item.get("id", item.get("hash", ""))
                        if news_id:
                            seen.add(news_id)
                    storage.save_seen(seen)
                    articles = [a for a in articles if not a.get("fast_track")]

            if articles:
                # مرحلهٔ «analyzing» فقط در حافظه؛ نوشتن جداگانه در KV نمی‌ارزید (سقف روزانهٔ نوشتن)
                state = set_stage(state, "analyzing")
                print("[INFO] Analyzing (local classifier + dynamic source trust)...")

                # اعتبار پویای منابع (از تصمیم‌های ادمین) + تایید چندمنبعی
                stats = storage.load_source_stats()
                if source_trust.sync_priors(stats, [f["name"] for f in FEEDS]):
                    storage.save_source_stats(stats)
                analyzed = analyze_articles(articles, stats)
                rl["analyzed"] = len(analyzed)
                rl.update(low_score=0, auto=0, pending=0)

                # همان رویداد از چند منبع → یک پست با «همچنین در: ...»
                before_cluster = len(analyzed)
                analyzed = cluster.merge_similar(analyzed)
                rl["merged"] = before_cluster - len(analyzed)
                if rl["merged"]:
                    print(f"[INFO] Merged {rl['merged']} duplicate stories")

                # 🧵 پیش‌زمینهٔ موضوعی: لینک پست‌های قبلی همان موجودیت (OpenAI، Gemini...)
                try:
                    rl["story_links"] = storylines.enrich(analyzed)
                except Exception as e:  # noqa: BLE001
                    modhealth.fail("storylines", e)

                # سیگنال Hacker News: اگر خبر واقعاً در HN داغ است، به جذابیت اضافه کن
                if HN_ENABLED:
                    analyzed = hn.enrich(analyzed, limit=HN_MAX_ITEMS)

                # موضوعات مورد علاقهٔ ادمین: اولویت، نه فیلتر
                topic_list = topic_prefs.load(state)
                if topic_list:
                    rl["topic_hits"] = topic_prefs.boost(analyzed, topic_list)

                # بازخورد بازدید: دسته‌هایی که واقعاً خوانده شده‌اند کمی اولویت می‌گیرند
                if VIEWS_ENABLED:
                    try:
                        rl["views_boost"] = views.boost(analyzed, storage.load_published())
                    except Exception as e:  # noqa: BLE001
                        modhealth.fail("views_boost", e)

                # 🗳 نتیجهٔ نظرسنجی هفتگی: دستهٔ برنده تا چند روز اولویت می‌گیرد
                try:
                    rl["poll_boost"] = weekly.boost(analyzed)
                except Exception as e:  # noqa: BLE001
                    modhealth.fail("weekly_boost", e)

                # بعد از تحلیل، خبر دیده‌شده محسوب می‌شود تا دوباره پردازش نشود.
                for article in articles:
                    news_id = article.get("id", article.get("hash", ""))
                    if news_id:
                        seen.add(news_id)
                storage.save_seen(seen)

                pending = storage.load_pending()
                approved = storage.load_approved()
                reviews_sent = 0
                notices_sent = 0
                pending_this_run = 0
                translated_ok = translated_fail = 0

                rejected_low = []  # یک‌جا ذخیره می‌شود (قبلاً برای هر خبر ردشده یک خواندن + یک نوشتن لیست ۱۵۰۰تایی)

                for item in analyzed:
                    item["score"] = calculate_score(item)

                    if item["score"] < AUTO_REJECT_THRESHOLD:
                        rl["low_score"] += 1
                        print(f"[REJECT] Low score ({item['score']}): {item['title'][:50]}")
                        rejected_low.append({**item, "published": False, "rejected_at": _now(), "reject_reason": "low_score"})
                        continue

                    # ترجمه فقط برای خبرهای باقی‌مانده (همهٔ منابع)
                    localize(item)
                    if item.get("translated") is False:
                        translated_fail += 1
                    else:
                        translated_ok += 1

                    source_trust_now = item["source_trust"]
                    not_rumor = item["confidence"] != "rumor"

                    # انتشار خودکار: هم از config و هم از کلید /auto در ربات پیروی می‌کنه
                    auto_enabled = AUTO_PUBLISH_ENABLED and state.get("auto_publish_enabled", True)
                    # ۱) منبع رسمی (آزمایشگاه‌ها) با اعتبار پویای کافی، مگر قالب غیرخبری
                    by_official = (item.get("official") and source_trust_now >= AUTO_TRUST_THRESHOLD
                                   and item["hot"] >= HOT_OFFICIAL_MIN)
                    # ۲) رسانه/منبع عمومی: فقط اگر خود خبر «جذاب» باشد (src/newsworthiness.py)
                    by_hot = (not item.get("official")) and item["hot"] >= item["hot_threshold"]
                    should_auto = auto_enabled and not_rumor and (by_official or by_hot)
                    item["auto_reason"] = "official" if by_official else ("hot" if by_hot else "")
                    if item.get("translated") is False and TRANSLATE_RETRY_MAX > 0:
                        # یک راند در صف می‌ماند تا ترجمه دوباره تلاش شود
                        item["hold_translation"] = True

                    if should_auto:
                        rl["auto"] += 1
                        item["status"] = "approved"
                        item["admin_action"] = "auto_approved"
                        approved.append(item)
                        if notices_sent < MAX_REVIEWS_PER_RUN:
                            notify_admin_new(item)
                            notices_sent += 1
                        print(f"[AUTO] Approved: {item['title_fa'][:50]} (score: {item['score']}, trust: {source_trust_now}, via: {item['auto_reason']}, hot: {item['hot']})")
                    else:
                        rl["pending"] += 1
                        item["status"] = "pending"
                        pending.append(item)
                        pending_this_run += 1
                        if reviews_sent < MAX_REVIEWS_PER_RUN:
                            _send_review(item)
                            reviews_sent += 1
                        print(f"[PENDING] {item['title_fa'][:50]} (score: {item['score']}, trust: {source_trust_now}, hot: {item['hot']}/{item['hot_threshold']:.0f})")

                if rejected_low:
                    pub = storage.load_published()
                    pub.extend(rejected_low)
                    storage.save_published(pub)

                rl["untranslated"] = translated_fail
                if translated_fail:
                    print(f"[WARN] {translated_fail} item(s) could not be translated (published with 'ترجمه نشده' label)")
                    state["last_error"] = f"{translated_fail} خبر ترجمه نشد"
                else:
                    state["last_error"] = None
                print(f"[INFO] Translated: {translated_ok}, untranslated: {translated_fail}")

                hidden = pending_this_run - reviews_sent
                if hidden > 0 and ADMIN_ID:
                    send_message(
                        ADMIN_ID,
                        f"📋 {hidden} خبر دیگر در انتظار تایید است — /pending",
                        reply_markup={"inline_keyboard": [[{"text": "🗑 پاک کردن کل صف", "callback_data": "clearq"}]]},
                    )

                storage.save_pending(pending)
                storage.save_approved(approved)
                approved_to_publish = approved
                print(f"[INFO] Routed: {len(analyzed)} items")

        state["last_collect_time"] = now.isoformat() + "Z"
        storage.save_state(state)

    # 4. پست آموزشی (فقط متن) — قبل از انتشار اخبار، تا اگر انتشار طول کشید یا
    #    اجرا به تایم‌اوت نزدیک شد، اسلات آموزشی از دست نرود.
    #    Gemini اول، در صورت خطا مدل‌های رایگان OpenRouter، در آخر متن آمادهٔ محلی.
    state = _safe("edu", edu_post.maybe_post, state, force=force, default=state)

    # 5. دایجست هفتگی (جمعه شب، حداکثر یک‌بار در هفته)
    state = _safe("digest", digest.maybe_post, state, default=state)

    # 6. مقالهٔ روز arXiv (روزی یک‌بار، از ساعت مقرر به بعد)
    state = _safe("arxiv", arxiv_daily.maybe_post, state, default=state)

    # 7. Publish approved items
    state = set_stage(state, "publishing")
    storage.save_state(state)
    state = publish_approved(state, approved_to_publish)

    # 8. صبحانهٔ AI — بعد از انتشار، تا تازه‌ترین خبرهای همین ساعت هم داخلش باشند
    state = _safe("brief", brief.maybe_post, state, default=state)

    # 9. جمع‌بندی و نظرسنجی هفتگی (شنبه شب، حداکثر یک‌بار در هفته)
    state = _safe("weekly", weekly.maybe_post, state, default=state)

    # 10. Save state
    state = set_stage(state, "waiting")
    storage.save_state(state)

    # آمار این اجرا برای /runs (اگر ذخیره نشد، پایپ‌لاین خراب نشود)
    try:
        rl["paused"] = bool(state.get("paused"))
        rl["published"] = state.get("daily_post_count", 0)
        rl["queue_pending"] = len(storage.load_pending())
        rl["queue_approved"] = len(storage.load_approved())
        rl["kv_put"], rl["kv_get"] = cloudflare_kv.STATS["put"], cloudflare_kv.STATS["get"]
        bad = [n for n, r in modhealth.current().items() if not r["ok"]]
        if bad:
            rl["mod_fail"] = bad
        storage.append_run_log(rl)
    except Exception as e:
        print(f"[WARN] run log failed: {e}")

    # زمان آخرین اجرا برای /health (شکستش اجرا را متوقف نمی‌کند)
    # 💾 پشتیبان هفتگی KV (اختیاری) — قبل از heartbeat تا وضعیتش هم ثبت شود
    _safe("backup", backup.maybe_send, state)

    try:
        alerts = storage.heartbeat({"stage": state.get("current_stage"),
                                    "published": state.get("daily_post_count", 0)})
        for name in alerts or []:
            err = (modhealth.current().get(name) or {}).get("err")
            if ADMIN_ID:
                send_message(ADMIN_ID, f"⚠️ ماژول «{name}» {modhealth.ALERT_AT} اجرای پیاپی خطا داده:\n{err}\n\nانتشار خبر ادامه دارد. وضعیت: /dashboard")
    except Exception as e:
        print(f"[WARN] heartbeat failed: {e}")
    print(f"[KV] requests this run: put={cloudflare_kv.STATS['put']} get={cloudflare_kv.STATS['get']}")

    print(f"[{clock.utcnow().isoformat()}] Pipeline finished")


_REJECT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(k) for k in REJECT_KEYWORDS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def auto_reject(articles: list) -> list:
    """رد خبرهای مشکوک — با مرز کلمه (تا «cryptography» به‌خاطر «crypto» رد نشود)"""
    kept = []
    for a in articles:
        if _REJECT_RE.search(a["title"]):
            print(f"[REJECT] Blocked keyword: {a['title'][:60]}")
            continue
        kept.append(a)
    return kept


def _now():
    return clock.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        force = "--force" in sys.argv
        run(force=force)
    else:
        print("Usage: python main.py run [--force] [--dry-run]")
