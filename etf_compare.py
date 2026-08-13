"""ETF 趋势策略对比 —— 可转债ETF vs 宽基ETF，复用现有 SellEngine"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np

from backtest.engine import BacktestEngine


def run_etf_backtest(symbol, name, params, start="2022-01-01", end="2025-06-30"):
    """对单支ETF跑回测"""
    cache_file = f"data/cache/{symbol}.csv"
    if not os.path.exists(cache_file):
        return None

    df = pd.read_csv(cache_file, parse_dates=["date"], index_col="date")
    df = df.loc[start:end]
    if len(df) < 100:
        return None

    data_dict = {symbol: df}
    names_map = {symbol: name}

    # 用自身做指数（无大盘过滤）
    index_df = df[["close"]].copy()
    index_df["open"] = index_df["high"] = index_df["low"] = index_df["close"]
    index_df["volume"] = 1

    engine = BacktestEngine(params)
    result = engine.run(data_dict, names_map=names_map, index_df=index_df)
    return result


def main():
    with open("config/params.json") as f:
        params = json.load(f)

    etfs = [
        ("sh511380", "可转债ETF"),
        ("sh510300", "沪深300ETF"),
        ("sz159915", "创业板ETF"),
        ("sh510500", "中证500ETF"),
        ("sh510050", "上证50ETF"),
    ]

    print(f"{'='*70}")
    print(f"  ETF 趋势策略对比 (2022-2025, 复用现有框架)")
    print(f"{'='*70}")
    print(f"  {'ETF':<16}{'笔数':<6}{'胜率%':<8}{'PF':<8}{'年化%':<10}{'持仓天':<8}{'回撤%':<8}")
    print(f"  {'-'*65}")

    for sym, name in etfs:
        t0 = time.time()
        result = run_etf_backtest(sym, name, params)
        if result:
            s = result.summary()
            print(f"  {name:<16}{s['total_trades']:<6}{s['win_rate']:<8}"
                  f"{s['profit_factor']:<8}{s['annual_return']:<10}"
                  f"{s['avg_hold_days']:<8}{s['max_drawdown']:<8}"
                  f"({time.time()-t0:.0f}s)")

            # 离场原因
            reasons = {}
            for t in result.trades:
                reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
            if reasons:
                top = sorted(reasons.items(), key=lambda x: -x[1])[:3]
                detail = " | ".join(f"{r}:{c}" for r, c in top)
                print(f"  {'':16}{'离场:':<6}{detail}")
        else:
            print(f"  {name:<16}无数据")

    # 对比: buy & hold
    print(f"\n  {'='*50}")
    print(f"  买入持有对照 (同期)")
    print(f"  {'='*50}")
    for sym, name in etfs:
        cache_file = f"data/cache/{sym}.csv"
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file, parse_dates=["date"], index_col="date")
            sub = df.loc["2022-01-01":"2025-06-30"]
            if len(sub) > 0:
                ret = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
                days = (sub.index[-1] - sub.index[0]).days
                ann = ((sub["close"].iloc[-1] / sub["close"].iloc[0]) ** (365/days) - 1) * 100
                print(f"  {name:<16}: +{ret:.1f}% 总 / {ann:+.1f}% 年化")


if __name__ == "__main__":
    main()
