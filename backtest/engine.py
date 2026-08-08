"""
回测引擎 v3.0-fixed — 事件驱动 + SellEngine 真正接入（单一事实来源）

修复项（相对 11 文件夹版本）:
  [P0-A] 真正调用 risk.sell_engine.evaluate()，删除内联简化卖出逻辑
  [P0-B] 接入大盘过滤：set_index_data + can_open_position + get_max_positions
  [P1-2] 净值按日估值（含持仓浮亏），回撤不再被低估
  [P1-3] 止损线基于实际成交价重算
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from strategies.trend_engine import TrendEngine
from risk.sell_engine import (
    evaluate as sell_evaluate, Position as SPosition, Bar as SBar,
    Regime as SellRegime, ExecTime, set_config, SellConfig,
)
from execution.broker import Broker, Order, OrderSide
from execution.cost import CostModel
from strategies.indicators import calc_atr, calc_supertrend, calc_ma, calc_rsi


@dataclass
class Trade:
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    holding_days: int
    signals: list = field(default_factory=list)


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    params: dict
    start_date: str
    end_date: str

    @property
    def total_trades(self): return len(self.trades)
    @property
    def win_trades(self): return sum(1 for t in self.trades if t.pnl_pct > 0)
    @property
    def loss_trades(self): return sum(1 for t in self.trades if t.pnl_pct <= 0)
    @property
    def win_rate(self):
        if not self.trades: return 0
        return round(self.win_trades / len(self.trades) * 100, 1)
    @property
    def avg_win(self):
        wins = [t.pnl_pct for t in self.trades if t.pnl_pct > 0]
        return round(np.mean(wins), 2) if wins else 0
    @property
    def avg_loss(self):
        losses = [t.pnl_pct for t in self.trades if t.pnl_pct <= 0]
        return round(np.mean(losses), 2) if losses else 0
    @property
    def profit_factor(self):
        tw = sum(t.pnl_pct for t in self.trades if t.pnl_pct > 0)
        tl = abs(sum(t.pnl_pct for t in self.trades if t.pnl_pct <= 0))
        return round(tw / tl, 2) if tl > 0 else 0
    @property
    def total_return(self):
        if self.equity_curve.empty: return 0
        return round((self.equity_curve.iloc[-1] - 1) * 100, 2)
    @property
    def annual_return(self):
        if self.equity_curve.empty or len(self.equity_curve) < 2: return 0
        days = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days
        if days < 1: return 0
        return round((self.equity_curve.iloc[-1] ** (365 / days) - 1) * 100, 2)
    @property
    def max_drawdown(self):
        if self.equity_curve.empty: return 0
        peak = self.equity_curve.expanding().max()
        return round(((self.equity_curve - peak) / peak).min() * 100, 2)
    @property
    def sharpe_ratio(self):
        if self.equity_curve.empty or len(self.equity_curve) < 2: return 0
        dr = self.equity_curve.pct_change().dropna()
        if len(dr) < 2 or dr.std() == 0: return 0
        return round((dr.mean() / dr.std()) * np.sqrt(252), 2)
    @property
    def avg_holding_days(self):
        if not self.trades: return 0
        return round(np.mean([t.holding_days for t in self.trades]), 1)

    def summary(self) -> dict:
        return {
            "start": self.start_date, "end": self.end_date,
            "total_trades": self.total_trades, "win_rate": self.win_rate,
            "avg_win": self.avg_win, "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor, "total_return": self.total_return,
            "annual_return": self.annual_return, "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio, "avg_hold_days": self.avg_holding_days,
        }


class BacktestEngine:
    def __init__(self, params: dict = None, trade_cost: float = 0.003):
        self.params = params or {}
        self.engine = TrendEngine(params)
        self.cost = CostModel(
            commission_rate=0.00025, stamp_tax_rate=0.0005,
            slippage_rate=0.0005)
        self.broker = Broker(self.cost)
        self.trade_cost = trade_cost
        self.cooldown_days = self.params.get("cooldown_days", 5)
        self.max_positions_hard = self.params.get("top_n", 5)
        # 关键：从 params 加载卖出配置（含 atr_stop_multiplier=3.0）
        set_config(SellConfig.load("config/params.json"))

    def run(self, data_dict: dict, names_map: dict = None,
            index_df: pd.DataFrame = None) -> BacktestResult:
        if names_map is None: names_map = {}
        # [P0-B] 大盘过滤：接入指数数据（完整传入，run 内逐日切片更新 regime）
        self._index_df_full = index_df

        all_dates = sorted(set().union(*[df.index for df in data_dict.values()]))
        if len(all_dates) < 60:
            return BacktestResult([], pd.Series(), {}, "", "")

        positions = {}        # code -> dict(entry/stop/highest/shares/entry_idx/entry_date)
        pending_buy = []      # (code, entry_price_ref) 次日开盘成交
        pending_sell = []     # (code, reason, exec_time)
        cooldown = {}         # code -> cooldown_until_date
        trades = []
        INITIAL_CAPITAL = 10000.0   # 名义初始资金（元）
        cash = INITIAL_CAPITAL
        equity = pd.Series(1.0, index=all_dates)
        single_pos_pct = self.params.get("single_position_pct", 0.15)

        print(f"\n  回测: {all_dates[0].strftime('%Y-%m-%d')} → {all_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  股票: {len(data_dict)} | 冷却:{self.cooldown_days}天 | 单票仓位:{single_pos_pct:.0%}")

        for i, date in enumerate(all_dates):
            if i < 50: continue

            # [P0-B] 逐日更新大盘 regime（用截止当日的指数数据）
            if self._index_df_full is not None and len(self._index_df_full) > 0:
                self.engine.set_index_data(
                    self._index_df_full.loc[:date] if date in self._index_df_full.index
                    else self._index_df_full)

            # 当日指数状态（用于 SellEngine regime）
            regime_state = self.engine.get_regime()
            hs300_below_ma20 = False
            if index_df is not None and date in index_df.index:
                idx_pos = index_df.index.get_loc(date)
                if idx_pos >= self.engine.index_ma_period:
                    ma_val = calc_ma(index_df["close"], self.engine.index_ma_period).iloc[idx_pos]
                    hs300_below_ma20 = float(index_df["close"].iloc[idx_pos]) < float(ma_val)

            # ---- 执行卖单（T+1：信号日收盘产生，次日开盘/14:50成交） ----
            for code, reason, exec_time in list(pending_sell):
                if code in data_dict and date in data_dict[code].index and code in positions:
                    df = data_dict[code]
                    bar = df.loc[date]
                    prev_close = float(df["close"].iloc[df.index.get_loc(date) - 1]) if df.index.get_loc(date) > 0 else float(bar["open"])
                    fill = self.broker.try_fill(
                        Order(code, OrderSide.SELL, 0),
                        float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]), prev_close,
                        float(bar["volume"]))
                    if fill:
                        pos = positions[code]
                        pnl = (fill.price - pos["entry_price"]) / pos["entry_price"]
                        pnl -= self.trade_cost
                        holding = (date - pos["entry_date"]).days
                        trades.append(Trade(
                            code, names_map.get(code, code),
                            pos["entry_date"].strftime("%Y-%m-%d"), date.strftime("%Y-%m-%d"),
                            round(pos["entry_price"], 2), round(fill.price, 2),
                            reason, round(pnl * 100, 2), holding))
                        cash += pos["shares"] * fill.price - fill.fee
                        del positions[code]
                        # 冷却期：止损卖出后禁止重买
                        if self.cooldown_days > 0 and "止损" in reason:
                            cooldown[code] = date + pd.Timedelta(days=self.cooldown_days)
            pending_sell.clear()

            # ---- 清理过期冷却 ----
            expired = [c for c, d in cooldown.items() if date >= d]
            for c in expired: del cooldown[c]

            # ---- 执行买单（T+1：信号日收盘产生，次日开盘成交） ----
            for code, entry_ref in list(pending_buy):
                if code in data_dict and date in data_dict[code].index and code not in positions:
                    df = data_dict[code]
                    bar = df.loc[date]
                    idx = df.index.get_loc(date)
                    prev_close = float(df["close"].iloc[idx - 1]) if idx > 0 else float(bar["open"])
                    # 按仓位比例计算份额（A股 100 股整数倍）
                    budget = cash * single_pos_pct
                    shares = int(budget / float(bar["open"]) / 100) * 100
                    if shares <= 0:
                        continue
                    fill = self.broker.try_fill(
                        Order(code, OrderSide.BUY, shares),
                        float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]), prev_close,
                        float(bar["volume"]))
                    if fill:
                        # [P1-3] 止损线基于实际成交价重算
                        stop_loss = self.engine.calc_initial_stop(df, fill.price, idx)
                        cash -= fill.price * fill.shares + fill.fee
                        positions[code] = {
                            "entry_date": date, "entry_idx": idx,
                            "entry_price": fill.price,
                            "stop_loss": stop_loss,
                            "highest_since_entry": fill.price,
                            "shares": fill.shares,
                            "signals": entry_ref.get("signals", []),
                        }
            pending_buy.clear()

            # ---- 收盘后：SellEngine 评估持仓（[P0-A] 真正调用） ----
            for code, pos in list(positions.items()):
                if code not in data_dict or date not in data_dict[code].index:
                    continue
                df = data_dict[code]
                idx = df.index.get_loc(date)
                row = df.iloc[idx]

                # 更新最高价
                high_since = float(df["high"].iloc[pos["entry_idx"]:idx+1].max())
                if high_since > pos["highest_since_entry"]:
                    pos["highest_since_entry"] = high_since

                # 构造 SellEngine 输入
                atr = calc_atr(df["high"], df["low"], df["close"], self.engine.atr_period).iloc[idx]
                st = calc_supertrend(df["high"], df["low"], df["close"],
                                     self.engine.atr_period, self.engine.st_multiplier)
                ma20 = calc_ma(df["close"], self.engine.ma_short).iloc[idx]
                rsi = calc_rsi(df["close"], self.engine.rsi_period).iloc[idx]
                vol_ma = df["volume"].rolling(20).mean().iloc[idx]
                volume_ratio = float(df["volume"].iloc[idx] / vol_ma) if vol_ma > 0 else 0
                day_change = float(row["close"] / df["close"].iloc[idx-1] - 1) if idx > 0 else 0

                s_pos = SPosition(
                    code=code, name=names_map.get(code, code),
                    entry_price=pos["entry_price"],
                    hold_days=(date - pos["entry_date"]).days,
                    prev_stop_loss=pos["stop_loss"],
                    highest_price=pos["highest_since_entry"],
                )
                s_bar = SBar(
                    close=float(row["close"]), open=float(row["open"]),
                    high=float(row["high"]), low=float(row["low"]),
                    atr=float(atr),
                    supertrend_bullish=bool(st["direction"].iloc[idx] == 1),
                    ma20=float(ma20), rsi=float(rsi),
                    volume_ratio=float(volume_ratio),
                    day_change_pct=float(day_change * 100),
                    recent_3d_high=float(df["high"].iloc[max(0, idx-2):idx+1].max()),
                    prev_3d_high=float(df["high"].iloc[max(0, idx-5):max(0, idx-2)].max()),
                )
                s_regime = SellRegime(state=regime_state, hs300_below_ma20=hs300_below_ma20)

                sig = sell_evaluate(s_pos, s_bar, s_regime)
                if sig:
                    # 更新止损线
                    if sig.new_stop_loss > pos["stop_loss"]:
                        pos["stop_loss"] = sig.new_stop_loss
                    if sig.exec_time == ExecTime.NEXT_OPEN:
                        pending_sell.append((code, sig.reason, "open"))
                    else:
                        pending_sell.append((code, sig.reason, "1450"))

            # ---- 生成新买单（[P0-B] 大盘过滤 + 动态仓位） ----
            if self.engine.can_open_position():
                max_slots = self.engine.get_max_positions()
                slots = max(0, max_slots - len(positions))
                if slots > 0:
                    cands = []
                    for code, df in data_dict.items():
                        if code in positions: continue
                        if code in cooldown: continue
                        if date not in df.index: continue
                        idx = df.index.get_loc(date)
                        result = self.engine.analyze(
                            df.iloc[:idx+1].copy(), code=code,
                            name=names_map.get(code, code))
                        if result:
                            cands.append((code, result))
                    cands.sort(key=lambda x: x[1]["score"], reverse=True)
                    for code, result in cands[:slots]:
                        pending_buy.append((code, result))

            # ---- 净值按日估值（[P1-2] 含持仓浮亏，归一化到 1.0） ----
            total_value = cash
            for code, pos in positions.items():
                if code in data_dict and date in data_dict[code].index:
                    total_value += pos["shares"] * float(data_dict[code]["close"].loc[date])
                else:
                    total_value += pos["shares"] * pos["entry_price"]
            equity.loc[date] = total_value / INITIAL_CAPITAL

        # ---- 强制清仓 ----
        last_date = all_dates[-1]
        for code, pos in positions.items():
            if code in data_dict and last_date in data_dict[code].index:
                exit_price = float(data_dict[code]["close"].loc[last_date])
                pnl = (exit_price - pos["entry_price"]) / pos["entry_price"]
                pnl -= self.trade_cost
                holding = (last_date - pos["entry_date"]).days
                trades.append(Trade(
                    code, names_map.get(code, code),
                    pos["entry_date"].strftime("%Y-%m-%d"), last_date.strftime("%Y-%m-%d"),
                    round(pos["entry_price"], 2), round(exit_price, 2),
                    "回测结束", round(pnl * 100, 2), holding))

        equity = equity.ffill().fillna(1.0)

        return BacktestResult(
            trades=trades, equity_curve=equity,
            params=self.engine.__dict__,
            start_date=all_dates[0].strftime("%Y-%m-%d"),
            end_date=all_dates[-1].strftime("%Y-%m-%d"),
        )
