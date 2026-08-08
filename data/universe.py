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
    # 50支低价股池(3年+数据, ¥2-44, 一手中位数¥2000)
    "000002","000166","000100","002027","000725","000630","001979",
    "000617","000157","000625","000425","001965","000001","601012",
    "000301","000708","600021","002236","600585","601166","600031",
    "000768","000776","002202","002241","000895","000999","600887",
    "000975","000807","002074","600900","600030","000792","000963",
    "000338","002001","002142","000063","601899","002179","002415",
    "000938","600036","002050","000651","002304","002230","601088",
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
