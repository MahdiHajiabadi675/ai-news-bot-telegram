"""سلامت ماژول‌های اختیاری (views، weekly، edu، digest، brief، backup، ...).

هر ماژول در main.py با خطاگیر اجرا می‌شود تا خرابی یکی، انتشار خبر را نخواباند. اما خطا قبلاً
فقط در لاگ GitHub می‌ماند. اینجا نتیجهٔ هر اجرا ثبت می‌شود و داخل «heartbeat» (که هر اجرا
به‌هرحال نوشته می‌شود) می‌رود؛ یعنی هیچ نوشتن اضافه‌ای در KV لازم نیست.

  • /health و /dashboard آخرین وضعیت هر ماژول را نشان می‌دهند
  • اگر ماژولی ALERT_AT بار پیاپی خراب شود، یک‌بار به ادمین پیام می‌رود
"""
from . import clock

ALERT_AT = 3

_run = {}  # نتیجهٔ همین اجرا: name -> {"ok": bool, "err": str|None}


def reset():
    _run.clear()


def ok(name):
    _run[name] = {"ok": True, "err": None}


def fail(name, exc):
    err = f"{type(exc).__name__}: {str(exc)[:120]}"
    _run[name] = {"ok": False, "err": err}
    print(f"[{name.upper()}] failed: {err}")


def current():
    return dict(_run)


def merge(prev):
    """ادغام نتیجهٔ این اجرا با سابقهٔ قبلی (از heartbeat قبلی). برمی‌گرداند: (merged, تازه‌خراب‌ها)."""
    prev = prev if isinstance(prev, dict) else {}
    now = clock.utcnow().isoformat() + "Z"
    merged, alerts = {}, []
    for name, r in _run.items():
        p = prev.get(name) if isinstance(prev.get(name), dict) else {}
        fails = 0 if r["ok"] else int(p.get("fails", 0)) + 1
        if not r["ok"] and fails == ALERT_AT:
            alerts.append(name)
        merged[name] = {
            "ok": r["ok"], "fails": fails, "err": r["err"],
            "last_ok": now if r["ok"] else p.get("last_ok"),
        }
    for name, p in prev.items():  # ماژولی که این اجرا نیامد (مثلاً حالت skip) سابقه‌اش حفظ شود
        if name not in merged and isinstance(p, dict):
            merged[name] = p
    return merged, alerts
