"""
回测绩效指标
"""

import numpy as np
import pandas as pd
from backtest.engine import BacktestResult, Trade


def by_year(result: BacktestResult) -> pd.DataFrame:
    """按年分组统计"""
    if not result.trades:
        return pd.DataFrame()

    rows = []
    for trade in result.trades:
        year = trade.exit_date[:4]
        rows.append({
            "year": year,
            "pnl": trade.pnl_pct,
            "win": 1 if trade.pnl_pct > 0 else 0,
            "hold_days": trade.holding_days,
        })

    df = pd.DataFrame(rows)
    grouped = df.groupby("year").agg(
        trades=("pnl", "count"),
        win_rate=("win", "mean"),
        avg_pnl=("pnl", "mean"),
        total_pnl=("pnl", "sum"),
        avg_hold=("hold_days", "mean"),
    ).reset_index()

    grouped["win_rate"] = (grouped["win_rate"] * 100).round(1)
    grouped["avg_pnl"] = grouped["avg_pnl"].round(2)
    grouped["total_pnl"] = grouped["total_pnl"].round(2)
    grouped["avg_hold"] = grouped["avg_hold"].round(1)

    return grouped


def by_market_regime(result: BacktestResult,
                     benchmark: pd.Series = None) -> dict:
    """
    按市场状态分组：
    - 牛市：均线多头排列 + 价格在年线上方
    - 熊市：均线空头排列 + 价格在年线下方
    - 震荡：其他
    """
    if not result.trades or benchmark is None:
        return {}

    # 简化：用年线方向判断
    ma250 = benchmark.rolling(250).mean()
    regimes = {}

    for trade in result.trades:
        date = pd.Timestamp(trade.exit_date)
        if date in ma250.index and date in benchmark.index:
            if benchmark.loc[date] > ma250.loc[date] * 1.05:
                regime = "bull"
            elif benchmark.loc[date] < ma250.loc[date] * 0.95:
                regime = "bear"
            else:
                regime = "sideways"
        else:
            regime = "unknown"

        if regime not in regimes:
            regimes[regime] = {"trades": 0, "wins": 0, "total_pnl": 0}
        regimes[regime]["trades"] += 1
        regimes[regime]["wins"] += 1 if trade.pnl_pct > 0 else 0
        regimes[regime]["total_pnl"] += trade.pnl_pct

    result = {}
    for regime, data in regimes.items():
        result[regime] = {
            "trades": data["trades"],
            "win_rate": round(data["wins"] / data["trades"] * 100, 1),
            "total_pnl": round(data["total_pnl"], 2),
            "avg_pnl": round(data["total_pnl"] / data["trades"], 2),
        }

    return result


def print_report(result: BacktestResult):
    """打印回测报告"""
    s = result.summary()

    print(f"\n{'='*60}")
    print(f"  📊 回测报告")
    print(f"{'='*60}")
    print(f"  回测区间: {s['start']} → {s['end']}")
    print(f"  交易次数: {s['total_trades']}")
    print(f"  胜率:     {s['win_rate']}%")
    print(f"  平均盈利: {s['avg_win']}%")
    print(f"  平均亏损: {s['avg_loss']}%")
    print(f"  Profit Factor: {s['profit_factor']}")
    print(f"  总收益率:  {s['total_return']}%")
    print(f"  年化收益:  {s['annual_return']}%")
    print(f"  最大回撤:  {s['max_drawdown']}%")
    print(f"  夏普比率:  {s['sharpe_ratio']}")
    print(f"  平均持仓:  {s['avg_hold_days']}天")
    print(f"{'='*60}")

    # 按年
    yearly = by_year(result)
    if not yearly.empty:
        print(f"\n  按年统计:")
        print(yearly.to_string(index=False))

    # 按离场原因
    if result.trades:
        reasons = {}
        for t in result.trades:
            r = t.exit_reason
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n  离场原因分布:")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r}: {c}次")
