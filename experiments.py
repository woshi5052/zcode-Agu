"""优化实验：A(量比1.5) / B(锁利8%) 各跑 WF"""
import json, sys, time
import numpy as np

from validation.walkforward import walk_forward
from data.universe import get_universe

INDEX_SYMBOL = "000300"


def run_exp(label, config_path):
    with open(config_path) as f:
        params = json.load(f)

    symbols = get_universe()
    t0 = time.time()
    result = walk_forward(
        symbols=symbols,
        index_symbol=INDEX_SYMBOL,
        start="2022-01-01", end="2025-06-30",
        train_months=12, test_months=3, step_months=3,
        base_params=params,
    )

    elapsed = time.time() - t0
    if "error" in result:
        print(f"  ❌ {result['error']}")
        return None

    # 不含2024-09分析
    sep_wins = []
    non_sep_wins = []
    for w in result.get("window_results", []):
        test = w["test"]
        if "2024-09" in test or "2024-08" in test or "2024-10" in test:
            sep_wins.append(w)
        else:
            non_sep_wins.append(w)

    return {
        "label": label,
        "elapsed": elapsed,
        "oos_pf": result["oos_pf"],
        "oos_annual": result["oos_annual"],
        "oos_maxdd": result["oos_maxdd"],
        "oos_winrate": result["oos_winrate"],
        "oos_trades": result["oos_trades"],
        "oos_avg_win": result["oos_avg_win"],
        "oos_avg_loss": result["oos_avg_loss"],
        "non_sep_pf": round(np.mean([w["profit_factor"] for w in non_sep_wins]), 2) if non_sep_wins else 0,
        "non_sep_annual": round(np.mean([w["annual_return"] for w in non_sep_wins]), 2) if non_sep_wins else 0,
        "windows": result["windows"],
        "window_results": result.get("window_results", []),
        "sep_count": len(sep_wins),
        "non_sep_count": len(non_sep_wins),
    }


def main():
    # 基线（已跑过，直接硬编码）
    baseline = {
        "label": "基线(v3.0)",
        "oos_pf": 2.42, "oos_annual": 0.84, "oos_maxdd": -2.56,
        "oos_winrate": 45.5, "oos_trades": 112,
        "oos_avg_win": 8.44, "oos_avg_loss": -2.92,
        "non_sep_pf": 1.8, "non_sep_annual": 0.2,
    }

    results = [baseline]

    for label, config_path in [
        ("A:量比≥1.5", "config/params_expA.json"),
        ("B:锁利≥8%", "config/params_expB.json"),
    ]:
        print(f"\n{'='*60}")
        print(f"  实验: {label}")
        print(f"{'='*60}")
        r = run_exp(label, config_path)
        if r:
            results.append(r)

    # 对比表
    print(f"\n{'='*60}")
    print(f"  实验对比")
    print(f"{'='*60}")
    print(f"  {'实验':<16}{'OOS PF':<10}{'年化%':<10}{'回撤%':<10}{'胜率%':<10}{'笔数':<8}{'不含9月PF':<12}{'不含9月年化%'}")
    print(f"  {'-'*80}")
    for r in results:
        print(f"  {r['label']:<16}{r.get('oos_pf',0):<10}{r.get('oos_annual',0):<10}"
              f"{r.get('oos_maxdd',0):<10}{r.get('oos_winrate',0):<10}"
              f"{r.get('oos_trades',0):<8}"
              f"{r.get('non_sep_pf',0):<12}{r.get('non_sep_annual',0):<12}")

    # 判定
    print(f"\n  ---- 优化判定 ----")
    for r in results[1:]:  # skip baseline
        annual = r.get("oos_annual", 0)
        pf = r.get("oos_pf", 0)
        dd = abs(r.get("oos_maxdd", 0))
        status = "✅ 达标" if annual >= 2 and pf >= 1.2 and dd <= 25 else "❌ 未达标"
        print(f"  {r['label']}: 年化={annual}% {'≥2%' if annual>=2 else '<2%'} → {status}")


if __name__ == "__main__":
    main()
