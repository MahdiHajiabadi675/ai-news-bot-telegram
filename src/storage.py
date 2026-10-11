import json
from pathlib import Path
from .config import DATA_DIR

def _load(fname, default):
    f = DATA_DIR / fname
    if f.exists():
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    return default

def _save(fname, data):
    f = DATA_DIR / fname
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)

def load_state():
    return _load("state.json", {
        "last_update_id": 0,
        "last_collect_time": None,
        "daily_post_count": 0,
        "daily_post_date": None,
        "paused": False,
        "auto_publish": False,
        "current_stage": "idle",
        "stage_updated_at": None,
        "last_error": None,
        "gemini_fail_count": 0,
        "fallback_translator": "googletrans"
    })

def save_state(state):
    _save("state.json", state)

def set_stage(state, stage, error=None):
    state["current_stage"] = stage
    from datetime import datetime
    state["stage_updated_at"] = datetime.utcnow().isoformat() + "Z"
    if error:
        state["last_error"] = str(error)
    return state

def load_seen():
    return set(_load("seen.json", []))

def save_seen(seen):
    _save("seen.json", list(seen))

def load_pending():
    return _load("pending.json", [])

def save_pending(items):
    _save("pending.json", items)

def load_approved():
    return _load("approved.json", [])

def save_approved(items):
    _save("approved.json", items)

def load_published():
    return _load("published.json", [])

def save_published(items):
    _save("published.json", items)
