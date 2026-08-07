"""
回测引擎 v3.0 — 事件驱动 + SellEngine统一卖出
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from strategies.trend_engine import TrendEngine
from risk.sell_engine import (
    evaluate as sell_evaluate, Position, Bar, Regime as SellRegime,
    SellSignal, ExecTime, set_config, SellConfig,
)
from execution.broker import Broker, Order, OrderSide


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
        self.engine = TrendEngine(params)
        self.broker = Broker(trade_cost)
        self.trade_cost = trade_cost
        self.cooldown_days = params.get("cooldown_days", 0) if params else 0
        self.trail_mult = params.get("trailing_atr_multiplier", 2.0) if params else 2.0
        set_config(SellConfig.load())

    def run(self, data_dict: dict, names_map: dict = None,
            index_df: pd.DataFrame = None) -> BacktestResult:
        if names_map is None: names_map = {}

        all_dates = sorted(set().union(*[df.index for df in data_dict.values()]))
        if len(all_dates) < 60:
            return BacktestResult([], pd.Series(), {}, "", "")

        positions = {}
        pending_buy = []
        pending_sell = []
        sell_reasons = {}
        trades = []
        cooldown = {}  # code → cooldown_until_date
        equity = pd.Series(1.0, index=all_dates)

        print(f"\n  回测: {all_dates[0].strftime('%Y-%m-%d')} → {all_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  股票: {len(data_dict)} | 交易日: {len(all_dates)} | 冷却:{self.cooldown_days}天 | 止损:{self.trail_mult}×ATR")

        for i, date in enumerate(all_dates):
            if i < 50: continue

            # ---- 执行卖单 ----
            for order in list(pending_sell):
                if order.symbol in data_dict and date in data_dict[order.symbol].index:
                    df = data_dict[order.symbol]
                    bar = df.loc[date]
                    fill = self.broker.try_fill(
                        order, float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]), float(bar["volume"]))
                    if fill and order.symbol in positions:
                        pos = positions[order.symbol]
                        pnl = (fill.price - pos["entry_price"]) / pos["entry_price"]
                        pnl -= self.trade_cost
                        holding = (date - pos["entry_date"]).days
                        trades.append(Trade(
                            order.symbol, names_map.get(order.symbol, order.symbol),
                            pos["entry_date"].strftime("%Y-%m-%d"), date.strftime("%Y-%m-%d"),
                            round(pos["entry_price"], 2), round(fill.price, 2),
                            sell_reasons.get(order.symbol, "系统卖出"),
                            round(pnl * 100, 2), holding))
                        del positions[order.symbol]
                        # 冷却期: 止损卖出后禁止重买
                        if self.cooldown_days > 0 and "止损" in sell_reasons.get(order.symbol, ""):
                            cooldown[order.symbol] = date + pd.Timedelta(days=self.cooldown_days)
            pending_sell.clear()
            sell_reasons.clear()

            # ---- 清理过期冷却 ----
            expired = [c for c, d in cooldown.items() if date >= d]
            for c in expired: del cooldown[c]

            # ---- 执行买单 ----
            for order in list(pending_buy):
                if order.symbol in data_dict and date in data_dict[order.symbol].index:
                    df = data_dict[order.symbol]
                    bar = df.loc[date]
                    fill = self.broker.try_fill(
                        order, float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]), float(bar["volume"]))
                    if fill and order.symbol not in positions:
                        idx = df.index.get_loc(date)
                        positions[order.symbol] = {
                            "entry_date": date, "entry_idx": idx,
                            "entry_price": fill.price,
                            "stop_loss": fill.price * 0.98,
                            "highest_since_entry": fill.price,
                            "signals": [],
                        }
            pending_buy.clear()

            # ---- 收盘后：SellEngine 评估持仓 + 生成新买单 ----
            # SellEngine 检查
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

                # 简化SellEngine: 只看ATR止损 + Supertrend翻空
                from strategies.indicators import calc_atr, calc_supertrend
                atr = calc_atr(df["high"], df["low"], df["close"], 14)
                st = calc_supertrend(df["high"], df["low"], df["close"], 14, 3.0)

                trailing = pos["highest_since_entry"] - self.trail_mult * float(atr.iloc[idx])
                effective = max(trailing, pos["stop_loss"])

                exit_now = False
                reason = ""
                if float(row["close"]) <= effective:
                    exit_now = True
                    reason = "ATR止损"
                elif st["direction"].iloc[idx] != 1:
                    exit_now = True
                    reason = "ST翻空"
                elif (date - pos["entry_date"]).days >= 20:
                    exit_now = True
                    reason = "20天到期"

                if exit_now:
                    pending_sell.append(Order(code, OrderSide.SELL, 0))
                    sell_reasons[code] = reason

            # 生成新买单
            cands = []
            for code, df in data_dict.items():
                if code in positions: continue
                if code in cooldown: continue   # 冷却期跳过
                if date not in df.index: continue
                idx = df.index.get_loc(date)
                result = self.engine.analyze(
                    df.iloc[:idx+1].copy(), code=code,
                    name=names_map.get(code, code))
                if result:
                    cands.append((code, result))
            cands.sort(key=lambda x: x[1]["score"], reverse=True)

            slots = max(0, 3 - len(positions))
            for code, _ in cands[:slots]:
                pending_buy.append(Order(code, OrderSide.BUY, 0))

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

        # ==== 净值曲线 ====
        if trades:
            cum = 1.0
            for date in all_dates:
                day_trades = [t for t in trades if t.exit_date == date.strftime("%Y-%m-%d")]
                for t in day_trades:
                    cum *= (1 + t.pnl_pct / 100)
                equity.loc[date] = cum
            equity = equity.ffill().fillna(1.0)

        return BacktestResult(
            trades=trades, equity_curve=equity,
            params=self.engine.__dict__,
            start_date=all_dates[0].strftime("%Y-%m-%d"),
            end_date=all_dates[-1].strftime("%Y-%m-%d"),
        )
