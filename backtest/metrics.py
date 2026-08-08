"""
回测绩效指标 v3.0
"""

import numpy as np
import pandas as pd


def by_year(trades: list) -> pd.DataFrame:
    """按年分组统计"""
    if not trades:
        return pd.DataFrame()

    rows = []
    for trade in trades:
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


def exit_reasons(trades: list) -> dict:
    """离场原因分布"""
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return dict(sorted(reasons.items(), key=lambda x: -x[1]))


def print_report(result) -> None:
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

    yearly = by_year(result.trades)
    if not yearly.empty:
        print(f"\n  按年统计:")
        print(yearly.to_string(index=False))

    reasons = exit_reasons(result.trades)
    if reasons:
        print(f"\n  离场原因分布:")
        for r, c in reasons.items():
            print(f"    {r}: {c}次")
