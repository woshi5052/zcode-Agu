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
    # 低价股池(收盘<30元, 1手≤3000元, 1万本金×30%仓位=3000买得起)
    "000001","000002","000100","000157","000166","000301","000425",
    "000617","000625","000630","000708","000725","000768","000776",
    "000792","000807","000895","000963","000975","000999",
    "001965","001979","002001","002027","002050","002074","002142",
    "002179","002202","002236","002241","002304","002311","002493",
    "002532",
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
