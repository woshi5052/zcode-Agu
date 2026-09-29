# -*- coding: utf-8 -*-
"""
沪深300低价子集对照回测 (2026-09-29) —— 只出报告, 不改生产、不上线
问题: 49支手动低价池 (A) vs 沪深300成分股∩信号日收盘≤¥10 (B), 同策略同参数谁强?
背景: 09-29 演练发现 get_universe 缓存污染隐患已修; 此前 49→100 扩池实验 PF 2.91→1.17,
      用户排队问题: "用沪深300低价子集才是最优对吗?" 本实验用数据回答。

方法: 信号级回测, 两池完全同规则同参数 (生产 params.json 实值)。
信号 (四层过滤, 与生产记忆口径一致):
  1. Supertrend(14, 3.0) 多头状态
  2. close > MA20 > MA60
  3. RSI14 ∈ [40, 65]
  4. 量比 >= 1.5 (当日量 / 前5日均量)
执行 (执行单语义, 与推送给用户的话术一致):
  T收盘出信号 → T+1开盘买入; 入场当日不可卖 (T+1制度)
  初始止损 = max(3×ATR14, 2%); +8% 卖一半; 剩余 3×ATR 目标价 / 3×ATR移动止损 / 20日到期平仓
  成本: 来回 0.3%; 同股同时只持1笔; 平仓后冷却5日
口径与简化 (报告须列明):
  - B池成员取当前快照 (生存偏差), 但信号日要求当日收盘≤¥10 缓解前视
  - SellEngine 分段减仓/评分卖出未复刻, 以执行单语义近似
  - 信号级独立记账, 不做组合层面仓位并发约束
运行: cd /d/ashare-quant && python scripts/wf_csi300_cheap_compare.py
输出: reports/wf_csi300_cheap_compare_2026-09-29.md / .json
"""
import sys, os, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import akshare as ak

from data.universe import get_universe

P = json.load(open("config/params.json", encoding="utf-8"))
ATR_N, ST_M = P["atr_period"], P["st_multiplier"]
MA_S, MA_L = P["ma_short"], P["ma_long"]
RSI_LO, RSI_HI = P["rsi_threshold"], P["rsi_upper"]
VR_MIN, BRK = P["volume_ratio"], P["breakout_days"]
STOP_M, MAXH = P["atr_stop_multiplier"], P["max_holding_days"]
COST, MIN_AMT, COOL = P["trade_cost"], P["min_avg_amount"], P["cooldown_days"]
TP1_PCT, TP1_RATIO = 8.0, 0.5

START, END = "2022-10-01", "2026-09-28"   # 前段为指标预热
START_C, END_C = START.replace("-", ""), END.replace("-", "")  # akshare 要 yyyymmdd
CACHE = "data/cache_wf"
os.makedirs(CACHE, exist_ok=True)


# ---------------- 数据 ----------------
def _retry(fn, n=3, wait=1.5):
    """网络调用统一重试 (代理抖动/东财限频)"""
    last = None
    for a in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (a + 1))
    raise last


def fetch_daily(code: str) -> pd.DataFrame | None:
    fp = f"{CACHE}/{code}.csv"
    if os.path.exists(fp):
        try:
            df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
            if len(df) > 60:
                return df
        except Exception:
            pass
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=_fmt(code), adjust="qfq",
                                     start_date=START_C, end_date=END_C)
            if df is not None and len(df) > 60:
                break
        except Exception:
            df = None
            time.sleep(1.5 * (attempt + 1))
    if df is None or len(df) <= 60:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated()][["open", "high", "low", "close", "volume"]].astype(float)
    df.to_csv(fp)
    time.sleep(0.3)
    return df


def _fmt(code: str) -> str:
    return ("sh" if code.startswith(("6", "9", "5")) else ("bj" if code.startswith(("4", "8")) else "sz")) + code


def universe_a() -> list[str]:
    return get_universe()


def universe_b() -> tuple[list[str], dict]:
    """沪深300成分; 优先现价快照预筛≤¥10, 失败退回全成分(信号日动态≤10过滤兜底)"""
    info = {}
    try:
        cons = _retry(lambda: ak.index_stock_cons_csindex(symbol="000300"), n=2)
        codes = cons["成分券代码"].astype(str).str.zfill(6).tolist()
        src = "csindex"
    except Exception as e1:
        print(f"[WARN] csindex成分接口失败({str(e1)[:40]}), 退回 index_stock_cons")
        cons = _retry(lambda: ak.index_stock_cons(symbol="000300"), n=2)
        col = [c for c in cons.columns if "代码" in c][0]
        codes = cons[col].astype(str).str.zfill(6).tolist()
        src = "cons"
    info["n_cons"] = len(codes)
    try:
        spot = _retry(lambda: ak.stock_zh_a_spot_em(), n=2)
        px = dict(zip(spot["代码"].astype(str).str.zfill(6), spot["最新价"]))
        nm = dict(zip(spot["代码"].astype(str).str.zfill(6), spot["名称"].astype(str)))
        picked = []
        for c in codes:
            p = px.get(c)
            if p is None or pd.isna(p) or p <= 0 or p > 10:
                continue
            if "ST" in nm.get(c, "").upper():
                continue
            picked.append(c)
        info.update({"src": src, "mode": "spot_prefilter", "n_cheap": len(picked)})
        return picked, info
    except Exception as e2:
        print(f"[WARN] 现价快照失败({str(e2)[:60]}) → 全成分拉取, 信号日动态≤¥10过滤兜底")
        info.update({"src": src, "mode": "full_cons_dynamic"})
        return codes, info


# ---------------- 指标与信号 ----------------
def prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l = d["close"], d["high"], d["low"]
    d["ma20"] = c.rolling(MA_S).mean()
    d["ma60"] = c.rolling(MA_L).mean()
    # RSI14 (Wilder)
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    d["vr"] = d["volume"] / d["volume"].shift(1).rolling(5).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(ATR_N).mean()
    # Supertrend
    hl2 = (h + l) / 2
    ub, lb = hl2 + ST_M * d["atr"], hl2 - ST_M * d["atr"]
    n = len(d)
    fub, flb, direction = ub.copy(), lb.copy(), np.ones(n)
    fub.iloc[0], flb.iloc[0] = ub.iloc[0], lb.iloc[0]
    direction[0] = 1
    cc = c.values
    for i in range(1, n):
        fub.iloc[i] = ub.iloc[i] if (ub.iloc[i] < fub.iloc[i-1] or cc[i-1] > fub.iloc[i-1]) else fub.iloc[i-1]
        flb.iloc[i] = lb.iloc[i] if (lb.iloc[i] > flb.iloc[i-1] or cc[i-1] < flb.iloc[i-1]) else flb.iloc[i-1]
        if cc[i] > fub.iloc[i-1]:
            direction[i] = 1
        elif cc[i] < flb.iloc[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
    d["st_dir"] = direction
    d["avg_amt"] = (c * d["volume"]).rolling(20).mean()
    return d


def signal_mask(d: pd.DataFrame, price_cap: float | None) -> pd.Series:
    m = (
        (d["st_dir"] == 1)
        & (d["close"] > d["ma20"]) & (d["ma20"] > d["ma60"])
        & (d["rsi"] >= RSI_LO) & (d["rsi"] <= RSI_HI)
        & (d["vr"] >= VR_MIN)
        & (d["avg_amt"] >= MIN_AMT)
        & (d["atr"] > 0)
    )
    if price_cap is not None:
        m &= d["close"] <= price_cap
    return m.fillna(False)


# ---------------- 回测 ----------------
def backtest(pool: dict[str, pd.DataFrame], price_cap: float | None, label: str) -> dict:
    trades = []
    for code, d0 in pool.items():
        d = prep(d0)
        mask = signal_mask(d, price_cap)
        idx = d.index
        last_exit_i = -10**9
        sigs = [i for i in range(len(d)) if mask.iloc[i]]
        for T in sigs:
            e = T + 1                      # T+1 开盘买
            if e >= len(d) or e <= last_exit_i + COOL:
                continue
            entry = float(d["open"].iloc[e])
            atr_e = float(d["atr"].iloc[T])
            if entry <= 0 or atr_e <= 0 or np.isnan(atr_e):
                continue
            stop = entry - max(STOP_M * atr_e, 0.02 * entry)
            tp1 = entry * (1 + TP1_PCT / 100)
            tp2 = entry + STOP_M * atr_e
            sold1 = False
            r1 = r2 = None
            exit_j = None
            trail = stop
            for j in range(e + 1, min(e + MAXH, len(d))):   # 入场日不可卖
                o, h, l, c = (float(d[k].iloc[j]) for k in ("open", "high", "low", "close"))
                # 剩余仓止损线随最高收盘上移 (3ATR, 入场ATR为尺)
                trail = max(trail, c - STOP_M * atr_e) if c > trail + STOP_M * atr_e else trail
                if not sold1 and h >= tp1:
                    px1 = tp1 if o < tp1 else o        # 跳空高开按开盘
                    r1 = px1 / entry - 1
                    sold1 = True
                if sold1:
                    stop_now = max(stop, trail)
                    if l <= stop_now:
                        px2 = stop_now if o > stop_now else o
                        r2 = px2 / entry - 1
                        exit_j = j
                        break
                    if h >= tp2:
                        px2 = tp2 if o < tp2 else o
                        r2 = px2 / entry - 1
                        exit_j = j
                        break
                else:
                    if l <= stop:                       # 未到TP1先破初始止损 → 全走
                        px = stop if o > stop else o
                        r1 = r2 = px / entry - 1
                        exit_j = j
                        break
            else:
                j = min(e + MAXH, len(d)) - 1
                px = float(d["close"].iloc[j])
                r2 = px / entry - 1
                if not sold1:
                    r1 = r2
                exit_j = j
            if r1 is None:                              # 持满未触发TP1也未破位
                r1 = float(d["close"].iloc[exit_j]) / entry - 1
            ret = TP1_RATIO * r1 + (1 - TP1_RATIO) * r2 - COST
            trades.append({
                "code": code, "entry": str(idx[e].date()), "exit": str(idx[exit_j].date()),
                "entry_px": round(entry, 2), "ret": round(ret * 100, 2),
                "hold_days": exit_j - e,
            })
            last_exit_i = exit_j
    # 指标
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf") if wins else 0
    eq = np.cumprod([1 + r / 100 for r in rets]) if rets else np.array([1.0])
    peak = np.maximum.accumulate(eq)
    mdd = float(((eq / peak) - 1).min() * 100) if len(eq) > 1 else 0.0
    return {
        "label": label, "n_stocks": len(pool), "trades": len(trades),
        "winrate": round(len(wins) / len(rets) * 100, 1) if rets else 0,
        "pf": round(pf, 2) if pf != float("inf") else 99.0,
        "avg_ret": round(float(np.mean(rets)), 2) if rets else 0,
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0,
        "maxdd": round(mdd, 1),
        "avg_hold": round(float(np.mean([t["hold_days"] for t in trades])), 1) if rets else 0,
        "trades_detail": trades,
    }


# ---------------- 主流程 ----------------
def main():
    print("=" * 60)
    print("  沪深300低价子集对照回测 (只出报告, 不上线)")
    print(f"  参数: ATR{ATR_N}×{ST_M} | MA{MA_S}/{MA_L} | RSI[{RSI_LO},{RSI_HI}] "
          f"| 量比≥{VR_MIN} | 止损{STOP_M}ATR/2% | TP1+{TP1_PCT}%半仓 | 持有≤{MAXH}日 | 成本{COST:.1%}")
    print("=" * 60)

    a_codes = universe_a()
    b_codes, b_info = universe_b()
    b_desc = (f"沪深300({b_info['n_cons']}支)∩现价≤10 = {b_info.get('n_cheap','?')}支 [{b_info['src']}]"
              if b_info.get("mode") == "spot_prefilter"
              else f"沪深300全成分{b_info['n_cons']}支, 信号日动态≤¥10过滤 [{b_info['src']}]")
    print(f"A池: 49支手动低价池 | B池: {b_desc}")

    pools = {"A_49pool": {}, "B_csi300_cheap": {}}
    for key, codes in (("A_49pool", a_codes), ("B_csi300_cheap", b_codes)):
        ok = 0
        for i, c in enumerate(codes):
            df = fetch_daily(c)
            if df is not None and len(df) > 250:
                pools[key][c] = df
                ok += 1
            if (i + 1) % 20 == 0:
                print(f"  [{key}] {i+1}/{len(codes)} 成功{ok}", flush=True)
        print(f"[{key}] 数据就绪 {ok}/{len(codes)}")

    res_a = backtest(pools["A_49pool"], price_cap=None, label="A: 49支手动低价池")
    res_b = backtest(pools["B_csi300_cheap"], price_cap=10.0, label="B: 沪深300∩信号日≤¥10")
    print(f"\nA: 交易{res_a['trades']} 胜率{res_a['winrate']}% PF{res_a['pf']} 均值{res_a['avg_ret']}% 回撤{res_a['maxdd']}%")
    print(f"B: 交易{res_b['trades']} 胜率{res_b['winrate']}% PF{res_b['pf']} 均值{res_b['avg_ret']}% 回撤{res_b['maxdd']}%")

    # 报告
    now = time.strftime("%Y-%m-%d %H:%M")
    gates_a = {"pf_ge_1.2": res_a["pf"] >= 1.2, "trades_ge_20": res_a["trades"] >= 20}
    gates_b = {"pf_ge_1.2": res_b["pf"] >= 1.2, "trades_ge_20": res_b["trades"] >= 20}
    verdict = []
    verdict.append(f"- A池门禁: {'✅' if all(gates_a.values()) else '❌'} {gates_a}")
    verdict.append(f"- B池门禁: {'✅' if all(gates_b.values()) else '❌'} {gates_b}")
    better = "A" if (res_a["pf"], res_a["winrate"]) >= (res_b["pf"], res_b["winrate"]) else "B"
    verdict.append(f"- 信号质量占优: **{better}池** (PF与胜率综合)")

    b_line = (f"- B池: 沪深300成分股{b_info['n_cons']}支 ∩ 现价≤¥10 = **{b_info.get('n_cheap','?')}支** (csindex成分+东财现价快照, 剔ST)"
              if b_info.get("mode") == "spot_prefilter" else
              f"- B池: 沪深300全成分 **{b_info['n_cons']}支** 全量拉取, 信号日动态过滤收盘≤¥10 (现价快照接口不可用, 回退模式; ST股未剔除, 由流动性门禁部分约束)")

    md = f"""# 沪深300低价子集对照回测报告 (2026-09-29)

> 性质: **只出报告, 未上线任何改动**。生产推送/模拟盘/回测池维持49支手动低价池不动。

## 问题
用户排队问题: "回测用沪深300(低价子集)才是最优对吗?" —— 用同策略同参数对照回答。

## 设置
- 期间: {START} ~ {END} (前段指标预热), 信号日动态过滤
- 参数(生产 params.json 实值): ATR{ATR_N}×{ST_M}, MA{MA_S}/{MA_L}, RSI[{RSI_LO},{RSI_HI}], 量比≥{VR_MIN}, 止损{STOP_M}ATR(下限2%), TP1 +{TP1_PCT}%卖{TP1_RATIO:.0%}, 剩余{STOP_M}ATR目标/移动止损/到期{MAXH}日, 成本来回{COST:.1%}, 同股冷却{COOL}日
- A池: 49支手动低价池 (与生产/模拟盘同池)
{b_line}

## 结果

| 指标 | A: 49支低价池 | B: 沪深300低价子集 |
|---|---|---|
| 有效标的 | {res_a['n_stocks']} | {res_b['n_stocks']} |
| 交易笔数 | {res_a['trades']} | {res_b['trades']} |
| 胜率 | {res_a['winrate']}% | {res_b['winrate']}% |
| 盈亏比 PF | {res_a['pf']} | {res_b['pf']} |
| 单笔均值 | {res_a['avg_ret']}% | {res_b['avg_ret']}% |
| 平均盈利笔 | {res_a['avg_win']}% | {res_b['avg_win']}% |
| 平均亏损笔 | {res_a['avg_loss']}% | {res_b['avg_loss']}% |
| 交易序列最大回撤 | {res_a['maxdd']}% | {res_b['maxdd']}% |
| 平均持有 | {res_a['avg_hold']}日 | {res_b['avg_hold']}日 |

## 门禁与结论
{chr(10).join(verdict)}

## 口径与局限 (重要)
1. B池成员用**当前成分快照** → 存在生存偏差 (退市/调出的历史成员未含), 对B偏乐观; 信号日"收盘≤¥10"动态过滤缓解前视但不消除。
2. SellEngine 的分段减仓/评分卖出未复刻, 出场按**执行单语义**近似 (与推送给用户的话术一致)。
3. 信号级独立记账, 未做组合层面并发/资金约束; A股T+1已按"入场日不可卖"执行。
4. 与生产回测(walk-forward带门禁)不同, 本实验为**固定参数全期信号回放**, 用于池间横向对照, 不作为上线依据。

## 建议
- 若 A 优: 维持现状, 49池不动 (与09-22决策一致), 沪深300低价子集**不替换**。
- 若 B 显著优 (PF差距>30%且交易数≥20): 仅作为**下一个实验假设**——"手动池换成规则化低价子集", 需再做 PIT 成分+walk-forward 验证后才可讨论上线; 本次**不执行任何切换**。
"""
    fp_md = "reports/wf_csi300_cheap_compare_2026-09-29.md"
    fp_js = "reports/wf_csi300_cheap_compare_2026-09-29.json"
    with open(fp_md, "w", encoding="utf-8") as f:
        f.write(md)
    with open(fp_js, "w", encoding="utf-8") as f:
        json.dump({"built": now, "params": {k: P[k] for k in
                   ("atr_period", "st_multiplier", "ma_short", "ma_long", "rsi_threshold",
                    "rsi_upper", "volume_ratio", "atr_stop_multiplier", "max_holding_days",
                    "trade_cost", "cooldown_days")},
                   "universe_b_info": b_info, "A": {k: v for k, v in res_a.items() if k != "trades_detail"},
                   "B": {k: v for k, v in res_b.items() if k != "trades_detail"},
                   "A_trades": res_a["trades_detail"][:200], "B_trades": res_b["trades_detail"][:200]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n报告已写: {fp_md} / {fp_js}")


if __name__ == "__main__":
    main()
