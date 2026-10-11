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
    AUTO_PUBLISH_TRUSTED, AUTO_PUBLISH_CONFIRMED, TRUST_SCORES, REJECT_KEYWORDS
)
from src.collectors import collect
from src.analyzer import analyze_articles
from src.fast_track import process_fast_track
from src.bot import poll_updates, _send_review, notify_admin_new
from src.publisher import publish_approved


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
                # حذف fast-track از لیست اصلی
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
                    is_trusted = source_trust >= 4
                    is_confirmed = item["confidence"] == "confirmed"
                    is_high_imp = item["importance"] == "high"

                    if (AUTO_PUBLISH_TRUSTED and AUTO_PUBLISH_CONFIRMED
                        and is_trusted and is_confirmed and is_high_imp):
                        # ✅ خودکار: منبع معتبر + قطعی + مهم
                        item["status"] = "approved"
                        item["admin_action"] = "auto_approved"
                        approved.append(item)
                        notify_admin_new(item)
                    else:
                        # 👀 نیاز به تایید شما
                        item["status"] = "pending"
                        item["source_trust"] = source_trust
                        pending.append(item)
                        _send_review(item)

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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        force = "--force" in sys.argv
        run(force=force)
    else:
        print("Usage: python main.py run [--force]")
