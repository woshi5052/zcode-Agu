"""
数据统一入口 — 全仓库唯一数据门面
v3.0: 所有模块必须通过 get_price() 获取K线，禁止直接调 akshare
"""

import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from data.akshare_fetcher import _format_code
from data.quality import check_kline

ADJUST_MODE = "qfq"  # 前复权，全系统唯一
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_price(symbol: str, start: str | None = None,
              end: str | None = None, days: int = 250) -> pd.DataFrame | None:
    """
    获取A股日K线 — 唯一数据入口

    Args:
        symbol: 股票代码 "600519" / "000001"
        start:   开始日期 "2024-01-01"
        end:     结束日期 "2026-07-01"
        days:    回溯天数 (当 start 未指定时)

    Returns:
        已前复权的 DataFrame，含列 [open, high, low, close, volume, amount]
        索引为 date (datetime)
    """
    cache_file = CACHE_DIR / f"{symbol}.csv"

    # 1. 尝试缓存（1小时内有效）
    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).seconds < 3600:
            try:
                df = pd.read_csv(cache_file, parse_dates=["date"], index_col="date")
                if not df.empty:
                    df = _clip_range(df, start, end, days)
                    return df
            except Exception:
                pass

    # 2. 拉取数据（优先新浪，失败降级东财）
    df = _fetch_from_sina(symbol)
    if df is None or df.empty:
        df = _fetch_from_eastmoney(symbol)

    if df is None or df.empty:
        return None

    # 3. 前复权确认
    df = _ensure_adjusted(df, symbol)

    # 4. 质量校验
    quality = check_kline(df, symbol)
    if not quality["pass"]:
        print(f"[WARN] {symbol} 数据质量问题: {quality['issues']}")

    # 5. 缓存
    df.to_csv(cache_file)

    # 6. 截取范围
    df = _clip_range(df, start, end, days)

    return df


def _clip_range(df: pd.DataFrame, start: str | None,
                end: str | None, days: int) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]
    df = df.tail(days)
    return df


def _fetch_from_sina(symbol: str) -> pd.DataFrame | None:
    """新浪日K线"""
    try:
        import akshare as ak
        code = _format_code(symbol)
        df = ak.stock_zh_a_daily(symbol=code, adjust=ADJUST_MODE)
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        if "amount" not in df.columns:
            df["amount"] = df["close"] * df["volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df
    except Exception:
        return None


def _fetch_from_eastmoney(symbol: str) -> pd.DataFrame | None:
    """东方财富日K线（备用）"""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=365*3)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date,
            adjust=ADJUST_MODE,
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
        })
        if "成交额" in df.columns:
            df["amount"] = df["成交额"]
        else:
            df["amount"] = df["close"] * df["volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df
    except Exception:
        return None


def _ensure_adjusted(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    确保数据已前复权。
    检测方式：如果最近1年价格出现>50%的跳空且不是除权日 →
    标记警告（但当前版本依赖数据源标注的复权"""
    return df


def get_index_price(code: str = "000300") -> pd.DataFrame | None:
    """获取指数日K线（沪深300等）"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=f"sh{code}")
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        if "amount" not in df.columns:
            df["amount"] = df["close"] * df["volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df
    except Exception:
        # 备用: 用ETF代替
        return get_price("510300", days=500)
