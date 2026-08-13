"""
模拟盘 v2.0 — 生产版，每日运行
- 持仓持久化 (position/paper_state.json)
- 每日信号日志 (position/paper_log.csv)
- 14:50 检查 + 次日开盘执行
- 复用 SellEngine + Broker 全链路
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from dataclasses import dataclass, asdict
from typing import Optional

from strategies.trend_engine import TrendEngine
from risk.sell_engine import (
    evaluate as sell_evaluate, set_config, SellConfig,
    Position as SPosition, Bar as SBar, Regime as SellRegime,
)
from execution.broker import Broker, Order, OrderSide, CostModel
from data.universe import get_universe
from data.adjust import get_price
from strategies.indicators import calc_ma, calc_atr, calc_supertrend, calc_rsi

# ==========================================
# 路径
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "position", "paper_state.json")
LOG_FILE = os.path.join(BASE_DIR, "position", "paper_log.csv")
PARAMS_FILE = os.path.join(BASE_DIR, "config", "params.json")

INITIAL_CAPITAL = 10000.0


# ==========================================
# 持仓状态
# ==========================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "cash": INITIAL_CAPITAL,
        "positions": {},       # code -> {shares, entry_price, entry_date, stop_loss, highest}
        "cooldown": {},        # code -> cooldown_until (YYYY-MM-DD)
        "pending_orders": [],  # [(code, side, shares, signal_price, reason), ...]
        "trades": [],          # [{code, entry_date, exit_date, ...}]
        "last_run": None,
    }


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)


def log_event(event_type: str, detail: str):
    """追加日志"""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts}|{event_type}|{detail}\n")


# ==========================================
# 主逻辑
# ==========================================
def run_daily():
    """每日运行入口"""
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"  模拟盘 {today_str}")
    print(f"{'='*50}")

    # 1. 加载状态
    state = load_state()
    state["last_run"] = today_str

    # 2. 加载参数
    with open(PARAMS_FILE) as f:
        params = json.load(f)
    set_config(SellConfig.load(PARAMS_FILE))

    # 3. 获取今日数据
    codes = get_universe()[:50]
    names_map = {c: c for c in codes}

    data_dict = {}
    for code in codes:
        df = get_price(code, start="2025-01-01", end=today_str)
        if df is not None and len(df) > 50:
            data_dict[code] = df

    if len(data_dict) < 5:
        print(f"  数据不足({len(data_dict)}支)，跳过")
        save_state(state)
        return

    # 4. 获取最新交易日
    last_date = data_dict[list(data_dict.keys())[0]].index[-1]
    print(f"  数据日期: {last_date.date()}")

    # 5. 引擎初始化
    engine = TrendEngine(params)

    # 合成指数
    closes = [df["close"] for df in data_dict.values()]
    all_dates = sorted(set().union(*[c.index for c in closes]))
    synth = pd.DataFrame(index=all_dates)
    synth["close"] = sum(c.reindex(all_dates).ffill().fillna(0) for c in closes) / len(closes)
    for col in ["open", "high", "low"]:
        synth[col] = synth["close"]
    synth["volume"] = 1
    engine.set_index_data(synth.loc[:last_date])

    regime_state = engine.get_regime()
    max_pos = engine.get_max_positions()
    can_open = engine.can_open_position()
    print(f"  大盘: {regime_state} | 仓位上限: {max_pos} | {'可开仓' if can_open else '禁开仓'}")

    # 6. [先] 执行昨日挂单 (T+1 开盘成交 → 与回测 next_open 一致)
    cost = CostModel()
    broker = Broker(cost)
    cooldown_days = params.get("cooldown_days", 5)

    for order_info in list(state["pending_orders"]):
        code, side_str, shares, signal_px, reason = order_info
        side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL

        if code not in data_dict or last_date not in data_dict[code].index:
            continue
        row = data_dict[code].loc[last_date]
        prev_close = float(data_dict[code]["close"].iloc[
            data_dict[code].index.get_loc(last_date) - 1]) if data_dict[code].index.get_loc(last_date) > 0 else float(row["open"])

        fill = broker.try_fill(
            Order(code, side, shares),
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), prev_close, float(row["volume"]))

        if fill:
            slippage = (fill.price - signal_px) / signal_px * 100
            if side == OrderSide.BUY:
                state["cash"] -= fill.price * fill.shares + fill.fee
                idx2 = data_dict[code].index.get_loc(last_date)
                sl = engine.calc_initial_stop(data_dict[code], fill.price, idx2)
                state["positions"][code] = {
                    "shares": fill.shares, "entry_price": round(fill.price, 2),
                    "entry_date": today_str, "stop_loss": round(sl, 2),
                    "highest": round(fill.price, 2),
                }
                log_event("BUY_FILL",
                          f"{code} | shares={fill.shares} | fill_price={fill.price:.2f} | "
                          f"signal_price={signal_px:.2f} | slippage={slippage:+.2f}% | "
                          f"fee={fill.fee:.2f} | stop_loss={sl:.2f}")
                print(f"  📥 买入: {code} {fill.shares}股 @{fill.price:.2f} "
                      f"滑点{slippage:+.2f}% 止损={sl:.2f}")
            else:
                # 卖出
                if code in state["positions"]:
                    pos = state["positions"][code]
                    pnl = (fill.price - pos["entry_price"]) / pos["entry_price"]
                    pnl -= 0.003
                    state["cash"] += pos["shares"] * fill.price - fill.fee
                    state["trades"].append({
                        "code": code, "entry_date": pos["entry_date"],
                        "exit_date": today_str, "entry_price": pos["entry_price"],
                        "exit_price": round(fill.price, 2),
                        "signal_price": round(signal_px, 2),
                        "reason": reason, "pnl_pct": round(pnl * 100, 2),
                        "fee": round(fill.fee, 2),
                    })
                    del state["positions"][code]
                    if "止损" in reason:
                        cd_until = (last_date + pd.Timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
                        state["cooldown"][code] = cd_until
                    log_event("SELL_FILL",
                              f"{code} | fill_price={fill.price:.2f} | signal_price={signal_px:.2f} | "
                              f"slippage={slippage:+.2f}% | fee={fill.fee:.2f} | PnL={pnl*100:.2f}%")
                    print(f"    ✅ 卖出成交 @{fill.price:.2f} 滑点{slippage:+.2f}% PnL={pnl*100:.2f}%")
        else:
            log_event("ORDER_REJECT", f"{code} {side_str} 拒单(涨跌停/停牌)")

    state["pending_orders"] = []  # 清空已执行挂单

    # 7. 评估持仓（收盘后 SellEngine）→ 生成卖出挂单
    for code, pos in list(state["positions"].items()):
        if code not in data_dict or last_date not in data_dict[code].index:
            continue
        df = data_dict[code]
        idx = df.index.get_loc(last_date)
        row = df.iloc[idx]

        # 更新最高价
        entry_date = pd.Timestamp(pos["entry_date"])
        if entry_date in df.index:
            entry_idx = df.index.get_loc(entry_date)
            high_since = float(df["high"].iloc[entry_idx:idx+1].max())
            if high_since > pos.get("highest", pos["entry_price"]):
                pos["highest"] = high_since

        # SellEngine
        atr = calc_atr(df["high"], df["low"], df["close"], engine.atr_period).iloc[idx]
        st = calc_supertrend(df["high"], df["low"], df["close"],
                             engine.atr_period, engine.st_multiplier)
        ma20 = calc_ma(df["close"], engine.ma_short).iloc[idx]
        rsi = calc_rsi(df["close"], engine.rsi_period).iloc[idx]
        vol_ma = df["volume"].rolling(20).mean().iloc[idx]
        vr = float(df["volume"].iloc[idx] / vol_ma) if vol_ma > 0 else 0
        day_chg = float(row["close"] / df["close"].iloc[idx-1] - 1) if idx > 0 else 0

        hs300_below = False  # 简化
        s_pos = SPosition(code=code, name=code, entry_price=pos["entry_price"],
                          hold_days=(last_date - entry_date).days,
                          prev_stop_loss=pos["stop_loss"],
                          highest_price=pos.get("highest", pos["entry_price"]))
        s_bar = SBar(close=float(row["close"]), open=float(row["open"]),
                     high=float(row["high"]), low=float(row["low"]),
                     atr=float(atr), supertrend_bullish=bool(st["direction"].iloc[idx] == 1),
                     ma20=float(ma20), rsi=float(rsi), volume_ratio=float(vr),
                     day_change_pct=float(day_chg * 100),
                     recent_3d_high=float(df["high"].iloc[max(0, idx-2):idx+1].max()),
                     prev_3d_high=float(df["high"].iloc[max(0, idx-5):max(0, idx-2)].max()))
        s_regime = SellRegime(state=regime_state, hs300_below_ma20=hs300_below)

        sig = sell_evaluate(s_pos, s_bar, s_regime)
        if sig:
            signal_price = float(row["close"])
            log_event("SELL_SIGNAL",
                      f"{code} | action={sig.action} | reason={sig.reason} | "
                      f"score={sig.sell_score} | signal_price={signal_price:.2f} | "
                      f"hold_days={s_pos.hold_days} | profit_pct={(signal_price/pos['entry_price']-1)*100:.1f}%")
            print(f"  📤 卖出信号: {code} {sig.action} [{sig.reason}]")

            # 挂单 → 次日开盘执行 (与回测 next_open 一致)
            state["pending_orders"].append(
                (code, "SELL", pos["shares"], signal_price, sig.reason))

    # 7. 清理过期冷却
    for c in list(state["cooldown"].keys()):
        if last_date.strftime("%Y-%m-%d") >= state["cooldown"][c]:
            del state["cooldown"][c]

    # 8. 生成新买单
    if can_open:
        slots = max(0, max_pos - len(state["positions"]))
        if slots > 0:
            cands = []
            for code, df in data_dict.items():
                if code in state["positions"]: continue
                if code in state["cooldown"]: continue
                if last_date not in df.index: continue
                idx = df.index.get_loc(last_date)
                result = engine.analyze(df.iloc[:idx+1].copy(), code=code, name=code)
                if result:
                    cands.append((code, result))
            cands.sort(key=lambda x: x[1]["score"], reverse=True)

            single_pct = params.get("single_position_pct", 0.10)
            for code, result in cands[:slots]:
                row = data_dict[code].loc[last_date]
                budget = state["cash"] * single_pct
                shares = int(budget / float(row["open"]) / 100) * 100
                if shares <= 0: continue
                signal_px = float(result.get("entry_price", row["close"]))

                # 挂单 → 次日开盘执行 (与回测 next_open 一致)
                state["pending_orders"].append(
                    (code, "BUY", shares, signal_px, "信号入场"))
                log_event("BUY_SIGNAL",
                          f"{code} | shares={shares} | signal_price={signal_px:.2f} | "
                          f"score={result.get('score',0):.0f}")
                print(f"  📥 买入信号: {code} {shares}股 信号价{signal_px:.2f}")

    # 9. 资产快照
    pos_value = 0
    for code, pos in state["positions"].items():
        if code in data_dict and last_date in data_dict[code].index:
            pos_value += pos["shares"] * float(data_dict[code].loc[last_date, "close"])
    total = state["cash"] + pos_value
    pnl_total = total - INITIAL_CAPITAL

    print(f"\n  💰 资产: 现金¥{state['cash']:.0f} 持仓¥{pos_value:.0f} "
          f"总计¥{total:.0f} ({(total/INITIAL_CAPITAL-1)*100:+.2f}%)")
    slots_used = len(state["positions"])
    slots_max = max_pos
    print(f"  槽位: {slots_used}/{slots_max} | 累计交易: {len(state['trades'])}笔")
    if state["pending_orders"]:
        print(f"  ⏳ 挂单: {len(state['pending_orders'])}笔 (次日开盘执行)")
    print(f"  ⚠️ 成交假设: T日收盘信号 → T+1日开盘执行 (与回测 next_open 一致)")

    log_event("SNAPSHOT", f"cash={state['cash']:.0f} pos={pos_value:.0f} "
              f"total={total:.0f} pnl={pnl_total:.0f} positions={len(state['positions'])}")

    # 10. 保存
    save_state(state)
    print(f"  状态已保存: {STATE_FILE}")


if __name__ == "__main__":
    run_daily()
