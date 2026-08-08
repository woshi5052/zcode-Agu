"""
Walk-Forward 运行入口 v3.0-fixed
修复：股票池显式化 + 传 index_df（大盘过滤生效）
"""

import json
import sys

from validation.walkforward import walk_forward
from data.universe import get_universe

INDEX_SYMBOL = "000300"  # 沪深300


def main():
    symbols = get_universe()
    print(f"股票池: {len(symbols)} 支 (显式 universe，非 glob 截断)")

    with open("config/params.json") as f:
        params = json.load(f)

    result = walk_forward(
        symbols=symbols,
        index_symbol=INDEX_SYMBOL,
        start="2023-01-01",
        end="2026-07-01",
        train_months=12,
        test_months=3,
        step_months=3,
        base_params=params,
    )

    print("\n" + "=" * 60)
    print("  Walk-Forward 样本外结果")
    print("=" * 60)
    if "error" in result:
        print(f"  ❌ {result['error']}")
        return

    print(f"  窗口数:   {result['windows']}")
    print(f"  交易笔数: {result['oos_trades']}")
    print(f"  OOS PF:   {result['oos_pf']}")
    print(f"  OOS 胜率: {result['oos_winrate']}%")
    print(f"  OOS 年化: {result['oos_annual']}%")
    print(f"  OOS 回撤: {result['oos_maxdd']}%")
    print(f"  OOS 均盈: {result['oos_avg_win']}% / 均亏: {result['oos_avg_loss']}%")

    print("\n  逐窗明细:")
    for w in result.get("window_results", []):
        print(f"    W{w['window']}: PF={w['profit_factor']} 交易={w['total_trades']} "
              f"年化={w['annual_return']}% 回撤={w['max_drawdown']}%")

    # 门禁判断
    pf, dd = result["oos_pf"], abs(result["oos_maxdd"])
    print("\n  ---- 门禁判断 ----")
    print(f"  PF ≥ 1.2: {'✅' if pf >= 1.2 else '❌'} ({pf})")
    print(f"  回撤 ≤ 25%: {'✅' if dd <= 25 else '❌'} ({result['oos_maxdd']}%)")
    if pf >= 1.2 and dd <= 25:
        print("  → 门禁通过，可进模拟盘")
    else:
        print("  → 门禁未过，按行动清单继续优化")


if __name__ == "__main__":
    main()
