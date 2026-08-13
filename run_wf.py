"""
Walk-Forward 运行入口 v3.0 — 含极端行情依赖分析
"""
import json, sys, time
import numpy as np

from validation.walkforward import walk_forward
from data.universe import get_universe

INDEX_SYMBOL = "000300"  # 沪深300


def main():
    symbols = get_universe()
    print(f"股票池: {len(symbols)} 支")

    with open("config/params.json") as f:
        params = json.load(f)

    t0 = time.time()

    result = walk_forward(
        symbols=symbols,
        index_symbol=INDEX_SYMBOL,
        start="2022-01-01",
        end="2025-06-30",
        train_months=12,
        test_months=3,
        step_months=3,
        base_params=params,
    )

    elapsed = time.time() - t0

    print("\n" + "=" * 60)
    print(f"  Walk-Forward 完成 ({elapsed/60:.0f}分钟)")
    print("=" * 60)

    if "error" in result:
        print(f"  ❌ {result['error']}")
        return

    print(f"  窗口数:   {result['windows']}")
    print(f"  OOS交易:  {result['oos_trades']}笔")
    print(f"  OOS PF:   {result['oos_pf']}")
    print(f"  OOS 胜率: {result['oos_winrate']}%")
    print(f"  OOS 年化: {result['oos_annual']}%")
    print(f"  OOS 回撤: {result['oos_maxdd']}%")
    print(f"  OOS 均盈: {result['oos_avg_win']}% / 均亏: {result['oos_avg_loss']}%")

    # 逐窗表
    print(f"\n  {'窗':<4}{'测试区间':<24}{'笔数':<6}{'PF':<8}{'胜率%':<8}{'年化%':<10}{'回撤%':<8}{'持仓天'}")
    print(f"  {'-'*80}")
    for w in result.get("window_results", []):
        print(f"  W{w['window']:<3}{w['test']:<24}{w['total_trades']:<6}"
              f"{w['profit_factor']:<8}{w['win_rate']:<8}"
              f"{w['annual_return']:<10}{w['max_drawdown']:<8}"
              f"{w['avg_hold_days']}")

    # ---- 门禁 ----
    pf, dd = result["oos_pf"], abs(result["oos_maxdd"])
    trades = result["oos_trades"]
    print(f"\n  ---- 门禁 ----")
    print(f"  PF≥1.2: {'✅' if pf >= 1.2 else '❌'} ({pf})")
    print(f"  回撤≤25%: {'✅' if dd <= 25 else '❌'} ({result['oos_maxdd']}%)")
    print(f"  交易≥30: {'✅' if trades >= 30 else '❌'} ({trades})")

    # ---- 极端行情分析 ----
    print(f"\n{'='*60}")
    print(f"  极端行情依赖: 去掉含 2024-09 的窗口")
    print(f"{'='*60}")

    sep_wins = []
    non_sep_wins = []
    for w in result.get("window_results", []):
        test = w["test"]  # e.g. "2024-07-01→2024-10-01"
        if "2024-09" in test or "2024-08" in test or "2024-10" in test:
            sep_wins.append(w)
        else:
            non_sep_wins.append(w)

    if sep_wins:
        print(f"  含2024-09窗口: {len(sep_wins)}")
        for w in sep_wins:
            print(f"    W{w['window']}: {w['test']} PF={w['profit_factor']} "
                  f"交易={w['total_trades']} 年化={w['annual_return']}%")

    if non_sep_wins:
        total_t = sum(w["total_trades"] for w in non_sep_wins)
        # 把剩余窗口等权合算（近似）
        avg_pf = np.mean([w["profit_factor"] for w in non_sep_wins]) if non_sep_wins else 0
        avg_ret = np.mean([w["annual_return"] for w in non_sep_wins]) if non_sep_wins else 0
        print(f"  不含2024-09: {len(non_sep_wins)}窗 {total_t}笔 "
              f"加权PF≈{avg_pf:.1f} 均年化≈{avg_ret:.1f}%")


if __name__ == "__main__":
    main()
