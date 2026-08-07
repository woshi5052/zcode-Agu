"""
回测引擎 v3.0 — 事件驱动 + SellEngine统一卖出
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

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
        # 初始化 SellEngine 配置
        set_config(SellConfig.load())

    def run(self, data_dict: dict, names_map: dict = None,
            index_df: pd.DataFrame = None) -> BacktestResult:
        if names_map is None:
            names_map = {}

        all_dates = sorted(set().union(*[df.index for df in data_dict.values()]))
        if len(all_dates) < 60:
            return BacktestResult([], pd.Series(), {}, "", "")

        positions = {}          # code → {entry_date, entry_idx, entry_price, stop, signals, ...}
        pending_buy_orders = []
        pending_sell_orders = []
        sell_reasons = {}       # code → reason
        trades = []
        equity = pd.Series(1.0, index=all_dates)

        print(f"\n  回测: {all_dates[0].strftime('%Y-%m-%d')} → {all_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  股票: {len(data_dict)} | 交易日: {len(all_dates)} | 手续费: {self.trade_cost:.2%}")

        for i, date in enumerate(all_dates):
            if i < 50: continue

        # ===== 执行昨日卖单 =====
        sell_codes = {}
        for order in pending_sell_orders:
            if order.symbol in data_dict and date in data_dict[order.symbol].index:
                    df = data_dict[order.symbol]
                    idx = df.index.get_loc(date)
                    bar = df.iloc[idx]
                    fill = self.broker.try_fill(
                        order, float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]),
                        float(bar["volume"])
                    )
                    if fill and order.symbol in positions:
                        pos = positions[order.symbol]
                        pnl = (fill.price - pos["entry_price"]) / pos["entry_price"]
                        pnl -= self.trade_cost
                        holding = (date - pos["entry_date"]).days
                        trades.append(Trade(
                            code=order.symbol,
                            name=names_map.get(order.symbol, order.symbol),
                            entry_date=pos["entry_date"].strftime("%Y-%m-%d"),
                            exit_date=date.strftime("%Y-%m-%d"),
                            entry_price=round(pos["entry_price"], 2),
                            exit_price=round(fill.price, 2),
                            exit_reason=sell_reasons.get(order.symbol, "系统卖出"),
                            pnl_pct=round(pnl * 100, 2),
                            holding_days=holding,
                        ))
                        equity.loc[date] = equity.iloc[i-1] * (1 + pnl) if i > 0 else equity.iloc[i]
                        del positions[order.symbol]
            pending_sell_orders.clear()
            sell_reasons.clear()

            # ===== 执行昨日买单 =====
            for order in pending_buy_orders:
                if order.symbol in data_dict and date in data_dict[order.symbol].index:
                    df = data_dict[order.symbol]
                    idx = df.index.get_loc(date)
                    bar = df.iloc[idx]
                    fill = self.broker.try_fill(
                        order, float(bar["open"]), float(bar["high"]),
                        float(bar["low"]), float(bar["close"]),
                        float(bar["volume"])
                    )
                    if fill and order.symbol not in positions:
                        positions[order.symbol] = {
                            "entry_date": date, "entry_idx": idx,
                            "entry_price": fill.price,
                            "stop_loss": fill.price * 0.98,  # 初始止损2%
                            "highest_since_entry": fill.price,
                            "signals": [],
                        }
            pending_buy_orders.clear()

            # ===== 收盘后：SellEngine评估 =====
            for code, pos in list(positions.items()):
                if code not in data_dict or date not in data_dict[code].index:
                    continue
                df = data_dict[code]
                idx = df.index.get_loc(date)
                bar_row = df.iloc[idx]

                # 更新最高价
                high_since = float(df["high"].iloc[pos["entry_idx"]:idx+1].max())
                if high_since > pos["highest_since_entry"]:
                    pos["highest_since_entry"] = high_since

                # 构建 Bar
                atr_col = _calc_atr_col(df, 14)
                rsi_col = _calc_rsi_col(df, 14)
                ma20_col = df["close"].rolling(20).mean()
                from strategies.indicators import calc_supertrend
                st_col = calc_supertrend(df["high"], df["low"], df["close"], 14, 3.0)

                vol_ma20 = df["volume"].rolling(20).mean().iloc[idx]

                bar_data = Bar(
                    close=float(bar_row["close"]), open=float(bar_row["open"]),
                    high=float(bar_row["high"]), low=float(bar_row["low"]),
                    atr=float(atr_col.iloc[idx]) if atr_col is not None else 0.1,
                    supertrend_bullish=(st_col["direction"].iloc[idx] == 1),
                    ma20=float(ma20_col.iloc[idx]), rsi=float(rsi_col.iloc[idx]) if rsi_col is not None else 50,
                    volume_ratio=float(df["volume"].iloc[idx] / vol_ma20) if vol_ma20 > 0 else 1,
                    day_change_pct=float((bar_row["close"] - bar_row["open"]) / bar_row["open"] * 100) if bar_row["open"] > 0 else 0,
                    recent_3d_high=float(df["high"].iloc[max(0, idx-2):idx+1].max()),
                    prev_3d_high=float(df["high"].iloc[max(0, idx-5):max(0, idx-2)].max()) if idx > 5 else 0,
                )

                sell_pos = Position(
                    code=code, name=names_map.get(code, code),
                    entry_price=pos["entry_price"], hold_days=(date - pos["entry_date"]).days,
                    prev_stop_loss=pos["stop_loss"],
                    highest_price=pos["highest_since_entry"],
                )

                sig = sell_evaluate(sell_pos, bar_data)
                if sig:
                    pos["stop_loss"] = sig.new_stop_loss

                    if sig.exec_time == ExecTime.NEXT_OPEN:
                        pending_sell_orders.append(Order(
                            symbol=code, side=OrderSide.SELL, shares=0))
                        pos["pending_sell"] = True
                        sell_reasons[code] = sig.reason
                    elif sig.exec_time == ExecTime.NEXT_1450:
                        pending_sell_orders.append(Order(
                            symbol=code, side=OrderSide.SELL, shares=0))
                        pos["pending_sell"] = True
                        sell_reasons[code] = sig.reason

            # ===== 收盘后：生成新买单 =====
            candidates = []
            for code, df in data_dict.items():
                if code in positions:
                    continue
                if date not in df.index:
                    continue
                idx = df.index.get_loc(date)
                temp_df = df.iloc[:idx+1].copy()
                result = self.engine.analyze(temp_df, code=code,
                                             name=names_map.get(code, code))
                if result:
                    candidates.append((code, result))

            candidates.sort(key=lambda x: x[1]["score"], reverse=True)

            # 最多开3仓
            slots = max(0, 3 - len(positions))
            for code, signal in candidates[:slots]:
                pending_buy_orders.append(Order(
                    symbol=code, side=OrderSide.BUY, shares=0))

        # ===== 清理未平仓 =====
        last_date = all_dates[-1]
        for code, pos in positions.items():
            if code in data_dict and last_date in data_dict[code].index:
                exit_price = float(data_dict[code]["close"].loc[last_date])
                pnl = (exit_price - pos["entry_price"]) / pos["entry_price"]
                pnl -= self.trade_cost
                holding = (last_date - pos["entry_date"]).days
                trades.append(Trade(
                    code=code, name=names_map.get(code, code),
                    entry_date=pos["entry_date"].strftime("%Y-%m-%d"),
                    exit_date=last_date.strftime("%Y-%m-%d"),
                    entry_price=round(pos["entry_price"], 2),
                    exit_price=round(exit_price, 2),
                    exit_reason="回测结束",
                    pnl_pct=round(pnl * 100, 2), holding_days=holding,
                ))

        # 构建净值曲线
        if trades:
            cum = 1.0
            for date in all_dates:
                day_trades = [t for t in trades
                             if t.exit_date == date.strftime("%Y-%m-%d")]
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


def _calc_atr_col(df, period):
    from strategies.indicators import calc_atr
    return calc_atr(df["high"], df["low"], df["close"], period)


def _calc_rsi_col(df, period):
    from strategies.indicators import calc_rsi
    return calc_rsi(df["close"], period)
