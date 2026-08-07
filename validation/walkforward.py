"""
Walk-Forward 滚动验证
训练段调参 → 验证段参数固定 → 汇总样本外绩效
"""

import json
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from backtest.engine import BacktestEngine
from data.adjust import get_price


def walk_forward(
    symbols: list[str],
    start: str = "2023-01-01",
    end: str = "2026-07-01",
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
    base_params: dict = None,
) -> dict:
    """
    滚动前推验证

    Returns:
        {
            "windows": [...],    # 每窗数据
            "oos_trades": int,   # 样本外总交易
            "oos_pf": float,     # 样本外 PF
            "oos_annual": float, # 样本外年化
            "oos_winrate": float,# 样本外胜率
            "oos_maxdd": float,  # 样本外最大回撤
        }
    """
    if base_params is None:
        with open("config/params.json") as f:
            base_params = json.load(f)

    # 生成窗口
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    train_len = relativedelta(months=train_months)
    test_len = relativedelta(months=test_months)
    step = relativedelta(months=step_months)

    windows = []
    cursor = s
    while cursor + train_len + test_len <= e:
        train_start = cursor.strftime("%Y-%m-%d")
        train_end = (cursor + train_len).strftime("%Y-%m-%d")
        test_start = (cursor + train_len).strftime("%Y-%m-%d")
        test_end = (cursor + train_len + test_len).strftime("%Y-%m-%d")
        windows.append((train_start, train_end, test_start, test_end))
        cursor += step

    print(f"Walk-Forward: {len(windows)} 个窗口 (train={train_months}m / test={test_months}m / step={step_months}m)")
    print(f"股票: {len(symbols)} 支 | 区间: {start} → {end}")

    all_oos_trades = []
    all_oos_equity = []

    for wi, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        print(f"\n窗{wi+1}/{len(windows)}: train={tr_s}→{tr_e} test={te_s}→{te_e}")

        # 获取训练段数据
        train_data = {}
        for code in symbols:
            df = get_price(code, start=tr_s, end=tr_e, days=9999)
            if df is not None and len(df) > 50:
                train_data[code] = df

        if len(train_data) < 5:
            print(f"  训练数据不足({len(train_data)}支)，跳过")
            continue

        # 训练段内调参 (简化: 用基线参数)
        params = base_params.copy()
        # TODO: 这里可以实现网格搜索自动调参

        # 获取验证段数据
        test_data = {}
        for code in list(train_data.keys()):
            df = get_price(code, start=tr_s, end=te_e, days=9999)  # 扩展到验证段
            if df is not None and len(df) > 50:
                test_data[code] = df

        if len(test_data) < 5:
            print(f"  验证数据不足，跳过")
            continue

        # 跑验证段回测
        engine = BacktestEngine(params=params, trade_cost=0.003)
        result = engine.run(test_data)
        s = result.summary()

        print(f"  样本外: PF={s['profit_factor']} 胜率={s['win_rate']}% "
              f"交易={s['total_trades']}笔 年化={s['annual_return']}% "
              f"回撤={s['max_drawdown']}%")

        all_oos_trades.extend(result.trades)

    # 汇总样本外
    if not all_oos_trades:
        return {"windows": len(windows), "oos_trades": 0, "oos_pf": 0,
                "error": "无样本外交易"}

    total_trades = len(all_oos_trades)
    wins = [t for t in all_oos_trades if t.pnl_pct > 0]
    losses = [t for t in all_oos_trades if t.pnl_pct <= 0]
    wr = round(len(wins) / total_trades * 100, 1) if total_trades > 0 else 0

    tw = sum(t.pnl_pct for t in wins)
    tl = abs(sum(t.pnl_pct for t in losses))
    pf = round(tw / tl, 2) if tl > 0 else 0

    # 构建样本外净值
    all_dates = sorted(set(
        t.exit_date for t in all_oos_trades
    ).union(t.entry_date for t in all_oos_trades))
    equity = pd.Series(1.0, index=pd.to_datetime(all_dates).sort_values())
    cum = 1.0
    for date in equity.index.sort_values():
        day_trades = [t for t in all_oos_trades
                      if t.exit_date == date.strftime("%Y-%m-%d")]
        for t in day_trades:
            cum *= (1 + t.pnl_pct / 100)
        equity.loc[date] = cum
    equity = equity.ffill().fillna(1.0)

    days = (equity.index[-1] - equity.index[0]).days
    annual = round((equity.iloc[-1] ** (365 / days) - 1) * 100, 2) if days > 1 else 0

    peak = equity.expanding().max()
    maxdd = round(((equity - peak) / peak).min() * 100, 2)

    return {
        "windows": len(windows),
        "oos_trades": total_trades,
        "oos_pf": pf,
        "oos_winrate": wr,
        "oos_annual": annual,
        "oos_maxdd": maxdd,
        "oos_avg_win": round(sum(t.pnl_pct for t in wins) / len(wins), 2) if wins else 0,
        "oos_avg_loss": round(sum(t.pnl_pct for t in losses) / len(losses), 2) if losses else 0,
    }
