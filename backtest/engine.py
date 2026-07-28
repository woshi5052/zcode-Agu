"""
回测引擎 —— T+1执行 + 移动止损 + 手续费
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from strategies.trend_engine import TrendEngine
from strategies.filters import filter_limit_up_down


@dataclass
class Trade:
    """单笔交易记录"""
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
    """回测结果"""
    trades: list[Trade]
    equity_curve: pd.Series
    params: dict
    start_date: str
    end_date: str

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl_pct > 0)

    @property
    def loss_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl_pct <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0
        return round(self.win_trades / len(self.trades) * 100, 1)

    @property
    def avg_win(self) -> float:
        wins = [t.pnl_pct for t in self.trades if t.pnl_pct > 0]
        return round(np.mean(wins), 2) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl_pct for t in self.trades if t.pnl_pct <= 0]
        return round(np.mean(losses), 2) if losses else 0

    @property
    def profit_factor(self) -> float:
        total_win = sum(t.pnl_pct for t in self.trades if t.pnl_pct > 0)
        total_loss = abs(sum(t.pnl_pct for t in self.trades if t.pnl_pct <= 0))
        return round(total_win / total_loss, 2) if total_loss > 0 else 0

    @property
    def total_return(self) -> float:
        if self.equity_curve.empty:
            return 0
        return round((self.equity_curve.iloc[-1] - 1) * 100, 2)

    @property
    def annual_return(self) -> float:
        if self.equity_curve.empty or len(self.equity_curve) < 2:
            return 0
        days = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days
        if days < 1:
            return 0
        total = self.equity_curve.iloc[-1]
        return round((total ** (365 / days) - 1) * 100, 2)

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0
        peak = self.equity_curve.expanding().max()
        dd = (self.equity_curve - peak) / peak
        return round(dd.min() * 100, 2)

    @property
    def sharpe_ratio(self) -> float:
        if self.equity_curve.empty or len(self.equity_curve) < 2:
            return 0
        daily_returns = self.equity_curve.pct_change().dropna()
        if len(daily_returns) < 2 or daily_returns.std() == 0:
            return 0
        return round((daily_returns.mean() / daily_returns.std()) * np.sqrt(252), 2)

    @property
    def avg_holding_days(self) -> float:
        if not self.trades:
            return 0
        return round(np.mean([t.holding_days for t in self.trades]), 1)

    def summary(self) -> dict:
        return {
            "start": self.start_date,
            "end": self.end_date,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "avg_hold_days": self.avg_holding_days,
        }


class BacktestEngine:
    """回测引擎"""

    def __init__(self, params: dict = None, trade_cost: float = 0.003):
        self.engine = TrendEngine(params)
        self.trade_cost = trade_cost

    def run(self, data_dict: dict, names_map: dict = None,
            index_df: pd.DataFrame = None) -> BacktestResult:
        """
        运行回测

        data_dict: {code: DataFrame} 日K线数据
        names_map: {code: name}
        index_df: 大盘指数日K线（用于牛熊过滤）
        """
        if names_map is None:
            names_map = {}

        # 收集所有交易日
        all_dates = set()
        for df in data_dict.values():
            all_dates.update(df.index)
        trade_dates = sorted(all_dates)

        if len(trade_dates) < 60:
            return BacktestResult([], pd.Series(), self.engine.__dict__, "", "")

        # 状态跟踪
        positions: dict[str, dict] = {}
        trades: list[Trade] = []
        equity = pd.Series(1.0, index=trade_dates)

        print(f"\n  回测日期: {trade_dates[0].strftime('%Y-%m-%d')} → {trade_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  股票: {len(data_dict)} | 交易日: {len(trade_dates)} | 手续费: {self.trade_cost:.2%}")

        # 遍历每个交易日
        for i, date in enumerate(trade_dates):
            if i < 50:
                continue

            # 更新大盘状态 (v2.1)
            if index_df is not None and date in index_df.index:
                idx_idx = index_df.index.get_loc(date)
                self.engine.set_index_data(index_df.iloc[:idx_idx+1])

            max_pos = self.engine.get_max_positions()

            # === 阶段1: 检查现有持仓是否需要离场 ===
            exit_codes = []
            for code, pos in positions.items():
                if code not in data_dict:
                    continue
                df = data_dict[code]
                if date not in df.index:
                    continue

                idx = df.index.get_loc(date)
                holding_days = (date - pos["entry_date"]).days

                # 更新入场后最高价
                high_since = df["high"].iloc[pos["entry_idx"]:idx+1].max()
                if high_since > pos["highest_since_entry"]:
                    pos["highest_since_entry"] = high_since
                    pos["trailing_stop"] = self.engine.update_trailing_stop(
                        df, high_since, idx
                    )

                # 检查离场
                exit_check = self.engine.check_exit(
                    df, idx, pos["trailing_stop"], holding_days
                )

                if exit_check["exit"]:
                    exit_price = exit_check["price"]
                    pnl = (exit_price - pos["entry_price"]) / pos["entry_price"]
                    pnl -= self.trade_cost  # 手续费

                    trades.append(Trade(
                        code=code,
                        name=names_map.get(code, code),
                        entry_date=pos["entry_date"].strftime("%Y-%m-%d"),
                        exit_date=date.strftime("%Y-%m-%d"),
                        entry_price=round(pos["entry_price"], 2),
                        exit_price=round(exit_price, 2),
                        exit_reason=exit_check["reason"],
                        pnl_pct=round(pnl * 100, 2),
                        holding_days=holding_days,
                        signals=pos.get("signals", []),
                    ))

                    equity.loc[date] = equity.loc[date] * (1 + pnl)
                    exit_codes.append(code)

            for code in exit_codes:
                del positions[code]

            # === 阶段2: 生成新信号 (v2.1: 大盘过滤) ===
            if len(positions) >= max_pos:
                continue

            # 大盘不允许开仓
            if not self.engine.can_open_position():
                continue

            candidates = []
            for code, df in data_dict.items():
                if code in positions:
                    continue
                if date not in df.index:
                    continue

                idx = df.index.get_loc(date)

                # 用当天收盘价跑策略
                temp_df = df.iloc[:idx+1].copy()
                result = self.engine.analyze(temp_df, code=code,
                                             name=names_map.get(code, code))
                if result:
                    candidates.append((code, result))

            # 按评分排序
            candidates.sort(key=lambda x: x[1]["score"], reverse=True)

            # 最多开仓 max_pos - len(positions)
            slots = max_pos - len(positions)
            for code, signal in candidates[:slots]:
                df = data_dict[code]
                idx = df.index.get_loc(date)

                # T+1 入场价 = 次日开盘价
                if idx + 1 >= len(df):
                    continue
                next_idx = idx + 1
                entry_price = float(df["open"].iloc[next_idx])

                # 检查次日是否涨停（无法买入）
                if entry_price >= df["close"].iloc[idx] * 1.098:
                    continue

                # 入场
                entry_info = self.engine.simulate_entry(
                    df.iloc[:next_idx+1], entry_price, next_idx
                )

                positions[code] = {
                    "entry_date": df.index[next_idx],
                    "entry_idx": next_idx,
                    "entry_price": entry_price,
                    "trailing_stop": entry_info["trailing_stop"],
                    "highest_since_entry": entry_price,
                    "signals": signal.get("signals", []),
                }

        # === 清理未平仓（按最后一天收盘价） ===
        last_date = trade_dates[-1]
        for code, pos in positions.items():
            if code not in data_dict:
                continue
            df = data_dict[code]
            if last_date in df.index:
                exit_price = float(df["close"].loc[last_date])
                pnl = (exit_price - pos["entry_price"]) / pos["entry_price"]
                pnl -= self.trade_cost
                holding_days = (last_date - pos["entry_date"]).days

                trades.append(Trade(
                    code=code,
                    name=names_map.get(code, code),
                    entry_date=pos["entry_date"].strftime("%Y-%m-%d"),
                    exit_date=last_date.strftime("%Y-%m-%d"),
                    entry_price=round(pos["entry_price"], 2),
                    exit_price=round(exit_price, 2),
                    exit_reason="回测结束",
                    pnl_pct=round(pnl * 100, 2),
                    holding_days=holding_days,
                    signals=pos.get("signals", []),
                ))

        # 计算净值曲线
        if trades:
            cum_return = 1.0
            for date in trade_dates:
                day_trades = [t for t in trades
                             if t.exit_date == date.strftime("%Y-%m-%d")]
                for t in day_trades:
                    cum_return *= (1 + t.pnl_pct / 100)
                equity.loc[date] = cum_return
            equity = equity.ffill().fillna(1.0)

        result = BacktestResult(
            trades=trades,
            equity_curve=equity,
            params=self.engine.__dict__,
            start_date=trade_dates[0].strftime("%Y-%m-%d"),
            end_date=trade_dates[-1].strftime("%Y-%m-%d"),
        )

        return result
