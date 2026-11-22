"""ساعت UTC بدون هشدار deprecated (datetime.utcnow از پایتون ۳٫۱۲ منسوخ است).

همان مقدار naive-UTC قبلی را برمی‌گرداند تا مقایسه‌ها و ذخیره‌های موجود (ISO + «Z») بدون تغییر بمانند.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
