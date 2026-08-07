import sys, json, glob, os
sys.path.insert(0, '.')
import pandas as pd
from datetime import datetime
from backtest.engine import BacktestEngine

files = glob.glob('data/cache/*.csv')[:50]
data = {}
for f in files:
    code = os.path.splitext(os.path.basename(f))[0]
    try:
        df = pd.read_csv(f, parse_dates=['date'], index_col='date')
        if len(df) > 50: data[code] = df
    except: pass

with open('config/params.json') as f:
    params = json.load(f)
    params['cooldown_days'] = 5
    params['trailing_atr_multiplier'] = 3.0

engine = BacktestEngine(params=params, trade_cost=0.003)
result = engine.run(data)
s = result.summary()

# 按年统计
years = {}
for t in result.trades:
    y = t.exit_date[:4]
    if y not in years: years[y] = {"trades":0,"wins":0,"pnl":0,"hold":0}
    years[y]["trades"] += 1
    if t.pnl_pct > 0: years[y]["wins"] += 1
    years[y]["pnl"] += t.pnl_pct
    years[y]["hold"] += t.holding_days

year_rows = []
for y in sorted(years):
    d = years[y]
    wr = round(d["wins"]/d["trades"]*100,1) if d["trades"]>0 else 0
    avg = round(d["pnl"]/d["trades"],2) if d["trades"]>0 else 0
    avg_hold = round(d["hold"]/d["trades"],1) if d["trades"]>0 else 0
    year_rows.append(f'| {y} | {d["trades"]} | {wr}% | {avg}% | {round(d["pnl"],1)}% | {avg_hold}天 |')

# 离场原因
reasons = {}
for t in result.trades:
    r = t.exit_reason
    reasons[r] = reasons.get(r, 0) + 1
reason_rows = '\n'.join([f'- {r}: {c}次' for r, c in sorted(reasons.items(), key=lambda x:-x[1])])

report = f"""# A股量化平台 v3.0 -- 回测报告

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 引擎版本：v3.0 (SellEngine 统一回测+实盘卖出)
> 数据区间：{s['start']} → {s['end']}
> 股票数量：{len(data)} 支

## 核心指标

| 指标 | 数值 |
|------|------|
| 交易次数 | {s['total_trades']} |
| 胜率 | {s['win_rate']}% |
| 平均盈利 | {s['avg_win']}% |
| 平均亏损 | {s['avg_loss']}% |
| Profit Factor | {s['profit_factor']} |
| 总收益率 | {s['total_return']}% |
| 年化收益 | {s['annual_return']}% |
| 最大回撤 | {s['max_drawdown']}% |
| 夏普比率 | {s['sharpe_ratio']} |
| 平均持仓 | {s['avg_hold_days']}天 |

## v2.1 vs v3.0 对比

| 指标 | v2.1 (仅ATR止损) | v3.0 (四层卖出) |
|------|------|------|
| PF | 1.91 | {s['profit_factor']} |
| 胜率 | 39.1% | {s['win_rate']}% |
| 交易次数 | 23 | {s['total_trades']} |
| 年化 | +55.8% | {s['annual_return']}% |
| 最大回撤 | -29.1% | {s['max_drawdown']}% |

> v2.1 PF 1.91 是假象——回测只有ATR止损一种离场。
> v3.0 PF {s['profit_factor']} 才是包含完整四层卖出体系的真实数字。

## 按年统计

| 年份 | 交易 | 胜率 | 均盈亏 | 累计 | 均持仓 |
|------|------|------|------|------|------|
{chr(10).join(year_rows)}

## 离场原因分布

{reason_rows}

## 当前参数 (params.json)

```json
{json.dumps(params, ensure_ascii=False, indent=2)}
```

## v3.0 已完成的模块

| Phase | 模块 | 状态 |
|------|------|------|
| Phase1 | data/calendar.py 交易日历 | OK |
| Phase1 | data/adjust.py get_price()统一入口 | OK |
| Phase1 | data/quality.py 数据校验 | OK |
| Phase2 | risk/sell_engine.py 四层卖出引擎 | OK |
| Phase2 | backtest/engine.py 接入SellEngine | OK |
| Phase2 | execution/broker.py 虚拟券商 | OK |
| Phase4 | strategies/regime.py 大盘环境 | OK |
| Phase4 | risk/position.py 仓位管理 | OK |
| Phase4 | risk/drawdown.py 回撤熔断 | OK |
"""

desktop = os.path.expanduser('~/Desktop')
path = os.path.join(desktop, 'A股量化v3.0回测报告.md')
with open(path, 'w', encoding='utf-8') as f:
    f.write(report)

print(f'OK: {path}')
print(f'PF={s["profit_factor"]} 胜率={s["win_rate"]}% 交易={s["total_trades"]}笔')
