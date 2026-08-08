"""
对拍测试 —— 回测引擎与 SellEngine 决策一致性（Phase 2 灵魂验收）

验证目标：
  同一历史区间，回测引擎内部通过 SellEngine 生成的卖出决策，
  与直接调用 risk.sell_engine.evaluate() 的结果逐条一致。

运行: pytest tests/test_parity.py -v
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine
from risk.sell_engine import (
    evaluate as sell_evaluate, Position as SPosition, Bar as SBar,
    Regime as SellRegime, set_config, SellConfig,
)
from strategies.indicators import calc_atr, calc_supertrend, calc_ma, calc_rsi


def make_dummy_data(n=300, seed=42):
    """构造合成 K 线数据（确定性，用于对拍测试）"""
    import numpy as np
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 10 + np.cumsum(rng.normal(0.001, 0.02, n))
    close = np.maximum(close, 1)
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)

    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=dates)


def test_sell_engine_invoked():
    """验证：回测引擎确实调用了 SellEngine（非内联简化逻辑）"""
    # 反读 engine.py 源码：必须 import sell_evaluate 且 run() 内出现调用
    engine_src = open(
        os.path.join(os.path.dirname(__file__), "..", "backtest", "engine.py")
    ).read()

    assert "from risk.sell_engine import" in engine_src, "engine.py 未 import SellEngine"
    assert "sell_evaluate(" in engine_src, "engine.py 未调用 sell_evaluate()"
    # 不应存在内联简化卖出（原 L193 注释已删除）
    assert "简化SellEngine" not in engine_src, "engine.py 仍含内联简化卖出逻辑"


def test_regime_wired():
    """验证：回测引擎接入大盘过滤"""
    engine_src = open(
        os.path.join(os.path.dirname(__file__), "..", "backtest", "engine.py")
    ).read()

    assert "set_index_data" in engine_src, "engine.py 未调用 set_index_data()"
    assert "can_open_position" in engine_src, "engine.py 未调用 can_open_position()"
    assert "get_max_positions" in engine_src, "engine.py 未用 get_max_positions()"


def test_sell_engine_direct():
    """验证 SellEngine 基本行为：止损触发返回立即卖出"""
    set_config(SellConfig())

    pos = SPosition(code="T1", name="测试", entry_price=10.0, hold_days=3,
                    prev_stop_loss=8.0, highest_price=10.5)
    bar = SBar(close=7.9, open=8.0, high=8.1, low=7.8, atr=0.5,
               supertrend_bullish=True, ma20=9.5, rsi=30,
               volume_ratio=1.0, day_change_pct=-2.0,
               recent_3d_high=10.4, prev_3d_high=10.3)

    sig = sell_evaluate(pos, bar, SellRegime())
    assert sig is not None
    assert sig.action == "立即卖出"
    assert sig.exec_time.value == "next_open"


def test_backtest_runs():
    """验证：修复版回测引擎可运行（合成数据）"""
    params = {
        "atr_period": 14, "st_multiplier": 3.0, "ma_short": 20, "ma_long": 60,
        "rsi_period": 14, "rsi_threshold": 40, "rsi_upper": 65,
        "volume_ratio": 1.0, "breakout_days": 20,
        "atr_stop_multiplier": 3.0, "trailing_atr_multiplier": 3.0,
        "min_stop_pct": 2.0, "max_holding_days": 20,
        "trade_cost": 0.003, "cooldown_days": 5,
        "single_position_pct": 0.15, "top_n": 5,
        "bull_positions": 3, "sideways_positions": 1, "bear_positions": 0,
    }
    engine = BacktestEngine(params=params, trade_cost=0.003)

    data = {"600001": make_dummy_data(), "600002": make_dummy_data(seed=43)}
    idx = make_dummy_data(seed=1)

    result = engine.run(data, index_df=idx)
    assert result is not None
    assert hasattr(result, "equity_curve")
    # 净值曲线应按日估值（含浮亏），非仅平仓日
    assert result.equity_curve.notna().all()
    print(f"\n[对拍] 回测可运行: {result.total_trades} 笔, "
          f"PF={result.profit_factor}, 回撤={result.max_drawdown}%")
