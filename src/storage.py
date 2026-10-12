"""
Storage — از Cloudflare KV استفاده می‌کنه
"""
from . import cloudflare_kv

def load_state():
    return cloudflare_kv.get("state", {
        "last_update_id": 0,
        "last_collect_time": None,
        "daily_post_count": 0,
        "daily_post_date": None,
        "paused": False,
        "auto_publish": False,
        "current_stage": "idle",
        "stage_updated_at": None,
        "last_error": None,
        "gemini_fail_count": 0
    })

def save_state(state):
    cloudflare_kv.put("state", state)

def set_stage(state, stage, error=None):
    state["current_stage"] = stage
    from datetime import datetime
    state["stage_updated_at"] = datetime.utcnow().isoformat() + "Z"
    if error:
        state["last_error"] = str(error)
    return state

def load_seen():
    return set(cloudflare_kv.get("seen", []))

def save_seen(seen):
    cloudflare_kv.put("seen", list(seen))

def load_pending():
    return cloudflare_kv.get("pending", [])

def save_pending(items):
    cloudflare_kv.put("pending", items)

def load_approved():
    return cloudflare_kv.get("approved", [])

def save_approved(items):
    cloudflare_kv.put("approved", items)

def load_published():
    return cloudflare_kv.get("published", [])

def save_published(items):
    cloudflare_kv.put("published", items)
