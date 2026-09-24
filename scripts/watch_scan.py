"""
池外强势股观察名单 — 每日盘后扫描 (实验② 2026-09-24)
目的: 检验"49支池外"的金叉+放量信号次日表现是否优于池内, 用数据决定是否扩池
纪律: 只记录不推荐不实盘; 每条候选自动回填 T+1 表现, 累积命中率
运行: python scripts/watch_scan.py  (GitHub Actions 每日自动)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import pandas as pd
import akshare as ak
from data.universe import DEFAULT_UNIVERSE

POOL = set(DEFAULT_UNIVERSE)
REPORT = "reports/watchlist_scan.json"


def macd_golden_today(c: pd.Series) -> bool:
    if len(c) < 35:
        return False
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])


def main():
    bjt = pd.Timestamp.utcnow() + pd.Timedelta(hours=8)
    today_s = str(bjt.date())
    print(f"池外观察名单扫描 {today_s}")

    # 1. 全市场快照: 主板+非ST+2~10元+成交额>1亿, 排除池内
    spot = None
    for attempt in range(2):
        try:
            spot = ak.stock_zh_a_spot_em()
            break
        except Exception as e:
            print(f"[WARN] 全市场快照获取失败(第{attempt+1}次): {str(e)[:60]}")
            time.sleep(2)
    if spot is None:
        print("[ERROR] 快照不可用, 本轮扫描跳过 (非致命)")
        return
    df = spot[spot["代码"].str.startswith(("00", "60"))].copy()
    df = df[~df["名称"].str.contains("ST|退", na=False)]
    df = df[(df["最新价"] >= 2) & (df["最新价"] <= 10)]
    df = df[df["成交额"] > 1e8]
    df = df[~df["代码"].isin(POOL)]
    df = df.sort_values("成交额", ascending=False).head(30)
    print(f"候选(成交额Top30): {len(df)} 支")

    # 2. 逐支拉K线, 池内同款入场信号: MACD金叉今日 + 量比>=1.5 + 收盘>MA20
    from daily_push import fetch_data
    data = fetch_data(df["代码"].tolist())
    cands = []
    for code, k in data.items():
        if len(k) < 70:
            continue
        c = k["close"]
        v = k["volume"]
        name = df.loc[df["代码"] == code, "名称"].iloc[0] if (df["代码"] == code).any() else code
        sig = []
        if macd_golden_today(c):
            sig.append("MACD金叉")
        vr = float(v.iloc[-1] / v.rolling(60).mean().iloc[-1]) if v.rolling(60).mean().iloc[-1] > 0 else 0
        if vr >= 1.5:
            sig.append(f"量比{vr:.1f}")
        if c.iloc[-1] > c.rolling(20).mean().iloc[-1]:
            sig.append(">MA20")
        if sig:
            cands.append({"code": code, "name": name,
                          "price": round(float(c.iloc[-1]), 2), "signals": ";".join(sig)})
        time.sleep(0.05)
    print(f"信号命中: {len(cands)} 支")
    for x in cands:
        print(f"  {x['code']} {x['name']} ¥{x['price']} {x['signals']}")

    # 3. 读取历史, 回填旧条目的 T+1 表现
    history = []
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as f:
            history = json.load(f)
    filled = 0
    for entry in history:
        if entry.get("t1_filled"):
            continue
        d0 = entry["date"]
        rets = []
        for x in entry["candidates"]:
            try:
                sym = ("sh" if x["code"].startswith("6") else "sz") + x["code"]
                k = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
                k["date"] = pd.to_datetime(k["date"])
                k = k.set_index("date").sort_index()
                after = k.loc[d0:]
                after = after[after.index.strftime("%Y-%m-%d") > d0]
                if len(after) >= 1:
                    t1 = float(after["close"].iloc[0])
                    rets.append(round((t1 / x["price"] - 1) * 100, 2))
            except Exception:
                pass
            time.sleep(0.2)
        if rets:
            entry["t1_returns"] = rets
            entry["t1_avg"] = round(sum(rets) / len(rets), 2)
            entry["t1_winrate"] = round(100 * sum(1 for r in rets if r > 0) / len(rets), 1)
            entry["t1_filled"] = True
            filled += 1
    if filled:
        print(f"回填历史条目: {filled} 条")

    # 4. 追加今日条目并保存
    if cands or filled or not history:
        history.append({"date": today_s, "pool": "池外(全市场)", "candidates": cands})
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)

    # 5. 已回填条目的命中率汇总
    done = [h for h in history if h.get("t1_filled")]
    if done:
        all_rets = [r for h in done for r in h["t1_returns"]]
        print(f"\n观察名单累计: {len(done)}个交易日 | {len(all_rets)}笔 | "
              f"T+1均收益 {sum(all_rets)/len(all_rets):+.2f}% | "
              f"胜率 {100*sum(1 for r in all_rets if r>0)/len(all_rets):.1f}%")
    print("完成")


if __name__ == "__main__":
    main()
