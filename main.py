#!/usr/bin/env python3
"""
AI News Bot — Telegram channel for AI model news
Free tier: GitHub Actions + Gemini API + Telegram Bot API
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import storage
from src.storage import set_stage
from src.config import (
    COLLECT_INTERVAL_MIN, FAST_TRACK_ENABLED,
    AUTO_PUBLISH_ENABLED, AUTO_PUBLISH_THRESHOLD, AUTO_REJECT_THRESHOLD,
    TRUST_SCORES, REJECT_KEYWORDS, CATEGORY_WEIGHTS, ADMIN_ID,
    SCORE_SOURCE_WEIGHT, SCORE_IMPORTANCE_WEIGHT, SCORE_CONFIDENCE_WEIGHT, SCORE_CATEGORY_WEIGHT
)
from src.collectors import collect
from src.analyzer import analyze_articles
from src.fast_track import process_fast_track
from src.bot import poll_updates, _send_review, notify_admin_new, send_message
from src.publisher import publish_approved


def calculate_score(item: dict) -> int:
    """محاسبه اسکور خبر (0-100)"""
    source_trust = TRUST_SCORES.get(item["source"], 3)
    source_score = (source_trust / 5) * SCORE_SOURCE_WEIGHT

    importance_map = {"high": 1.0, "medium": 0.6, "low": 0.3}
    importance_score = importance_map.get(item["importance"], 0.5) * SCORE_IMPORTANCE_WEIGHT

    confidence_map = {"confirmed": 1.0, "likely": 0.6, "rumor": 0.2}
    confidence_score = confidence_map.get(item["confidence"], 0.5) * SCORE_CONFIDENCE_WEIGHT

    category_weight = CATEGORY_WEIGHTS.get(item["category"], 0.3)
    category_score = category_weight * SCORE_CATEGORY_WEIGHT

    total = int(source_score + importance_score + confidence_score + category_score)
    return min(total, 100)


def run(force=False):
    print(f"[{datetime.utcnow().isoformat()}] Pipeline started")

    # 1. Load state
    state = storage.load_state()
    approved_to_publish = None

    # 2. Poll bot updates (admin actions)
    state, updates = poll_updates(state)
    if updates:
        print(f"[INFO] Processed {len(updates)} bot updates")

    # 3. Collect new articles (if interval passed or forced)
    now = datetime.utcnow()
    last_collect = state.get("last_collect_time")
    should_collect = force
    if not force and last_collect:
        last_dt = datetime.fromisoformat(last_collect.replace("Z", ""))
        if (now - last_dt).total_seconds() < COLLECT_INTERVAL_MIN * 60:
            should_collect = False

    if should_collect and not state.get("paused"):
        state = set_stage(state, "collecting")
        storage.save_state(state)
        print("[INFO] Collecting articles...")
        seen = storage.load_seen()
        articles = collect(seen)

        if articles:
            articles = auto_reject(articles)

            if FAST_TRACK_ENABLED:
                print("[INFO] Processing fast-track...")
                fast_items = [a for a in articles if a.get("fast_track")]
                process_fast_track(fast_items)
                articles = [a for a in articles if not a.get("fast_track")]

            if articles:
                state = set_stage(state, "analyzing")
                storage.save_state(state)
                print("[INFO] Analyzing with Gemini...")
                analyzed = analyze_articles(articles)

                # تشخیص واقعی شکست Gemini (فقط یک بار در شروع هر قطعی اطلاع بده)
                used_fallback = bool(analyzed) and all(a.get("fallback") for a in analyzed)
                if used_fallback:
                    prev_fails = state.get("gemini_fail_count", 0)
                    state["gemini_fail_count"] = prev_fails + 1
                    state["last_error"] = "Gemini در دسترس نیست — ترجمه جایگزین استفاده شد"
                    if prev_fails == 0 and ADMIN_ID:
                        try:
                            send_message(ADMIN_ID, "⚠️ Gemini در دسترس نیست. اخبار با ترجمه جایگزین منتشر می‌شن.\nلطفاً GEMINI_API_KEY رو بررسی کن.")
                        except Exception as e:
                            print(f"[WARN] notify admin failed: {e}")
                elif analyzed:
                    state["gemini_fail_count"] = 0
                    state["last_error"] = None

                for a in articles:
                    news_id = a.get("id", a.get("hash", ""))
                    if news_id:
                        seen.add(news_id)
                storage.save_seen(seen)

                pending = storage.load_pending()
                approved = storage.load_approved()

                for item in analyzed:
                    source_trust = TRUST_SCORES.get(item["source"], 3)
                    item["source_trust"] = source_trust
                    item["id"] = item.get("id", item.get("hash", ""))
                    item["score"] = calculate_score(item)

                    if item["score"] < AUTO_REJECT_THRESHOLD:
                        print(f"[REJECT] Low score ({item['score']}): {item['title_fa'][:50]}")
                        pub = storage.load_published()
                        pub.append({**item, "published": False, "rejected_at": _now(), "reject_reason": "low_score"})
                        storage.save_published(pub)
                        continue

                    is_trusted = source_trust >= 4
                    is_confirmed = item["confidence"] == "confirmed"
                    is_high_score = item["score"] >= AUTO_PUBLISH_THRESHOLD
                    is_fallback = item.get("fallback", False)

                    # انتشار خودکار: هم از config و هم از کلید /auto در ربات پیروی می‌کنه
                    auto_enabled = AUTO_PUBLISH_ENABLED and state.get("auto_publish_enabled", True)
                    should_auto = auto_enabled and (
                        (is_trusted and is_confirmed and is_high_score)
                        or (is_fallback and source_trust >= 4)
                    )

                    if should_auto:
                        item["status"] = "approved"
                        item["admin_action"] = "auto_approved"
                        approved.append(item)
                        notify_admin_new(item)
                        print(f"[AUTO] Approved: {item['title_fa'][:50]} (score: {item['score']})")
                    else:
                        item["status"] = "pending"
                        pending.append(item)
                        _send_review(item)
                        print(f"[PENDING] {item['title_fa'][:50]} (score: {item['score']})")

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

    print(f"[{datetime.utcnow().isoformat()}] Pipeline finished")


def auto_reject(articles: list) -> list:
    kept = []
    for a in articles:
        title_lower = a["title"].lower()
        if any(kw.lower() in title_lower for kw in REJECT_KEYWORDS):
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
