# A-Share Quant Platform v3.0

> A股量化框架 —— 单一事实来源 + Walk-Forward 验证 + 风控三层 + 模拟盘全链路
> 
> **框架是资产，策略是消耗品。** 当前趋势策略年化 ~1.36%（练手级），但验证框架可复用任何新策略方向。

## 工程成就（v3.0）

| 模块 | 能力 | 状态 |
|------|------|------|
| 单一事实来源 | `risk/sell_engine.py` — 回测/实盘同一份卖出逻辑 | ✅ |
| Walk-Forward | `validation/walkforward.py` — 12M训练/3M测试/3M步进 | ✅ |
| 风控三层 | regime仓位控制 + drawdown熔断 + cooldown冷却 | ✅ |
| 模拟盘 | `paper_trade.py` — 信号→成交→账本全链路验证 | ✅ |
| 实验框架 | `experiments.py` — 改参→WF→对比，防止样本内过拟合 | ✅ |

## 快速开始

```bash
# Walk-Forward 样本外验证
python run_wf.py

# 模拟盘（执行链路验证）
python paper_trade.py

# 优化实验
python experiments.py
```

## 架构

```
config/params.json         # 策略参数（含 sell_config 四层卖出配置）
data/
  adjust.py                # get_price() 唯一数据门面（缓存+前复权）
  calendar.py              # 交易日历 2020-2027
  universe.py              # point-in-time 股票池（防生存偏差）
  akshare_fetcher.py       # AKShare 数据拉取（东财+新浪）
strategies/
  indicators.py            # 指标库（纯 pandas）
  trend_engine.py          # 趋势策略引擎（大盘过滤+仓位管理+评分）
  filters.py               # 股票池过滤
  regime.py                # 大盘状态判断
  scoring.py               # 评分排序
risk/
  sell_engine.py           # 四层卖出引擎（回测/实盘单一来源）
  drawdown.py              # 回撤熔断（10%减半/15%清仓/25%停摆）
  position.py              # 仓位管理
execution/
  broker.py                # 虚拟券商（涨跌停/停牌/T+1/滑点）
  cost.py                  # 成本模型（佣金+印花税+滑点）
backtest/
  engine.py                # 事件驱动回测引擎
  metrics.py               # 绩效指标（PF/夏普/年化/回撤）
validation/
  walkforward.py           # Walk-Forward 滚动验证
position/
  position_manager.py      # 持仓管理
  trades.csv               # 交易账本
notification/
  feishu.py                # 飞书 Bot 推送
tracker/
  predictor.py             # 预测追踪
paper_trade.py             # 模拟盘脚本
experiments.py             # 参数实验框架
run_wf.py                  # WF 运行入口
runner.py                  # 主入口（回测/实时）
```

## 当前策略参数（v3.0-final）

| 参数 | 值 | 说明 |
|------|-----|------|
| volume_ratio | 1.5 | 量比过滤（实验验证的最优值） |
| atr_period | 14 | ATR 周期 |
| st_multiplier | 3.0 | Supertrend 乘数 |
| rsi_threshold | 40 | RSI 下限 |
| single_position_pct | 0.10 | 单票仓位比例 |
| max_hold_days | 20 | 最大持仓天数 |
| cooldown_days | 5 | 止损后冷却期 |
| bull_positions | 3 | 牛市最大仓位 |
| sideways_positions | 1 | 震荡市最大仓位 |
| bear_positions | 0 | 熊市空仓 |

## 关键设计原则

1. **单一事实来源**：`risk/sell_engine.py` 的 `evaluate()` 函数是卖出决策的唯一来源——回测和实盘都调用它
2. **T+1 执行**：T日收盘生成信号 → T+1日开盘执行（防未来函数）
3. **净值按日估值**：含持仓浮亏，回撤真实
4. **Point-in-time universe**：防生存偏差
5. **卖出份额≠0**：`broker.py` 要求显式传份额，shares=0 会被拒单（v3.0 关键 bug 修复）

## Bug 修复记录

| 日期 | Bug | 根因 | 影响 |
|------|-----|------|------|
| 2026-08-08 | 卖单静默拒单 | `Order(shares=0)` → Broker返回None | 持仓257天→8.1天 |
| 2026-08-08 | WF汇总失真 | 跨窗复利拼接净值 | 年化150%→0.84% |
| 2026-08-08 | 双低WF净值归零 | 调仓日落周末→查询返回None | 回撤-100%→真实值 |

> ⚠️ 本项目仅供学习研究，不构成任何投资建议。过往业绩不代表未来表现。

## 策略终局（v3.1 三赛道验证完毕）

| 策略 | 年化 | PF | 回撤 | 样本 | 状态 |
|------|------|-----|------|------|------|
| 个股趋势 v3.0 | +1.36% | 3.72 | -1.8% | 112 | ✅ 唯一正期望，模拟盘验证中 |
| 双低轮动 v0.2 | -2.84% | 0.70 | -24.3% | 70 | ❌ 正式关闭（PF<1.0） |
| CB ETF MA趋势 | +1.5% | — | -7% | <10 | ❌ 低频无法验证，关闭 |

> **三条路走完的教训**：1万本金 × 自动化策略的收益天花板就是低——不是框架问题，不是赛道问题，是资金规模 × 策略类型的天花板。框架本身（WF验证/风控三层/PIT池/实验框架）是这两天真正的资产。

## 可转债模块（已关闭，代码保留）

```
data/cb_cache/             1043支历史溢价率 (9分钟全量拉取)
data/cb_fetcher.py          断点续传拉取工具
data/cb_universe.py         Point-in-time池 + 强赎标注
execution/cost_cb.py        转债成本模型 (无印花税+万1佣金)
execution/broker_cb.py      转债券商 (±20%涨跌停+T+0+张单位)
strategies/dual_low.py      双低轮动引擎 (OOS PF 0.70，已关闭)
validation/wf_dual_low.py   WF适配器
```
> 赛道已关闭但工程资产保留——PIT池/强赎处理/成本模型可复用。
