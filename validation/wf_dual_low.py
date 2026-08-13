"""
双低轮动 Walk-Forward 验证

复用 walkforward.py 的窗口框架, 调用 DualLowEngine 替代 BacktestEngine
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta

from strategies.dual_low import DualLowEngine, CBResult


def walk_forward_dual_low(
    start: str = "2022-01-01",
    end: str = "2025-06-30",
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
    capital: float = 10000.0,
) -> dict:
    """
    双低轮动 Walk-Forward 滚动验证

    每窗: 训练段 warmup(可选) → 测试段(样本外) 独立回测
    """
    # 生成窗口
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    train_len = relativedelta(months=train_months)
    test_len = relativedelta(months=test_months)
    step = relativedelta(months=step_months)

    windows = []
    cursor = s
    while cursor + train_len + test_len <= e:
        test_start = (cursor + train_len).strftime("%Y-%m-%d")
        test_end = (cursor + train_len + test_len).strftime("%Y-%m-%d")
        windows.append((test_start, test_end))
        cursor += step

    print(f"双低WF: {len(windows)}窗 (test={test_months}m / step={step_months}m)")
    print(f"区间: {start} → {end} | 本金: ¥{capital:.0f}")

    all_trades = []
    window_results = []
    oos_equity_fragments = []

    for wi, (te_s, te_e) in enumerate(windows):
        print(f"\n窗{wi+1}/{len(windows)}: test={te_s}→{te_e}")

        t0 = time.time()
        engine = DualLowEngine(capital=capital)
        result = engine.run(te_s, te_e)
        elapsed = time.time() - t0
        s_ = result.summary()

        # 截取测试段净值
        if result.equity_curve is not None and len(result.equity_curve) > 1:
            oos_equity_fragments.append((te_s, result.equity_curve))

        print(f"  OOS: PF={s_['profit_factor']} 胜率={s_['win_rate']}% "
              f"交易={s_['total_trades']}笔 年化={s_['annual_return']}% "
              f"回撤={s_['max_drawdown']}% ({elapsed:.0f}s)")

        window_results.append({
            "window": wi + 1,
            "test": f"{te_s}→{te_e}",
            **s_,
        })
        all_trades.extend(result.trades)

    # ========== 汇总 ==========
    if not all_trades:
        return {"windows": len(windows), "oos_trades": 0, "oos_pf": 0,
                "error": "无样本外交易"}

    total_trades = len(all_trades)
    wins = [t for t in all_trades if t.pnl_pct > 0]
    losses = [t for t in all_trades if t.pnl_pct <= 0]
    wr = round(len(wins) / total_trades * 100, 1) if total_trades > 0 else 0

    tw = sum(t.pnl_pct for t in wins)
    tl = abs(sum(t.pnl_pct for t in losses))
    pf = round(tw / tl, 2) if tl > 0 else 0

    # 拼接OOS净值
    annual, maxdd = _compute_oos(oos_equity_fragments)

    return {
        "windows": len(windows),
        "window_results": window_results,
        "oos_trades": total_trades,
        "oos_pf": pf,
        "oos_winrate": wr,
        "oos_annual": annual,
        "oos_maxdd": maxdd,
        "oos_avg_win": round(np.mean([t.pnl_pct for t in wins]), 2) if wins else 0,
        "oos_avg_loss": round(np.mean([t.pnl_pct for t in losses]), 2) if losses else 0,
    }


def _compute_oos(fragments: list) -> tuple:
    """拼接各窗OOS净值, 计算年化和回撤"""
    if not fragments:
        return 0.0, 0.0

    fragments.sort(key=lambda x: x[0])
    combined = None
    carry = 1.0

    for _, eq in fragments:
        scaled = eq * carry
        if combined is None:
            combined = scaled
        else:
            combined = pd.concat([combined, scaled[scaled.index > combined.index[-1]]])
        carry = scaled.iloc[-1]

    if combined is None or len(combined) < 2:
        return 0.0, 0.0

    days = (combined.index[-1] - combined.index[0]).days
    annual = round((combined.iloc[-1] ** (365.0 / days) - 1) * 100, 2) if days > 1 else 0
    peak = combined.expanding().max()
    maxdd = round(((combined - peak) / peak).min() * 100, 2)
    return annual, maxdd


# ==========================================
# 快速测试
# ==========================================
if __name__ == "__main__":
    result = walk_forward_dual_low(
        start="2022-01-01", end="2025-06-30",
        train_months=12, test_months=3, step_months=3,
    )
    print(f"\n{'='*60}")
    print(f"  WF 汇总")
    print(f"{'='*60}")
    for k, v in result.items():
        if k != "window_results":
            print(f"  {k}: {v}")
