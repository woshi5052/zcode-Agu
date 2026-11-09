# -*- coding: utf-8 -*-
"""P0-2 闸门事件化增强回测 (预注册裁决版)

========== 预注册裁决标准 (跑数据前写死, 不得事后修改) ==========

事件集 (4 个独立信号, 每日投票):
  E1 均线信号 (现有): 收盘 < MA20*0.98
  E2 恐慌事件:      近 5 日内存在单日跌幅 <= -2%, 且当前收盘未收复该暴跌日收盘价
  E3 新低事件:      收盘创 20 日新低
  E4 量能事件:      5日均量 < 20日均量*0.8 且 收盘 < MA20 (缩量无承接)

策略对比 (指数级别近似: 开闸=持有沪深300, 关闸=现金, 收盘决策次日生效):
  S0 基准:   不设闸门, 始终持有
  S1 现行:   E1 单指标 (close < MA20*0.98 空仓)
  S2 事件化: votes >= 2 空仓 (E1/E2/E3/E4 投票)

裁决规则 (全部满足才采纳 S2 进入下一阶段 49 池回测; 任一不满足维持 S1):
  R1: S2 年化收益 >= S1 年化收益 + 2 个百分点
  R2: S2 最大回撤 <= S1 最大回撤 + 2 个百分点
  R3: S2 空仓避险期的"错杀率"可解释: S2 关闸日中, 次日实际上涨比例 <= 55%
      (避免闸门关太死导致长期踏空)
样本: 沪深300 指数 2022-01-01 至最新 (覆盖 2022 熊 / 2024 924行情 / 2025-2026 震荡熊)
"""

import sys
import time

import akshare as ak
import numpy as np
import pandas as pd

START = "2022-01-01"


def _retry(func, n=3, wait=2):
    for i in range(n):
        try:
            return func()
        except Exception as e:
            print(f"  [retry {i+1}/{n}] {str(e)[:80]}")
            if i < n - 1:
                time.sleep(wait)
    return None


def load_index() -> pd.DataFrame:
    df = _retry(lambda: ak.stock_zh_index_daily(symbol="sh000300"))
    if df is None or len(df) < 100:
        print("FATAL: 指数数据获取失败")
        sys.exit(1)
    df = df[df["date"] >= pd.Timestamp(START).date()].reset_index(drop=True)
    return df


def build_events(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ma20"] = out["close"].rolling(20).mean()
    out["pct"] = out["close"].pct_change() * 100
    out["ma5_vol"] = out["volume"].rolling(5).mean()
    out["ma20_vol"] = out["volume"].rolling(20).mean()

    # E1 均线信号
    out["E1"] = (out["close"] < out["ma20"] * 0.98).astype(int)

    # E2 恐慌事件: 近5日内有暴跌(<=-2%)且未收复该日收盘
    crash = out["pct"] <= -2.0
    e2 = np.zeros(len(out), dtype=int)
    closes = out["close"].values
    for t in range(20, len(out)):
        for k in range(1, 6):  # 近 1~5 日前
            j = t - k
            if crash.iloc[j] and closes[t] < closes[j]:
                e2[t] = 1
                break
    out["E2"] = e2

    # E3 新低事件
    out["E3"] = (out["close"] <= out["close"].rolling(20).min()).astype(int)

    # E4 缩量无承接
    out["E4"] = ((out["ma5_vol"] < out["ma20_vol"] * 0.8)
                 & (out["close"] < out["ma20"])).astype(int)

    out["votes"] = out[["E1", "E2", "E3", "E4"]].sum(axis=1)
    return out


def simulate(df: pd.DataFrame, gate: pd.Series, label: str) -> dict:
    """收盘决策次日生效: 第 t 日关闸 → t+1 日收益记 0 (现金)"""
    ret = df["close"].pct_change().fillna(0).values
    gate_arr = gate.values
    strat = np.zeros(len(df))
    open_rets = []  # 开闸日实际指数收益 (用于 PF)
    missed_up = total_closed = 0  # 错杀率统计
    for t in range(1, len(df)):
        if not gate_arr[t - 1]:  # 前一日开闸 → 今日持有指数
            strat[t] = ret[t]
            open_rets.append(ret[t])
        else:  # 前一日关闸 → 空仓
            total_closed += 1
            if ret[t] > 0:
                missed_up += 1
    equity = np.cumprod(1 + strat)
    years = (pd.to_datetime(df["date"].iloc[-1])
             - pd.to_datetime(df["date"].iloc[0])).days / 365.25
    total_ret = equity[-1] - 1
    cagr = equity[-1] ** (1 / years) - 1
    peak = np.maximum.accumulate(equity)
    mdd = float(((equity - peak) / peak).min())
    orr = np.array(open_rets) if open_rets else np.array([0.0])
    pf = float(orr[orr > 0].sum() / abs(orr[orr < 0].sum())) if (orr < 0).any() else float("inf")
    return {
        "label": label, "total_ret": total_ret, "cagr": cagr, "mdd": mdd,
        "pf": pf, "closed_pct": total_closed / (len(df) - 1),
        "miss_rate": missed_up / max(total_closed, 1),
        "equity": equity, "gate": gate_arr,
    }


def main():
    df = load_index()
    print(f"样本: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}, 共 {len(df)} 个交易日\n")

    df = build_events(df)
    valid = df.iloc[25:].reset_index(drop=True)  # 跳过均线暖机期

    s0 = simulate(valid, pd.Series(0, index=valid.index), "S0 不设闸门")
    s1 = simulate(valid, valid["E1"], "S1 现行单指标(E1)")
    s2 = simulate(valid, (valid["votes"] >= 2).astype(int), "S2 事件化投票(>=2/4)")

    print(f"\n{'策略':<22}{'总收益':>9}{'年化':>9}{'最大回撤':>10}{'开闸日PF':>9}{'空仓占比':>9}{'错杀率':>8}")
    for s in (s0, s1, s2):
        print(f"{s['label']:<22}{s['total_ret']*100:>8.1f}%{s['cagr']*100:>8.1f}%"
              f"{s['mdd']*100:>9.1f}%{s['pf']:>9.2f}{s['closed_pct']*100:>8.1f}%"
              f"{s['miss_rate']*100:>7.1f}%")

    # ---- 预注册裁决 (标准见文件头, 不得修改) ----
    r1 = s2["cagr"] - s1["cagr"]
    r2 = s2["mdd"] <= s1["mdd"] + 0.02
    r3 = s2["miss_rate"] <= 0.55
    print(f"\n===== 预注册裁决 =====")
    print(f"R1 年化提升>=2pp:  S2-S1 = {r1*100:+.2f}pp → {'PASS' if r1 >= 0.02 else 'FAIL'}")
    print(f"R2 回撤不恶化>2pp: {s2['mdd']*100:.1f}% vs {s1['mdd']*100:.1f}%+2pp → {'PASS' if r2 else 'FAIL'}")
    print(f"R3 错杀率<=55%:    {s2['miss_rate']*100:.1f}% → {'PASS' if r3 else 'FAIL'}")
    verdict = "PASS" if (r1 >= 0.02 and r2 and r3) else "FAIL"
    print(f"\n裁决: {verdict} → "
          + ("进入下一阶段 49 池叠加回测" if verdict == "PASS" else "维持现行单指标闸门, 不上线"))

    # 事件触发频率参考
    print(f"\n事件触发频率: E1 {valid['E1'].mean()*100:.0f}% | E2 {valid['E2'].mean()*100:.0f}% "
          f"| E3 {valid['E3'].mean()*100:.0f}% | E4 {valid['E4'].mean()*100:.0f}% "
          f"| votes>=2 { (valid['votes']>=2).mean()*100:.0f}%")

    # 保存结果
    import json
    result = {
        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "sample": [str(valid["date"].iloc[0]), str(valid["date"].iloc[-1])],
        "strategies": {s["label"]: {k: (round(v, 4) if isinstance(v, float) else v)
                                    for k, v in s.items() if k not in ("equity", "gate")}
                       for s in (s0, s1, s2)},
        "rules": {"R1_cagr_diff_pp": round(r1 * 100, 2), "R2_mdd_ok": bool(r2),
                  "R3_miss_rate": round(s2["miss_rate"], 4)},
        "verdict": verdict,
    }
    with open("reports/gate_event_backtest.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n结果已存 reports/gate_event_backtest.json")


if __name__ == "__main__":
    main()
