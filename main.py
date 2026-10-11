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
from src.config import (
    COLLECT_INTERVAL_MIN, FAST_TRACK_ENABLED,
    AUTO_PUBLISH_ENABLED, AUTO_PUBLISH_THRESHOLD, AUTO_REJECT_THRESHOLD,
    TRUST_SCORES, REJECT_KEYWORDS, CATEGORY_WEIGHTS,
    SCORE_SOURCE_WEIGHT, SCORE_IMPORTANCE_WEIGHT, SCORE_CONFIDENCE_WEIGHT, SCORE_CATEGORY_WEIGHT
)
from src.collectors import collect
from src.analyzer import analyze_articles
from src.fast_track import process_fast_track
from src.bot import poll_updates, _send_review, notify_admin_new
from src.publisher import publish_approved


def calculate_score(item: dict) -> int:
    """محاسبه اسکور خبر (0-100)"""
    # Source trust (0-20)
    source_trust = TRUST_SCORES.get(item["source"], 3)
    source_score = (source_trust / 5) * SCORE_SOURCE_WEIGHT

    # Importance (0-30)
    importance_map = {"high": 1.0, "medium": 0.6, "low": 0.3}
    importance_score = importance_map.get(item["importance"], 0.5) * SCORE_IMPORTANCE_WEIGHT

    # Confidence (0-30)
    confidence_map = {"confirmed": 1.0, "likely": 0.6, "rumor": 0.2}
    confidence_score = confidence_map.get(item["confidence"], 0.5) * SCORE_CONFIDENCE_WEIGHT

    # Category (0-20)
    category_weight = CATEGORY_WEIGHTS.get(item["category"], 0.3)
    category_score = category_weight * SCORE_CATEGORY_WEIGHT

    total = int(source_score + importance_score + confidence_score + category_score)
    return min(total, 100)


def run(force=False):
    print(f"[{datetime.utcnow().isoformat()}] Pipeline started")

    # 1. Load state
    state = storage.load_state()

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
        print("[INFO] Collecting articles...")
        seen = storage.load_seen()
        articles = collect(seen)

        if articles:
            # Auto-reject: بررسی کلمات مشکوک
            articles = auto_reject(articles)

            # Fast-track: انتشار سریع بدون Gemini
            if FAST_TRACK_ENABLED:
                print("[INFO] Processing fast-track...")
                fast_items = [a for a in articles if a.get("fast_track")]
                process_fast_track(fast_items)
                articles = [a for a in articles if not a.get("fast_track")]

            # تحلیل با Gemini برای بقیه
            if articles:
                print("[INFO] Analyzing with Gemini...")
                analyzed = analyze_articles(articles)

                # Add to seen
                for a in articles:
                    seen.add(a["hash"])
                storage.save_seen(seen)

                # Route: auto-approve یا pending
                pending = storage.load_pending()
                approved = storage.load_approved()

                for item in analyzed:
                    source_trust = TRUST_SCORES.get(item["source"], 3)
                    item["source_trust"] = source_trust
                    item["score"] = calculate_score(item)

                    # Auto-reject: اسکور پایین
                    if item["score"] < AUTO_REJECT_THRESHOLD:
                        print(f"[REJECT] Low score ({item['score']}): {item['title_fa'][:50]}")
                        pub = storage.load_published()
                        pub.append({**item, "published": False, "rejected_at": _now(), "reject_reason": "low_score"})
                        storage.save_published(pub)
                        continue

                    # Auto-publish: اسکور بالا + منبع معتبر + قطعی
                    is_trusted = source_trust >= 4
                    is_confirmed = item["confidence"] == "confirmed"
                    is_high_score = item["score"] >= AUTO_PUBLISH_THRESHOLD

                    if AUTO_PUBLISH_ENABLED and is_trusted and is_confirmed and is_high_score:
                        item["status"] = "approved"
                        item["admin_action"] = "auto_approved"
                        approved.append(item)
                        notify_admin_new(item)
                        print(f"[AUTO] Published: {item['title_fa'][:50]} (score: {item['score']})")
                    else:
                        item["status"] = "pending"
                        pending.append(item)
                        _send_review(item)
                        print(f"[PENDING] {item['title_fa'][:50]} (score: {item['score']})")

                storage.save_pending(pending)
                storage.save_approved(approved)
                print(f"[INFO] Routed: {len(analyzed)} items")

        state["last_collect_time"] = now.isoformat() + "Z"

    # 4. Publish approved items
    state = publish_approved(state)

    # 5. Save state
    storage.save_state(state)

    print(f"[{datetime.utcnow().isoformat()}] Pipeline finished")


def auto_reject(articles: list) -> list:
    """فیلتر خودکار: حذف اخبار غیرمرتبط یا مشکوک"""
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
