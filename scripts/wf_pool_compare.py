"""
股票池扩容实验: 49支基线 vs 100支新池 — Walk-Forward 样本外对比
纪律: 数据说话, 门禁不过不上线
运行: python scripts/wf_pool_compare.py
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from validation.walkforward import walk_forward
from data.universe import DEFAULT_UNIVERSE

INDEX_SYMBOL = "000300"

def run_wf(symbols, label):
    print(f"\n{'='*60}\n  WF: {label} ({len(symbols)}支)\n{'='*60}", flush=True)
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
    print(f"  耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return result

def summarize(label, r, n_symbols):
    if "error" in r:
        return {"label": label, "error": r["error"]}
    pf, dd, trades = r["oos_pf"], abs(r["oos_maxdd"]), r["oos_trades"]
    gates = {
        "pf_ge_1.2": pf >= 1.2,
        "dd_le_25": dd <= 25,
        "trades_ge_30": trades >= 30,
    }
    print(f"\n  [{label}] OOS: 交易{trades}笔 | PF {pf} | 胜率 {r['oos_winrate']}% | "
          f"年化 {r['oos_annual']}% | 回撤 {r['oos_maxdd']}% | 门禁 {gates}", flush=True)
    return {
        "label": label, "n_symbols": n_symbols,
        "oos_trades": trades, "oos_pf": pf, "oos_winrate": r["oos_winrate"],
        "oos_annual": r["oos_annual"], "oos_maxdd": r["oos_maxdd"],
        "oos_avg_win": r["oos_avg_win"], "oos_avg_loss": r["oos_avg_loss"],
        "gates": gates, "windows": r.get("windows"),
        "window_results": r.get("window_results", []),
    }

if __name__ == "__main__":
    with open("config/params.json") as f:
        params = json.load(f)
    with open("data/pool_100.json", encoding="utf-8") as f:
        pool100 = json.load(f)["pool"]

    results = []
    if "--only100" in sys.argv:
        # 基线结果来自 2026-09-22 13:26 的完整运行 (d6d97ab同期数据)
        results.append({
            "label": "pool49_baseline", "n_symbols": len(DEFAULT_UNIVERSE),
            "oos_trades": 86, "oos_pf": 2.91, "oos_winrate": 52.3,
            "oos_annual": 0.38, "oos_maxdd": -1.8,
            "gates": {"pf_ge_1.2": True, "dd_le_25": True, "trades_ge_30": True},
            "note": "来自已完成的基线WF运行",
        })
    else:
        results.append(summarize("pool49_baseline", run_wf(sorted(DEFAULT_UNIVERSE), "49支基线池"), len(DEFAULT_UNIVERSE)))
    results.append(summarize("pool100_new", run_wf(pool100, "100支扩容池"), len(pool100)))

    os.makedirs("reports", exist_ok=True)
    with open("reports/wf_pool100_compare.json", "w", encoding="utf-8") as f:
        json.dump({"built": "2026-09-22", "rule": "主板+非ST+2~10元+流动性Top",
                   "period": "WF 2022-01-01~2025-06-30, 12M训练/3M测试/3M步进",
                   "results": results}, f, ensure_ascii=False, indent=1, default=str)

    # 对比结论
    a, b = results[0], results[1]
    if "error" not in a and "error" not in b:
        print(f"\n{'='*60}\n  对比结论\n{'='*60}")
        rows = [("OOS PF", a["oos_pf"], b["oos_pf"]), ("OOS 年化%", a["oos_annual"], b["oos_annual"]),
                ("OOS 胜率%", a["oos_winrate"], b["oos_winrate"]),
                ("OOS 回撤%", a["oos_maxdd"], b["oos_maxdd"]), ("OOS 交易笔数", a["oos_trades"], b["oos_trades"])]
        print(f"  {'指标':<12}{'49支基线':<14}{'100支新池':<14}{'变化'}")
        for name, va, vb in rows:
            print(f"  {name:<12}{va:<14}{vb:<14}{'↑' if vb > va else ('↓' if vb < va else '→')}")
        all_gates = all(b["gates"].values())
        print(f"\n  100支池门禁: {'✅ 全部通过, 可上线' if all_gates else '❌ 未全过, 不上线'}")
    print("\n结果已存 reports/wf_pool100_compare.json")
