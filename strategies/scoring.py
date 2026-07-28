"""
综合评分系统 —— 趋势策略 + 动量策略 综合排序
"""

import pandas as pd
import numpy as np

from strategies.trend_engine import TrendEngine
from config.settings import DEFAULT_PARAMS


def run_trend_analysis(data_dict: dict, names_map: dict = None,
                       top_n: int = 5, params: dict = None) -> list[dict]:
    """
    对数据池中所有股票运行趋势策略分析
    返回 Top N 推荐列表
    """
    if names_map is None:
        names_map = {}

    engine = TrendEngine(params or DEFAULT_PARAMS)
    recommendations = []

    total = len(data_dict)
    for i, (code, df) in enumerate(data_dict.items()):
        name = names_map.get(code, code)
        print(f"\r  策略分析: {i+1}/{total}  {code} {name}", end="", flush=True)

        try:
            result = engine.analyze(df, code=code, name=name)
            if result:
                recommendations.append(result)
        except Exception as e:
            print(f"\n  [WARN] {code} {name} 分析异常: {e}")

    # 按评分排序
    recommendations.sort(key=lambda x: x["score"], reverse=True)

    # 取 Top N
    top = recommendations[:top_n]

    print(f"\n  策略完成: {len(recommendations)} 支入选, Top{top_n} 推荐")

    return top


def run_momentum_analysis(data_dict: dict, names_map: dict = None) -> list[dict]:
    """
    动量策略分析（Phase 3 完善）
    目前返回基于 ROC + RSI + 量能的快速动量扫描
    """
    if names_map is None:
        names_map = {}

    momentum_results = []

    for code, df in data_dict.items():
        if df is None or len(df) < 20:
            continue

        close = df["close"]
        volume = df["volume"]
        latest = close.iloc[-1]

        # ROC(5) 和 ROC(10)
        roc5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0
        roc10 = (close.iloc[-1] / close.iloc[-11] - 1) * 100 if len(close) >= 11 else 0

        # 量比
        vol_ma20 = volume.rolling(20).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1

        # 动量评分（简单版）
        mom_score = (0.4 * roc5 + 0.3 * roc10 + 0.3 * (vol_ratio - 1) * 10)

        momentum_results.append({
            "code": code,
            "name": names_map.get(code, code),
            "momentum_score": round(mom_score, 1),
            "roc5": round(roc5, 1),
            "roc10": round(roc10, 1),
            "vol_ratio": round(vol_ratio, 2),
        })

    momentum_results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return momentum_results


def combine_scores(trend_results: list[dict], momentum_results: list[dict],
                   trend_weight: float = 0.7) -> list[dict]:
    """
    融合趋势策略和动量策略评分
    """
    # 建立动量查找表
    mom_map = {r["code"]: r["momentum_score"] for r in momentum_results}

    for rec in trend_results:
        code = rec["code"]
        trend_score = rec["score"]
        mom_score = mom_map.get(code, 0)

        # 归一化动量分到0-100
        mom_normalized = max(0, min(100, 50 + mom_score * 5))

        # 综合评分
        combined = trend_weight * trend_score + (1 - trend_weight) * mom_normalized
        rec["score"] = round(combined, 1)
        rec["momentum_score"] = round(mom_score, 1)

    trend_results.sort(key=lambda x: x["score"], reverse=True)
    return trend_results
