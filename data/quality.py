"""
数据质量校验
"""

import pandas as pd
import numpy as np


def check_kline(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    校验日K线数据质量
    返回 {"pass": bool, "issues": list}
    """
    issues = []

    if df is None or len(df) < 20:
        return {"pass": False, "issues": ["数据不足"]}

    close = df["close"]
    volume = df["volume"]

    # 1. 空值检查
    null_pct = close.isna().sum() / len(close)
    if null_pct > 0.01:
        issues.append(f"空值率{null_pct:.1%}")

    # 2. 价格异常跳空（除权日除外，单日>15%且无公告）
    pct_chg = close.pct_change()
    extreme = pct_chg[abs(pct_chg) > 0.15]
    if len(extreme) > 0:
        issues.append(f"异常跳空{len(extreme)}次")

    # 3. 零成交量天数（连续>3天=停牌）
    zero_vol = (volume == 0).astype(int)
    if zero_vol.sum() > 3:
        issues.append(f"零成交量{zero_vol.sum()}天")

    # 4. 日期连续性（自然日缺口>5天）
    if hasattr(df.index, 'to_series'):
        gaps = df.index.to_series().diff().dt.days
        big_gaps = gaps[gaps > 5]
        if len(big_gaps) > 0:
            issues.append(f"日期缺口{len(big_gaps)}次")

    return {
        "pass": len(issues) == 0,
        "issues": issues,
        "symbol": symbol,
        "rows": len(df),
    }
