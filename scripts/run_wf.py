import json, glob, os, sys
sys.path.insert(0, '.')
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from backtest.engine import BacktestEngine

files = glob.glob('data/cache/*.csv')[:20]
data_dict = {}
for f in files:
    code = os.path.splitext(os.path.basename(f))[0]
    try:
        df = pd.read_csv(f, parse_dates=['date'], index_col='date')
        if len(df) > 500:
            data_dict[code] = df
    except:
        pass
print(f'{len(data_dict)} stocks')

base_params = json.load(open('config/params.json'))
base_params['cooldown_days'] = 5
base_params['trailing_atr_multiplier'] = 3.0

all_trades = []
cursor = datetime(2023, 1, 1)
end = datetime(2026, 7, 1)
wi = 0

while cursor + relativedelta(months=15) <= end:
    t1 = cursor
    t2 = cursor + relativedelta(months=12)
    t3 = t2
    t4 = t2 + relativedelta(months=3)

    wd = {}
    for code, df in data_dict.items():
        mask = (df.index >= t1.strftime('%Y-%m-%d')) & (df.index <= t4.strftime('%Y-%m-%d'))
        sub = df[mask]
        if len(sub) > 50:
            wd[code] = sub

    if len(wd) >= 5:
        print(f'W{wi+1}: {t1.strftime("%Y-%m")}→{t2.strftime("%Y-%m")}|{t3.strftime("%Y-%m")}→{t4.strftime("%Y-%m")} ...', end=' ', flush=True)
        engine = BacktestEngine(params=base_params, trade_cost=0.003)
        r = engine.run(wd)
        sr = r.summary()
        all_trades.extend(r.trades)
        wi += 1
        print(f'PF={sr["profit_factor"]} T={sr["total_trades"]} Ann={sr["annual_return"]}%')

    cursor += relativedelta(months=3)

total = len(all_trades)
if total > 0:
    wins = [t for t in all_trades if t.pnl_pct > 0]
    losses = [t for t in all_trades if t.pnl_pct <= 0]
    wr = round(len(wins) / total * 100, 1)
    pf = round(sum(t.pnl_pct for t in wins) / abs(sum(t.pnl_pct for t in losses)), 2) if losses else 0
    print(f'\n=== OOS: PF={pf} WR={wr}% T={total} ===')
else:
    print('No trades')
