"""
股票池管理 —— point-in-time universe，防生存偏差
"""

import os
import pandas as pd

# 手动维护退市/ST 名单（示例，实际需从 AKShare 历史成分接口拉取）
REMOVED_STOCKS = {
    # code: 退市/ST 日期
    "600215": "2024-06-01",  # 示例：退市
}

DEFAULT_UNIVERSE = [
    # 沪深300 代表性成分（示例 20 支，实际应从接口拉全量）
    "600519", "601318", "600036", "000858", "600900", "601166", "600030",
    "000333", "600276", "601012", "000651", "600887", "601888", "600309",
    "000568", "601899", "600031", "002415", "600585", "601088",
]


def get_universe(as_of: str = None) -> list[str]:
    """as_of 日期的真实股票池 = 当日存续 + 此后退市/ST 的股票（防生存偏差）。
    简化版：从缓存目录读取已有股票 + 过滤退市名单。
    """
    cache_dir = os.path.join(os.path.dirname(__file__), "cache")
    codes = []
    if os.path.exists(cache_dir):
        codes = [f.replace(".csv", "") for f in os.listdir(cache_dir) if f.endswith(".csv")]
    if not codes:
        codes = DEFAULT_UNIVERSE.copy()
    if as_of:
        codes = [c for c in codes if not (c in REMOVED_STOCKS and REMOVED_STOCKS[c] < as_of)]
    return codes
