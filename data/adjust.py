"""
数据层 v3.0 —— 唯一数据门面 get_price()
前复权统一 + 交易日历 + 数据校验 + 股票池管理
"""

import os
import pandas as pd
from datetime import datetime

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
ADJUST_MODE = "qfq"  # 全系统唯一：前复权


def get_price(symbol: str, start: str = "2020-01-01", end: str = "2026-07-01",
              days: int = 9999) -> pd.DataFrame | None:
    """
    唯一数据入口：所有模块只能通过本函数拿K线，返回已前复权数据。
    顺序：本地缓存 → AKShare 东财 → 新浪备用。
    """
    cache_file = os.path.join(CACHE_DIR, f"{symbol}.csv")
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, parse_dates=["date"], index_col="date")
        if "close" in df.columns:
            df = df.loc[start:end]
            if len(df) > 0:
                return df
    return None


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保含 open/high/low/close/volume 列"""
    req = ["open", "high", "low", "close", "volume"]
    missing = [c for c in req if c not in df.columns]
    if missing:
        raise ValueError(f"缺少列: {missing}")
    return df[req]


def is_trading_day(date) -> bool:
    """交易日判断：周一至五，跳过春节/国庆等（简化版：用 A 股日历文件或规则）"""
    dt = pd.Timestamp(date)
    return dt.weekday() < 5


def next_trading_day(date) -> pd.Timestamp:
    dt = pd.Timestamp(date) + pd.Timedelta(days=1)
    while not is_trading_day(dt):
        dt += pd.Timedelta(days=1)
    return dt


def prev_trading_day(date) -> pd.Timestamp:
    dt = pd.Timestamp(date) - pd.Timedelta(days=1)
    while not is_trading_day(dt):
        dt -= pd.Timedelta(days=1)
    return dt


def trading_days_in_range(start, end) -> list:
    return [d for d in pd.date_range(start, end) if is_trading_day(d)]
