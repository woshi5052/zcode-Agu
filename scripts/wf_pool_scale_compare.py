# -*- coding: utf-8 -*-
"""49池扩容对照回测 (2026-11-02) —— 只出报告, 不改生产、不上线

问题 (用户排队老问题): "选股池很小(49支), 放大会不会更好?"
已答的一半 (09-29 报告): 沪深300低价子集 B 池 PF 1.18 vs A 49池 1.01,
    B 优 +17% < 预注册 30% 门槛 → 未采纳。
本实验补另一半: **全市场低价池** (A 股全市场 ∩ 现价≤¥10, 剔 ST/北交所, 流动性预筛)。

========== 预注册裁决标准 (跑数据前写死, 不得事后修改) ==========
  C池(全市场低价抽样200支, seed=42) 与 A池(49支) 同策略同参数同期对照:
  PASS 条件 (全部满足才进入下一步"全量池+walk-forward"验证, 否则维持49池):
    R1: C 的 PF >= A 的 PF * 1.30   (与 09-29 门槛一致, 防 +17% 式 marginal 改进混入)
    R2: C 的交易笔数 >= 100          (统计功效)
    R3: C 的胜率 >= A 的胜率 - 3pp  (不许以胜率换 PF 的极端不对称)
  口径: 与 09-29 完全一致 (同一引擎/参数/期间 2022-10-01~2026-09-28, 数据缓存复用)。
  局限 (报告须列明): C 池成员取当前快照(生存偏差), 信号日动态≤¥10缓解不消除; 抽样非全量。
"""

import sys, os, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import akshare as ak

from wf_csi300_cheap_compare import (prep, signal_mask, backtest, fetch_daily,
                                     _retry, START, END, P)

SEED = 42
N_SAMPLE = 200
MIN_SPOT_AMT = 30_000_000   # 快照当日成交额≥3000万 预筛 (与信号流动性门禁同数量级)


def universe_c() -> tuple[list[str], dict]:
    """全市场低价池: 全A现货快照 → ≤¥10 + 非ST + 非北交所 + 成交额预筛 → 随机抽样200"""
    spot = _retry(lambda: ak.stock_zh_a_spot_em(), n=3)
    df = spot.copy()
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    # 剔除北交所(4/8开头)与退市占位(9开头)
    df = df[~df["代码"].str.startswith(("4", "8", "9"))]
    # 只留主板+创业板现价≤10
    df = df[df["代码"].str.match(r"^(60|00|30)")]
    df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
    df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce")
    df = df[(df["最新价"] > 0) & (df["最新价"] <= 10)]
    df = df[df["成交额"] >= MIN_SPOT_AMT]
    df = df[~df["名称"].astype(str).str.upper().str.contains("ST")]
    full = df["代码"].tolist()
    picked = df["代码"].sample(n=min(N_SAMPLE, len(full)), random_state=SEED).tolist()
    info = {"n_cheap_all": len(full), "n_sampled": len(picked), "seed": SEED}
    return picked, info


def main():
    print("=" * 60)
    print("  49池扩容对照回测: A(49支) vs C(全市场低价抽样) (只出报告)")
    print(f"  期间: {START} ~ {END} | 参数与 09-29 实验完全一致")
    print("=" * 60)

    c_codes, c_info = universe_c()
    print(f"C池: 全市场≤¥10共 {c_info['n_cheap_all']} 支 → 抽样 {c_info['n_sampled']} 支 (seed={SEED})")

    pools = {"A_49pool": {}, "C_market_cheap": {}}
    plans = [("A_49pool", None),          # 49池代码由缓存/接口自取 (见下)
             ("C_market_cheap", c_codes)]
    # A 池代码
    from data.universe import get_universe
    a_codes = get_universe()
    plans[0] = ("A_49pool", a_codes)

    for key, codes in plans:
        ok = 0
        for i, c in enumerate(codes):
            df = fetch_daily(c)
            if df is not None and len(df) > 250:
                pools[key][c] = df
                ok += 1
            if (i + 1) % 50 == 0:
                print(f"  [{key}] {i+1}/{len(codes)} 成功{ok}", flush=True)
        print(f"[{key}] 数据就绪 {ok}/{len(codes)}")

    res_a = backtest(pools["A_49pool"], price_cap=None, label="A: 49支手动低价池")
    res_c = backtest(pools["C_market_cheap"], price_cap=10.0, label="C: 全市场低价抽样")

    print(f"\nA: 交易{res_a['trades']} 胜率{res_a['winrate']}% PF{res_a['pf']} 均值{res_a['avg_ret']}%")
    print(f"C: 交易{res_c['trades']} 胜率{res_c['winrate']}% PF{res_c['pf']} 均值{res_c['avg_ret']}%")

    # ---- 预注册裁决 ----
    r1 = res_c["pf"] >= res_a["pf"] * 1.30
    r2 = res_c["trades"] >= 100
    r3 = res_c["winrate"] >= res_a["winrate"] - 3
    print("\n===== 预注册裁决 =====")
    print(f"R1 C PF >= A PF*1.30:  {res_c['pf']} vs {res_a['pf']}*1.3={res_a['pf']*1.3:.2f} → {'PASS' if r1 else 'FAIL'}")
    print(f"R2 C 交易数>=100:      {res_c['trades']} → {'PASS' if r2 else 'FAIL'}")
    print(f"R3 C 胜率>=A-3pp:      {res_c['winrate']}% vs {res_a['winrate']}% → {'PASS' if r3 else 'FAIL'}")
    verdict = "PASS" if (r1 and r2 and r3) else "FAIL"
    print(f"\n裁决: {verdict} → " + ("进入下一步 全量低价池 walk-forward 验证" if verdict == "PASS"
                                   else "维持 49 池, 不扩容"))

    now = time.strftime("%Y-%m-%d %H:%M")
    md = f"""# 49池扩容对照回测报告 (2026-11-02)

> 性质: **只出报告, 未上线任何改动**。生产 49 池维持不动。

## 问题
用户排队老问题: "选股池很小(49支), 放大会不会更好?"
09-29 已答一半 (沪深300低价子集 B: 优+17% < 30% 门槛, 未采纳); 本实验补全市场维度。

## 预注册裁决标准 (跑前写死)
- R1: C池 PF >= A池 PF × 1.30
- R2: C池 交易数 >= 100
- R3: C池 胜率 >= A池 胜率 - 3pp
全部满足才进入"全量池+walk-forward"验证; 任一不满足维持 49 池。

## 设置
- 期间 {START} ~ {END} (与 09-29 完全一致, 数据缓存复用); 参数同生产 params.json
- C池: 全A现货快照 ∩ 现价≤¥10 + 非ST + 非北交所 + 当日成交额≥3000万
  = **{c_info['n_cheap_all']}支** → 随机抽样 **{c_info['n_sampled']}支** (seed={SEED}); 信号日动态≤¥10过滤
- 信号/执行: Supertrend+MA20>60+RSI[40,65]+量比≥1.5; T+1开盘买; 3ATR止损/2%下限;
  +8%卖半; 3ATR目标/移动止损/20日到期; 成本0.3%; 冷却5日 (与09-29同引擎)

## 结果

| 指标 | A: 49支低价池 | C: 全市场低价抽样 |
|---|---|---|
| 有效标的 | {res_a['n_stocks']} | {res_c['n_stocks']} |
| 交易笔数 | {res_a['trades']} | {res_c['trades']} |
| 胜率 | {res_a['winrate']}% | {res_c['winrate']}% |
| 盈亏比 PF | {res_a['pf']} | {res_c['pf']} |
| 单笔均值 | {res_a['avg_ret']}% | {res_c['avg_ret']}% |
| 平均盈利笔 | {res_a['avg_win']}% | {res_c['avg_win']}% |
| 平均亏损笔 | {res_a['avg_loss']}% | {res_c['avg_loss']}% |
| 交易序列最大回撤 | {res_a['maxdd']}% | {res_c['maxdd']}% |
| 平均持有 | {res_a['avg_hold']}日 | {res_c['avg_hold']}日 |

## 裁决
- R1: {'PASS' if r1 else 'FAIL'} ({res_c['pf']} vs {res_a['pf']}×1.3={res_a['pf']*1.3:.2f})
- R2: {'PASS' if r2 else 'FAIL'} ({res_c['trades']})
- R3: {'PASS' if r3 else 'FAIL'} ({res_c['winrate']}% vs {res_a['winrate']}%)
- **最终: {verdict} → {'进入下一步' if verdict == 'PASS' else '维持 49 池, 不扩容'}**

## 口径与局限
1. C池成员取**当前现货快照** → 生存偏差 (已退市/ST化的历史成员未含), 对 C 偏乐观;
   信号日收盘≤¥10 动态过滤缓解前视但不消除 → **C 的真实优势只会比报告更低**;
2. 抽样 200 支 (seed={SEED}) 非全量, 单种子存在抽样方差; PASS 情形下需全量复验;
3. 同 09-29: 信号级独立记账, 无组合并发约束; 固定参数全期回放, 不作上线依据。
"""
    with open("reports/wf_pool_scale_compare_2026-11-02.md", "w", encoding="utf-8") as f:
        f.write(md)
    with open("reports/wf_pool_scale_compare_2026-11-02.json", "w", encoding="utf-8") as f:
        json.dump({"built": now, "universe_c_info": c_info,
                   "A": {k: v for k, v in res_a.items() if k != "trades_detail"},
                   "C": {k: v for k, v in res_c.items() if k != "trades_detail"},
                   "rules": {"R1": bool(r1), "R2": bool(r2), "R3": bool(r3)},
                   "verdict": verdict},
                  f, ensure_ascii=False, indent=2)
    print("\n报告已写: reports/wf_pool_scale_compare_2026-11-02.md / .json")


if __name__ == "__main__":
    main()
