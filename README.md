# A-Share Quant Platform v3.0 (修复版)

> A股量化平台 —— 单一事实来源（SellEngine 统一回测/实盘）+ Walk-Forward 样本外验证
> 本版本基于 11 文件夹代码审查修复：P0-A SellEngine 真接入、P0-B 大盘过滤逐日生效、P1×3 已修

## 快速开始

```bash
# 1. 数据准备：将 A 股日K CSV（含 open/high/low/close/volume/date）放入 data/cache/{code}.csv
#    （列名 date/open/high/low/close/volume，date 为 YYYY-MM-DD）

# 2. 跑 Walk-Forward 样本外验证
python run_wf.py

# 3. 跑测试
python -m pytest tests/test_parity.py -v   # 或直接 python tests/run_tests.py
```

## 目录

```
config/params.json        # 策略参数（含 sell_config 四层卖出配置）
data/adjust.py            # get_price 唯一数据门面（前复权）
data/calendar.py          # 交易日历
data/universe.py          # point-in-time 股票池
strategies/indicators.py  # 指标库（纯 pandas）
strategies/trend_engine.py# 趋势策略引擎（大盘过滤+仓位管理）
risk/sell_engine.py       # 四层卖出引擎（回测/实盘共用）
execution/broker.py       # 虚拟券商（涨跌停/停牌/T+1）
execution/cost.py         # 成本模型
backtest/engine.py        # 事件驱动回测引擎（SellEngine 真接入）
backtest/metrics.py       # 绩效指标
validation/walkforward.py # Walk-Forward 滚动验证
tests/test_parity.py      # 对拍测试
run_wf.py                 # WF 运行入口
```

## 关键设计

- **单一事实来源**：回测引擎调用 `risk.sell_engine.evaluate()`，实盘 runner 也调它——同一份卖出逻辑
- **大盘过滤逐日生效**：每个交易日用当日指数切片更新 regime，bear 空仓
- **净值按日估值**：含持仓浮亏，回撤真实
- **止损基于实际成交价**：买入成交后重算止损线

> ⚠️ 本项目仅供学习研究，不构成任何投资建议。过往业绩不代表未来表现。
