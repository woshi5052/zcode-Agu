"""
A股特有过滤层 —— 涨跌停 / ST / 停牌 / 流动性 / 次新股
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config.settings import DEFAULT_PARAMS


def filter_by_chinese_holiday(date=None):
    """简化版：检查是否为周末（完整交易日历需额外库）"""
    if date is None:
        date = datetime.now()
    return date.weekday() >= 5  # 周六=5, 周日=6


# ============================================
# 个股过滤
# ============================================

def filter_limit_up_down(df: pd.DataFrame) -> dict:
    """
    涨跌停过滤
    返回 {"pass": bool, "reason": str}
    """
    if df is None or len(df) < 2:
        return {"pass": False, "reason": "数据不足"}

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = latest.get("close", 0)
    prev_close = prev.get("close", 1)

    if prev_close <= 0:
        return {"pass": False, "reason": "价格异常"}

    change_pct = (close - prev_close) / prev_close

    # 判断板块（通过代码前缀）
    code = latest.get("code", "")

    # 北交所 (8开头) → 30%
    # 科创板 (688) / 创业板 (300, 301) → 20%
    # 主板 → 10%
    if code.startswith("8"):
        limit = 0.30
    elif code.startswith("688") or code.startswith("300") or code.startswith("301"):
        limit = 0.20
    else:
        limit = 0.10

    # 涨停 → 买不到
    if change_pct >= limit * 0.98:  # 0.98 容差
        return {"pass": False, "reason": f"涨停({change_pct:.1%})"}

    # 跌停 → 卖不掉（对持仓来说是风险，对新推荐来说排除）
    if change_pct <= -limit * 0.98:
        return {"pass": False, "reason": f"跌停({change_pct:.1%})"}

    return {"pass": True, "reason": "ok"}


def filter_liquidity(df: pd.DataFrame, min_avg_amount: float = None) -> dict:
    """
    流动性过滤：日均成交额 >= min_avg_amount
    """
    if df is None or len(df) < 20:
        return {"pass": False, "reason": "数据不足"}

    if min_avg_amount is None:
        min_avg_amount = DEFAULT_PARAMS["min_avg_amount"]

    avg_amount = df["amount"].tail(20).mean()

    if avg_amount < min_avg_amount:
        return {"pass": False, "reason": f"成交额低({avg_amount/1e8:.1f}亿)"}

    return {"pass": True, "reason": f"日均{avg_amount/1e8:.1f}亿"}


def filter_new_stock(df: pd.DataFrame, min_days: int = None) -> dict:
    """
    次新股过滤：上市天数 >= min_days
    """
    if df is None:
        return {"pass": False, "reason": "无数据"}

    if min_days is None:
        min_days = DEFAULT_PARAMS["min_list_days"]

    if len(df) < min_days:
        return {"pass": False, "reason": f"次新股({len(df)}天)"}

    return {"pass": True, "reason": "ok"}


def filter_st_stock(name: str) -> dict:
    """ST 股票过滤"""
    if not name:
        return {"pass": False, "reason": "名称未知"}

    name_upper = str(name).upper()
    if "ST" in name_upper:
        return {"pass": False, "reason": "ST股票"}

    return {"pass": True, "reason": "ok"}


def filter_suspended(df: pd.DataFrame, days: int = 3) -> dict:
    """
    停牌/不活跃过滤：最近N天是否有交易
    如果最近3天成交量为0 → 疑似停牌
    """
    if df is None or len(df) < days:
        return {"pass": False, "reason": "数据不足"}

    recent_vol = df["volume"].tail(days)
    if (recent_vol == 0).all():
        return {"pass": False, "reason": "疑似停牌(无成交)"}

    return {"pass": True, "reason": "ok"}


# ============================================
# 综合过滤
# ============================================

def run_all_filters(df: pd.DataFrame, name: str = "", code: str = "") -> tuple[bool, list[str]]:
    """
    运行所有A股过滤
    返回 (通过?, [失败原因列表])
    """
    # 添加code到df中供filter_limit_up_down使用
    if code and "code" not in df.columns:
        df = df.copy()
        df["code"] = code

    checks = [
        ("次新股", filter_new_stock(df)),
        ("停牌", filter_suspended(df)),
        ("涨跌停", filter_limit_up_down(df)),
        ("流动性", filter_liquidity(df)),
        ("ST", filter_st_stock(name)),
    ]

    failures = [f"[{label}] {result['reason']}"
                for label, result in checks
                if not result["pass"]]

    return len(failures) == 0, failures


def stock_pool_filter(data_dict: dict, names_map: dict = None) -> dict:
    """
    对股票池全局过滤，返回 {code: df} 仅保留通过的
    """
    filtered = {}
    stats = {"total": len(data_dict), "passed": 0, "rejected": {}}

    for code, df in data_dict.items():
        name = names_map.get(code, "") if names_map else ""
        passed, failures = run_all_filters(df, name=name, code=code)

        if passed:
            filtered[code] = df
            stats["passed"] += 1
        else:
            for f in failures:
                reason = f.split("] ")[-1] if "] " in f else f
                stats["rejected"][code] = reason

    print(f"  选股池过滤: {stats['passed']}/{stats['total']} 通过")
    return filtered
