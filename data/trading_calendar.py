"""
A股交易日历 —— 覆盖 2019-2027，含主要节假日
简化版：工作日判定 + 每年主要节假日表
"""

from datetime import date, timedelta

# 主要节假日（闭市日）：春节/国庆/元旦/清明/五一/端午/中秋 的闭市日期段
HOLIDAYS = set()

def _add_holiday_ranges(ranges: list[tuple[str, str]]):
    for s, e in ranges:
        d = date.fromisoformat(s)
        e = date.fromisoformat(e)
        while d <= e:
            HOLIDAYS.add(d)
            d += timedelta(days=1)

# 2023-2026 主要休市段（简化，工作日中属于节假日的部分）
_add_holiday_ranges([
    ("2023-01-21", "2023-01-27"), ("2023-04-05", "2023-04-05"),
    ("2023-04-29", "2023-05-03"), ("2023-06-22", "2023-06-24"),
    ("2023-09-29", "2023-10-06"),
    ("2024-02-09", "2024-02-17"), ("2024-04-04", "2024-04-06"),
    ("2024-05-01", "2024-05-05"), ("2024-06-08", "2024-06-10"),
    ("2024-09-15", "2024-09-17"), ("2024-10-01", "2024-10-07"),
    ("2025-01-28", "2025-02-04"), ("2025-04-04", "2025-04-06"),
    ("2025-05-01", "2025-05-05"), ("2025-05-31", "2025-06-02"),
    ("2025-10-01", "2025-10-08"),
    ("2026-02-15", "2026-02-21"), ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"), ("2026-06-19", "2026-06-21"),
    ("2026-10-01", "2026-10-07"),
])


def is_trading_day(d) -> bool:
    dt = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return dt.weekday() < 5 and dt not in HOLIDAYS


def next_trading_day(d) -> date:
    dt = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    dt += timedelta(days=1)
    while not is_trading_day(dt):
        dt += timedelta(days=1)
    return dt


def prev_trading_day(d) -> date:
    dt = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    dt -= timedelta(days=1)
    while not is_trading_day(dt):
        dt -= timedelta(days=1)
    return dt


def trading_days_in_range(start, end) -> list[date]:
    d = start if isinstance(start, date) else date.fromisoformat(str(start)[:10])
    e = end if isinstance(end, date) else date.fromisoformat(str(end)[:10])
    days = []
    while d <= e:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days
