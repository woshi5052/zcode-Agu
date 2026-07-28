"""
AKShare 数据获取层 —— A股日K线 + 基础信息
"""

import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

import akshare as ak

from config.settings import DATA_DIR, STOCK_POOL, LOOKBACK_DAYS

# ============================================
# 沪深300成分股
# ============================================

def get_hs300_stocks() -> pd.DataFrame:
    """获取沪深300成分股列表"""
    try:
        df = ak.index_stock_cons_csindex(symbol="000300")
        df = df.rename(columns={
            "成分券代码": "code",
            "成分券名称": "name",
            "交易所": "exchange",
        })
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df[["code", "name", "exchange"]]
    except Exception as e:
        print(f"[WARN] 获取沪深300成分股失败: {e}")
        return pd.DataFrame()


def _format_code(code: str) -> str:
    """格式化股票代码为 AKShare 所需格式"""
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    return code


# ============================================
# 日K线数据
# ============================================

def fetch_daily_kline(code: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """
    获取单只股票日K线数据
    返回 DataFrame: [date, open, high, low, close, volume, amount]
    """
    cache_file = DATA_DIR / f"{code}.csv"

    # 尝试读缓存（1小时内有效）
    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).seconds < 3600:
            try:
                df = pd.read_csv(cache_file, parse_dates=["date"], index_col="date")
                if not df.empty:
                    return df
            except Exception:
                pass

    symbol = _format_code(code)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            # 新浪日K线接口（更稳定）
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                adjust="qfq",  # 前复权
            )

            if df is None or df.empty:
                return None

            # 统一列名
            df = df.rename(columns={
                "date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "amount": "amount",
            })

            # 确保有 amount 列
            if "amount" not in df.columns:
                df["amount"] = df["close"] * df["volume"]

            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df = df.sort_index()

            # 只保留最近N天
            df = df.tail(days)

            # 缓存
            df.to_csv(cache_file)
            return df

        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"\n  [RETRY] {code} 第{attempt+1}次失败, {wait}秒后重试...")
                time.sleep(wait)
            else:
                print(f"\n  [WARN] 获取 {code} K线最终失败: {e}")
                return None


# ============================================
# 批量获取
# ============================================

def fetch_stock_pool_data(stock_list: list[str], max_stocks: int = 300) -> dict:
    """
    批量获取股票池日K线数据
    返回 {code: DataFrame}
    """
    result = {}
    total = min(len(stock_list), max_stocks)

    for i, code in enumerate(stock_list[:max_stocks]):
        print(f"\r  获取数据: {i+1}/{total}  {code}", end="", flush=True)
        df = fetch_daily_kline(code)
        if df is not None and len(df) >= 50:
            result[code] = df
        time.sleep(0.3)  # AKShare 限速

    print(f"\n  完成: 成功 {len(result)}/{total} 支")
    return result


# ============================================
# 基础信息（ST/上市日期等）
# ============================================

def get_stock_basic_info() -> pd.DataFrame:
    """获取A股基本信息（含ST标记、上市日期等）"""
    cache_file = DATA_DIR / "stock_basic_info.csv"

    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).days < 1:
            try:
                return pd.read_csv(cache_file, dtype={"code": str})
            except Exception:
                pass

    try:
        df = ak.stock_info_a_code_name()
        df = df.rename(columns={"code": "code", "name": "name"})
        df["code"] = df["code"].astype(str).str.zfill(6)
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"[WARN] 获取股票基本信息失败: {e}")
        return pd.DataFrame(columns=["code", "name"])


def is_st_stock(name: str) -> bool:
    """判断是否为ST股票"""
    if not name:
        return False
    return "ST" in str(name).upper() or "*ST" in str(name).upper()
