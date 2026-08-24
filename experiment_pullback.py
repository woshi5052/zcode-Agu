"""
回踩信号验证实验: 基线(突破+金叉) vs +回踩买点
指标: 止损率/胜率/PF/年化/回撤/交易笔数
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

from backtest.engine import BacktestEngine
from data.universe import get_universe
from data.adjust import get_price


def build_data(start: str, end: str):
    codes = get_universe()[:50]
    data_dict = {}
    for code in codes:
        df = get_price(code, start=start, end=end)
        if df is not None and len(df) > 100:
            data_dict[code] = df
    return data_dict


def build_index(data_dict: dict, start: str, end: str):
    closes = [df["close"] for df in data_dict.values()]
    all_dates = sorted(set().union(*[c.index for c in closes]))
    synth = pd.DataFrame(index=all_dates)
    synth["close"] = sum(c.reindex(all_dates).ffill().fillna(0) for c in closes) / len(closes)
    for col in ["open", "high", "low"]:
        synth[col] = synth["close"]
    synth["volume"] = 1
    return synth.loc[start:end]


def run_one(params: dict, data_dict: dict, index_df: pd.DataFrame,
            pullback: bool) -> dict:
    p = dict(params)
    p["enable_pullback"] = pullback
    engine = BacktestEngine(p)
    result = engine.run(data_dict, names_map={c: c for c in data_dict},
                        index_df=index_df)

    s = result.summary()
    stop_count = sum(1 for t in result.trades if "止损" in t.exit_reason)
    stop_rate = round(stop_count / len(result.trades) * 100, 1) if result.trades else 0
    # 回踩信号占比
    pullback_entries = sum(1 for t in result.trades if "回踩" in str(getattr(t, "signals", "")))

    return {
        "trades": s["total_trades"],
        "win_rate": s["win_rate"],
        "pf": s["profit_factor"],
        "annual": s["annual_return"],
        "maxdd": s["max_drawdown"],
        "stop_rate": stop_rate,
        "pullback_entries": pullback_entries,
    }


def main():
    with open("config/params.json") as f:
        params = json.load(f)

    start, end = "2022-01-01", "2025-06-30"

    print("加载数据...")
    data_dict = build_data(start, end)
    index_df = build_index(data_dict, start, end)
    print(f"股票: {len(data_dict)} 支")

    print(f"\n{'='*60}")
    print(f"  回踩信号实验: 基线 vs +回踩买点")
    print(f"{'='*60}")

    print(f"\n[1/2] 基线 (突破+金叉)...")
    base = run_one(params, data_dict, index_df, pullback=False)

    print(f"[2/2] +回踩买点...")
    pb = run_one(params, data_dict, index_df, pullback=True)

    print(f"\n{'='*60}")
    print(f"  结果对比")
    print(f"{'='*60}")
    print(f"  {'指标':<12}{'基线':>10}{'+回踩':>12}{'变化':>10}")
    print(f"  {'-'*46}")
    for key, label in [
        ("trades", "交易笔数"), ("win_rate", "胜率%"),
        ("stop_rate", "止损率%"), ("pf", "PF"),
        ("annual", "年化%"), ("maxdd", "回撤%"),
        ("pullback_entries", "回踩入场数"),
    ]:
        b = base[key]
        f_ = pb[key]
        delta = f_ - b if isinstance(b, (int, float)) else 0
        sign = "+" if delta > 0 else ""
        print(f"  {label:<12}{b:>10}{f_:>12}{sign+str(delta):>10}")

    # 判定
    print(f"\n  判定: ", end="")
    if pb["stop_rate"] < base["stop_rate"] and pb["win_rate"] >= base["win_rate"]:
        print(f"✅ 回踩有效 (止损率 {base['stop_rate']}→{pb['stop_rate']}%, 胜率未降)")
    elif pb["stop_rate"] < base["stop_rate"]:
        print(f"⚠️ 止损率改善但胜率下降 ({base['win_rate']}→{pb['win_rate']}%), 看PF")
    else:
        print(f"❌ 回踩无效 (止损率未改善)")


if __name__ == "__main__":
    main()
