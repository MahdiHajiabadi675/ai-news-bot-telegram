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

# State
def load_state():
    return _load("state.json", {
        "last_update_id": 0,
        "last_collect_time": None,
        "daily_post_count": 0,
        "daily_post_date": None,
        "paused": False,
        "auto_publish": False
    })

def save_state(state):
    _save("state.json", state)

# Seen (dedup)
def load_seen():
    return set(_load("seen.json", []))

def save_seen(seen):
    _save("seen.json", list(seen))

# Pending
def load_pending():
    return _load("pending.json", [])

def save_pending(items):
    _save("pending.json", items)

# Approved
def load_approved():
    return _load("approved.json", [])

def save_approved(items):
    _save("approved.json", items)

# Published (log)
def load_published():
    return _load("published.json", [])

def save_published(items):
    _save("published.json", items)
