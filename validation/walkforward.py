"""
Walk-Forward 滚动验证 v3.0-fixed
修复：
  [P1-1] 股票池显式化（universe.py），禁止 glob[:20]
  [P0-B] 每个窗口回测都传 index_df（大盘过滤生效）
"""

import json
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

from backtest.engine import BacktestEngine
from data.adjust import get_price
from data.universe import get_universe


def walk_forward(
    symbols: list[str] = None,
    index_symbol: str = None,
    start: str = "2023-01-01",
    end: str = "2026-07-01",
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
    base_params: dict = None,
) -> dict:
    """
    滚动前推验证：训练段调参（可关）→ 验证段参数固定 → 汇总样本外

    Returns:
        {windows, oos_trades, oos_pf, oos_winrate, oos_annual, oos_maxdd, ...}
    """
    if base_params is None:
        with open("config/params.json") as f:
            base_params = json.load(f)

    if symbols is None:
        symbols = get_universe()  # [P1-1] 显式股票池，非 glob 截断

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
    window_results = []
    oos_equity_fragments = []  # [(start_date, equity_series), ...] 测试段净值片段

    for wi, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        print(f"\n窗{wi+1}/{len(windows)}: train={tr_s}→{tr_e} test={te_s}→{te_e}")

        # 验证段数据（含训练段延伸，保证指标 warmup）
        test_data = {}
        for code in symbols:
            df = get_price(code, start=tr_s, end=te_e, days=9999)
            if df is not None and len(df) > 50:
                test_data[code] = df

        if len(test_data) < 5:
            print(f"  验证数据不足({len(test_data)}支)，跳过")
            continue

        # [P0-B] 大盘指数数据（验证段）
        index_df = None
        if index_symbol:
            index_df = get_price(index_symbol, start=tr_s, end=te_e, days=9999)

        # 参数：固定基线（不调参；如需训练段调参，在此实现网格搜索）
        params = base_params.copy()

        engine = BacktestEngine(params=params, trade_cost=0.003)
        result = engine.run(test_data, index_df=index_df)
        s_ = result.summary()

        # 截取测试段净值 (OOS only)
        oos_eq = _extract_oos_equity(result.equity_curve, te_s, te_e)
        if oos_eq is not None and len(oos_eq) >= 2:
            oos_equity_fragments.append((te_s, oos_eq))

        print(f"  样本外: PF={s_['profit_factor']} 胜率={s_['win_rate']}% "
              f"交易={s_['total_trades']}笔 年化={s_['annual_return']}% "
              f"回撤={s_['max_drawdown']}%")

        window_results.append({
            "window": wi + 1, "train": f"{tr_s}→{tr_e}", "test": f"{te_s}→{te_e}",
            **s_,
        })
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

    # ---- 拼接 OOS 净值曲线（每窗独立净值→时间拼接→连续曲线） ----
    annual, maxdd = _compute_oos_metrics(oos_equity_fragments)

    return {
        "windows": len(windows),
        "window_results": window_results,
        "oos_trades": total_trades,
        "oos_pf": pf,
        "oos_winrate": wr,
        "oos_annual": annual,
        "oos_maxdd": maxdd,
        "oos_avg_win": round(sum(t.pnl_pct for t in wins) / len(wins), 2) if wins else 0,
        "oos_avg_loss": round(sum(t.pnl_pct for t in losses) / len(losses), 2) if losses else 0,
    }


def _extract_oos_equity(equity_curve: pd.Series, test_start: str, test_end: str) -> pd.Series:
    """从回测净值曲线中截取测试段（OOS）"""
    if equity_curve is None or equity_curve.empty:
        return None
    mask = (equity_curve.index >= test_start) & (equity_curve.index <= test_end)
    oos = equity_curve[mask]
    if len(oos) < 2:
        return None
    # 归一化到测试段起点=1.0
    return oos / oos.iloc[0]


def _compute_oos_metrics(fragments: list) -> tuple:
    """
    拼接各窗 OOS 净值片段，计算年化收益率和最大回撤。
    
    拼接规则：每窗净值从上一窗终值接续（资本不重置）。
    """
    if not fragments:
        return 0.0, 0.0

    # 按时间排序
    fragments.sort(key=lambda x: x[0])

    # 拼接：上一窗终值 × 当前窗归一化净值
    combined = None
    carry = 1.0  # 累积净值乘数

    for _, eq in fragments:
        scaled = eq * carry
        if combined is None:
            combined = scaled
        else:
            combined = pd.concat([combined, scaled[scaled.index > combined.index[-1]]])
        carry = scaled.iloc[-1]

    if combined is None or len(combined) < 2:
        return 0.0, 0.0

    # 年化收益率
    days = (combined.index[-1] - combined.index[0]).days
    if days > 1:
        annual = round((combined.iloc[-1] ** (365.0 / days) - 1) * 100, 2)
    else:
        annual = 0.0

    # 最大回撤
    peak = combined.expanding().max()
    dd = ((combined - peak) / peak).min()
    maxdd = round(dd * 100, 2)

    return annual, maxdd
