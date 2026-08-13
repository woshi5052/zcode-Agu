"""
基本面过滤对比实验: 基线 vs PIT三关过滤
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
    """加载股票池数据"""
    codes = get_universe()[:50]
    data_dict = {}
    for code in codes:
        df = get_price(code, start=start, end=end)
        if df is not None and len(df) > 100:
            data_dict[code] = df
    return data_dict


def build_index(data_dict: dict, start: str, end: str):
    """合成指数"""
    closes = [df["close"] for df in data_dict.values()]
    all_dates = sorted(set().union(*[c.index for c in closes]))
    synth = pd.DataFrame(index=all_dates)
    synth["close"] = sum(c.reindex(all_dates).ffill().fillna(0) for c in closes) / len(closes)
    for col in ["open", "high", "low"]:
        synth[col] = synth["close"]
    synth["volume"] = 1
    return synth.loc[start:end]


def run_one(params: dict, data_dict: dict, index_df: pd.DataFrame,
            use_fund: bool) -> dict:
    p = dict(params)
    p["use_fundamental"] = use_fund
    engine = BacktestEngine(p)
    t0 = time.time()
    result = engine.run(data_dict, names_map={c: c for c in data_dict},
                        index_df=index_df)
    elapsed = time.time() - t0

    s = result.summary()
    # 止损率 = ATR止损离场占比
    stop_count = sum(1 for t in result.trades if "止损" in t.exit_reason)
    stop_rate = round(stop_count / len(result.trades) * 100, 1) if result.trades else 0

    return {
        "trades": s["total_trades"],
        "win_rate": s["win_rate"],
        "pf": s["profit_factor"],
        "annual": s["annual_return"],
        "maxdd": s["max_drawdown"],
        "avg_hold": s["avg_hold_days"],
        "stop_rate": stop_rate,
        "rejects": len(engine.fund_rejects),
        "elapsed": round(elapsed, 0),
    }


def main():
    with open("config/params.json") as f:
        params = json.load(f)

    start, end = "2022-01-01", "2025-06-30"

    print("加载数据...")
    data_dict = build_data(start, end)
    index_df = build_index(data_dict, start, end)
    print(f"股票: {len(data_dict)} 支 | 区间: {start} → {end}")

    print(f"\n{'='*66}")
    print(f"  对比实验: 基线 vs PIT基本面三关过滤")
    print(f"{'='*66}")

    # 基线
    print(f"\n[1/2] 基线 (无基本面过滤)...")
    base = run_one(params, data_dict, index_df, use_fund=False)

    # 过滤版
    print(f"[2/2] PIT过滤版...")
    filt = run_one(params, data_dict, index_df, use_fund=True)

    # 对比表
    print(f"\n{'='*66}")
    print(f"  结果对比")
    print(f"{'='*66}")
    print(f"  {'指标':<12}{'基线':>10}{'PIT过滤':>12}{'变化':>10}")
    print(f"  {'-'*46}")
    for key, label in [
        ("trades", "交易笔数"), ("win_rate", "胜率%"),
        ("stop_rate", "止损率%"), ("pf", "PF"),
        ("annual", "年化%"), ("maxdd", "回撤%"),
        ("avg_hold", "持仓天"), ("rejects", "被过滤信号"),
    ]:
        b = base[key]
        f_ = filt[key]
        delta = f_ - b if isinstance(b, (int, float)) else 0
        sign = "+" if delta > 0 else ""
        print(f"  {label:<12}{b:>10}{f_:>12}{sign+str(delta):>10}")

    # 判定
    print(f"\n  核心问题: 止损率 {base['stop_rate']}% → {filt['stop_rate']}% "
          f"({'↓改善' if filt['stop_rate'] < base['stop_rate'] else '↑恶化'})")


if __name__ == "__main__":
    main()
