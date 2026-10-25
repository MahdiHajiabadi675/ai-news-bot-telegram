"""
اعتبار پویای منابع

به‌جای جدول ثابت، اعتبار هر منبع از سه سیگنال ساخته می‌شود:

  ۱. پیش‌فرض اولیه (TRUST_SCORES در config) — فقط نقطهٔ شروع، با وزن TRUST_PRIOR_WEIGHT.
  ۲. تصمیم‌های ادمین — هر «تایید/ویژه» به نفع منبع و هر «رد» به ضرر آن حساب می‌شود.
     شواهد با نیمه‌عمر TRUST_HALF_LIFE_DAYS کم‌اثر می‌شوند؛ پس منبعی که بد شده
     دوباره می‌تواند اعتبار بگیرد و منبعی که خراب شده زود شناخته می‌شود.
  ۳. تایید چندمنبعی — اگر چند منبع مستقل همان خبر را داده باشند، اعتبار همان خبر
     (نه خود منبع) بالا می‌رود.

فرمول اعتبار منبع (از ۱ تا ۵):
    prior_rate = (prior - 1) / 4
    rate       = (pos + prior_rate * K) / (pos + neg + K)
    trust      = 1 + 4 * rate

پیاده‌سازی معادل این منطق در worker/index.js هم هست (ثبت تصمیم ادمین لحظه‌ای
در Worker انجام می‌شود) — اگر اینجا را تغییر دادی، آنجا را هم عوض کن.
"""
import re
from datetime import datetime

from . import cloudflare_kv
from .config import (
    TRUST_PRIOR_WEIGHT, TRUST_HALF_LIFE_DAYS, TRUST_FEATURE_WEIGHT,
    CORROBORATION_BONUS, CORROBORATION_MAX_BONUS, TRUST_SCORES,
)

KEY = "source_stats"
DEFAULT_PRIOR = 3


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", ""))
    except Exception:
        return None


def _decay_factor(updated, now):
    last = _parse(updated)
    if not last or now <= last:
        return 1.0
    days = (now - last).total_seconds() / 86400
    return 0.5 ** (days / max(TRUST_HALF_LIFE_DAYS, 1))


def load_stats() -> dict:
    stats = cloudflare_kv.get(KEY, {})
    return stats if isinstance(stats, dict) else {}


def save_stats(stats: dict):
    cloudflare_kv.put(KEY, stats)


def prior_of(source: str) -> int:
    return TRUST_SCORES.get(source, DEFAULT_PRIOR)


def compute_trust(prior: float, pos: float, neg: float) -> float:
    prior_rate = (max(1.0, min(5.0, prior)) - 1) / 4
    rate = (pos + prior_rate * TRUST_PRIOR_WEIGHT) / (pos + neg + TRUST_PRIOR_WEIGHT)
    return round(1 + 4 * rate, 2)


def trust_of(source: str, stats: dict, now=None) -> float:
    """اعتبار فعلی یک منبع (۱ تا ۵) با در نظر گرفتن زوال شواهد"""
    now = now or datetime.utcnow()
    entry = stats.get(source) or {}
    prior = prior_of(source)  # پیش‌فرض اولیه همیشه از config (ویرایش config اعمال می‌شود)
    f = _decay_factor(entry.get("updated"), now)
    return compute_trust(prior, float(entry.get("pos", 0)) * f, float(entry.get("neg", 0)) * f)


def record_decision(stats: dict, source: str, action: str, prior=None, now=None) -> dict:
    """
    ثبت یک تصمیم ادمین. action: approve | feature | reject
    تصمیم‌های خنثی (مثل پاک‌کردن صف) هرگز ثبت نمی‌شوند.
    """
    if action not in ("approve", "feature", "reject"):
        return stats
    now = now or datetime.utcnow()
    entry = stats.get(source) or {"prior": prior if prior is not None else prior_of(source), "pos": 0.0, "neg": 0.0}
    f = _decay_factor(entry.get("updated"), now)
    pos = float(entry.get("pos", 0)) * f
    neg = float(entry.get("neg", 0)) * f
    if action == "reject":
        neg += 1.0
    else:
        pos += TRUST_FEATURE_WEIGHT if action == "feature" else 1.0
    entry.update({"pos": round(pos, 3), "neg": round(neg, 3), "updated": now.isoformat() + "Z"})
    stats[source] = entry
    return stats


def sync_priors(stats: dict, sources) -> bool:
    """
    پیش‌فرض اولیهٔ همهٔ منابع فعال را در stats ثبت می‌کند (برای نمایش در /sources).
    منبعِ حذف‌شده از FEEDS که هیچ تصمیمی دربارهٔ آن ثبت نشده، از stats پاک می‌شود.
    اگر تغییری بود True.
    """
    names = list(sources)
    changed = False
    for name in names:
        entry = stats.setdefault(name, {"pos": 0.0, "neg": 0.0})
        want = prior_of(name)
        if entry.get("prior") != want:
            entry["prior"] = want
            changed = True
    for name in list(stats):
        e = stats[name]
        if name not in names and not float(e.get("pos", 0)) and not float(e.get("neg", 0)):
            del stats[name]
            changed = True
    return changed


# ───────────── تایید چندمنبعی ─────────────

_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "its", "are", "was", "has",
    "have", "will", "new", "how", "why", "what", "can", "not", "but", "you", "your",
    "into", "about", "after", "over", "more", "says", "say", "از", "به", "در", "با",
    "که", "را", "این", "آن", "برای", "یک", "و",
}


def _tokens(title: str) -> set:
    words = re.findall(r"[\w\-.]+", (title or "").lower())
    return {w.strip(".-") for w in words if len(w.strip(".-")) >= 3 and w not in _STOP}


def corroboration_counts(articles: list) -> dict:
    """
    برای هر خبر می‌گوید چند منبع «مستقل دیگر» در همین دسته همان خبر را داده‌اند.
    شباهت: حداقل ۳ کلمهٔ مشترک و ≥ ۵۰٪ کلمات عنوان کوتاه‌تر.
    برمی‌گرداند: {article_id: تعداد منبع‌های دیگر}
    """
    toks = [(a, _tokens(a.get("title", ""))) for a in articles]
    result = {}
    for a, ta in toks:
        others = set()
        for b, tb in toks:
            if b is a or b.get("source") == a.get("source"):
                continue
            common = len(ta & tb)
            if common >= 3 and common / max(1, min(len(ta), len(tb))) >= 0.5:
                others.add(b.get("source"))
        result[a.get("id") or a.get("hash") or id(a)] = len(others)
    return result


def with_corroboration(trust: float, other_sources: int) -> float:
    bonus = min(CORROBORATION_MAX_BONUS, other_sources * CORROBORATION_BONUS)
    return round(min(5.0, trust + bonus), 2)
