"""
月度回测健康检查 v2 — 49支低价股池 (与推送/模拟盘同口径)
[修复 2026-09-22] 旧版内联脚本用沪深300前50支, 与实际交易体系脱节
安全门: 近6月回测 年化>0 且 PF>1.0 且 胜率>35% → 具备实盘资格
运行: python scripts/monthly_check.py   (GitHub Actions 每月1号 16:00 北京时间)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from data.universe import get_universe, STOCK_NAMES
from daily_push import fetch_data
from backtest.engine import BacktestEngine
from backtest.metrics import print_report
from notification.feishu import send_to_feishu


def main():
    end = datetime.now()
    start = end - timedelta(days=180)
    warmup_start = start - timedelta(days=120)  # 指标预热(MA60/量比20日等)
    print(f"月度回测(49支低价股池): {start:%Y-%m-%d} → {end:%Y-%m-%d}")

    # 1. 股票池: 与推送/模拟盘一致
    codes = get_universe()[:50]

    # 2. 数据: 联网拉取 (Actions 无本地缓存), 盘中自动剔除当日半截K线
    data_all = fetch_data(codes)
    print(f"获取: {len(data_all)}/{len(codes)} 支")
    if len(data_all) < 40:
        print(f"数据不足({len(data_all)}支), 退出")
        sys.exit(1)

    # 3. 切6个月回测窗口 (预留预热期)
    data = {}
    for code, df in data_all.items():
        w = df.loc[str(warmup_start.date()):str(end.date())]
        if len(w) > 60:
            data[code] = w
    names = {c: STOCK_NAMES.get(c, c) for c in data}

    # 4. 回测
    with open("config/params.json") as f:
        params = json.load(f)
    engine = BacktestEngine(params=params, trade_cost=0.003)
    result = engine.run(data, names_map=names)
    print_report(result)
    s = result.summary()

    # 5. 历史留档 (追加)
    os.makedirs("reports", exist_ok=True)
    report_file = "reports/monthly_backtest.json"
    history = []
    if os.path.exists(report_file):
        with open(report_file) as f:
            history = json.load(f)
    report = {
        "date": end.strftime("%Y-%m-%d"),
        "window": f"{start:%Y-%m-%d} → {end:%Y-%m-%d}",
        "pool": "49支低价股池",
        "stocks": len(data),
        **s,
    }
    history.append(report)
    with open(report_file, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # 6. 安全门判定
    annual = s.get("annual_return", 0)
    pf = s.get("profit_factor", 0)
    wr = s.get("win_rate", 0)
    if annual > 0 and pf > 1.0 and wr > 35:
        status = "🟢 健康 — 建议切换实盘"
    elif annual > -10:
        status = "🟡 观察 — 接近转正"
    else:
        status = "🔴 等待 — 继续模拟"

    # 7. 飞书月报
    msg_recs = [{
        "code": "BACKTEST",
        "name": f"月度回测 {end:%Y-%m} (49支池)",
        "entry_price": 0, "stop_loss": 0, "stop_pct": 0,
        "target_price": 0, "target_pct": 0, "holding_days": 0,
        "score": pf * 30,
        "confidence": "HIGH" if annual > 0 else "LOW",
        "signals": [
            f"年化{annual}%",
            f"PF{pf}",
            f"胜率{wr}%",
            f"回撤{s.get('max_drawdown', 0)}%",
            status,
        ],
    }]
    mock_stats = {
        "total": s.get("total_trades", 0),
        "resolved": s.get("total_trades", 0),
        "pending": 0, "wr": wr, "pf": pf,
    }
    ok = send_to_feishu(msg_recs, mock_stats)
    print(f"状态: {status}")
    if not ok:
        print("[ERROR] 飞书推送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
