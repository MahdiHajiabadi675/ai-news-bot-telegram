#!/usr/bin/env python3
"""
AI News Bot — Telegram channel for AI model news
Free tier: GitHub Actions + Cloudflare Worker/KV + Telegram Bot API
(ترجمه با src/translator.py؛ Gemini فعلاً کنار گذاشته شده)
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import storage
from src.storage import set_stage
from src.config import (
    COLLECT_INTERVAL_MIN, FAST_TRACK_ENABLED,
    AUTO_PUBLISH_ENABLED, AUTO_PUBLISH_THRESHOLD, AUTO_REJECT_THRESHOLD, AUTO_TRUST_THRESHOLD,
    MAX_ARTICLES_PER_RUN, MAX_REVIEWS_PER_RUN,
    TRUST_SCORES, REJECT_KEYWORDS, CATEGORY_WEIGHTS, ADMIN_ID,
    SCORE_SOURCE_WEIGHT, SCORE_IMPORTANCE_WEIGHT, SCORE_CONFIDENCE_WEIGHT, SCORE_CATEGORY_WEIGHT
)
from src.collectors import collect, FEEDS, interleave_by_source
from src.analyzer import analyze_articles, localize
from src import source_trust
from src.fast_track import process_fast_track
from src.bot import poll_updates, _send_review, notify_admin_new, send_message
from src.publisher import publish_approved
from src import translator


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
    return min(total, 100)


def _should_collect(force, last_collect, now=None, interval_min=None):
    """آیا الان باید اخبار را جمع‌آوری کرد؟"""
    now = now or datetime.utcnow()
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


def run(force=False):
    print(f"[{datetime.utcnow().isoformat()}] Pipeline started")
    translator.reset()
    # آمار «قیف» همین اجرا برای /runs: چند خبر پیدا شد و کجا کم شد
    rl = {"t": datetime.utcnow().isoformat() + "Z", "forced": bool(force), "collected": False}

    # 1. Load state
    state = storage.load_state()
    approved_to_publish = None

    # هر راند اجرا بودجه انتشار مستقل دارد (سقف MAX_POSTS_PER_RUN)
    # شمارنده در ابتدای هر اجرا صفر می‌شود تا هر اجرا تا ۱۵ پست برود
    state["daily_post_count"] = 0
    state["daily_post_date"] = datetime.utcnow().date().isoformat()
    storage.save_state(state)

    # 2. Poll bot updates (admin actions)
    state, updates = poll_updates(state)
    if updates:
        print(f"[INFO] Processed {len(updates)} bot updates")

    # 3. Collect new articles (if interval passed or forced)
    now = datetime.utcnow()
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
                state = set_stage(state, "analyzing")
                storage.save_state(state)
                print("[INFO] Analyzing (local classifier + dynamic source trust)...")

                # اعتبار پویای منابع (از تصمیم‌های ادمین) + تایید چندمنبعی
                stats = storage.load_source_stats()
                if source_trust.sync_priors(stats, [f["name"] for f in FEEDS]):
                    storage.save_source_stats(stats)
                analyzed = analyze_articles(articles, stats)
                rl["analyzed"] = len(analyzed)
                rl.update(low_score=0, auto=0, pending=0)

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

                for item in analyzed:
                    item["score"] = calculate_score(item)

                    if item["score"] < AUTO_REJECT_THRESHOLD:
                        rl["low_score"] += 1
                        print(f"[REJECT] Low score ({item['score']}): {item['title'][:50]}")
                        pub = storage.load_published()
                        pub.append({**item, "published": False, "rejected_at": _now(), "reject_reason": "low_score"})
                        storage.save_published(pub)
                        continue

                    # ترجمه فقط برای خبرهای باقی‌مانده (همهٔ منابع)
                    localize(item)
                    if item.get("translated") is False:
                        translated_fail += 1
                    else:
                        translated_ok += 1

                    source_trust_now = item["source_trust"]
                    is_trusted = source_trust_now >= AUTO_TRUST_THRESHOLD
                    is_confirmed = item["confidence"] == "confirmed"
                    is_high_score = item["score"] >= AUTO_PUBLISH_THRESHOLD

                    # انتشار خودکار: هم از config و هم از کلید /auto در ربات پیروی می‌کنه
                    auto_enabled = AUTO_PUBLISH_ENABLED and state.get("auto_publish_enabled", True)
                    should_auto = auto_enabled and is_trusted and is_confirmed and is_high_score

                    if should_auto:
                        rl["auto"] += 1
                        item["status"] = "approved"
                        item["admin_action"] = "auto_approved"
                        approved.append(item)
                        if notices_sent < MAX_REVIEWS_PER_RUN:
                            notify_admin_new(item)
                            notices_sent += 1
                        print(f"[AUTO] Approved: {item['title_fa'][:50]} (score: {item['score']}, trust: {source_trust_now})")
                    else:
                        rl["pending"] += 1
                        item["status"] = "pending"
                        pending.append(item)
                        pending_this_run += 1
                        if reviews_sent < MAX_REVIEWS_PER_RUN:
                            _send_review(item)
                            reviews_sent += 1
                        print(f"[PENDING] {item['title_fa'][:50]} (score: {item['score']}, trust: {source_trust_now})")

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

    # 4. Publish approved items
    state = set_stage(state, "publishing")
    storage.save_state(state)
    state = publish_approved(state, approved_to_publish)

    # 5. Save state
    state = set_stage(state, "waiting")
    storage.save_state(state)

    # آمار این اجرا برای /runs (اگر ذخیره نشد، پایپ‌لاین خراب نشود)
    try:
        rl["paused"] = bool(state.get("paused"))
        rl["published"] = state.get("daily_post_count", 0)
        rl["queue_pending"] = len(storage.load_pending())
        rl["queue_approved"] = len(storage.load_approved())
        storage.append_run_log(rl)
    except Exception as e:
        print(f"[WARN] run log failed: {e}")

    print(f"[{datetime.utcnow().isoformat()}] Pipeline finished")


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
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        force = "--force" in sys.argv
        run(force=force)
    else:
        print("Usage: python main.py run [--force]")
