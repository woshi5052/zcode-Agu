#!/usr/bin/env python3
"""
A股量化平台 —— 主运行入口
模式: 实时推送 / 历史回测
"""

import json
import sys
import argparse
from datetime import datetime

from config.settings import (
    DEFAULT_PARAMS, REPORTS_DIR, FEISHU_ENABLED,
    AI_ENABLED, STOCK_POOL, IS_GITHUB_ACTIONS,
)
from data.akshare_fetcher import (
    get_hs300_stocks, fetch_stock_pool_data,
)
from strategies.filters import stock_pool_filter
from strategies.scoring import run_trend_analysis
from notification.feishu import send_to_feishu
from tracker.predictor import (
    add_predictions, check_predictions, save_recommendations,
)
from ai.sentiment import enhance_with_sentiment, filter_by_sentiment


def load_params() -> dict:
    params_file = REPORTS_DIR.parent / "config" / "params.json"
    try:
        with open(params_file, "r") as f:
            return {k: v for k, v in json.load(f).items()
                    if not k.startswith("_")}
    except Exception:
        return DEFAULT_PARAMS


def step(label: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def run_live(stock_count: int = 50):
    """实时模式：获取数据 → 策略分析 → 飞书推送"""
    params = load_params()

    print(f"\n  📊 A-Share Quant Platform v2.0 (实时)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  股票池: {STOCK_POOL} | 飞书: {'启用' if FEISHU_ENABLED else '未启用'}")

    # 获取数据
    step("Step 1: 获取数据")
    stocks = get_hs300_stocks()
    codes = stocks['code'].tolist()[:stock_count]
    names = dict(zip(stocks['code'], stocks['name']))
    print(f"  成分股: {len(codes)} 支")

    data = fetch_stock_pool_data(codes, max_stocks=stock_count)
    if not data:
        print("[ERROR] 无数据")
        return

    # 过滤
    step("Step 2: A股过滤")
    filtered = stock_pool_filter(data, names_map=names)

    # 策略
    step("Step 3: 策略分析 (v2.0 移动止损)")
    results = run_trend_analysis(
        filtered, names_map=names,
        top_n=params.get("top_n", 5), params=params,
    )

    # AI增强
    if AI_ENABLED and results:
        step("Step 4: AI增强")
        results = enhance_with_sentiment(results)
        results = filter_by_sentiment(results)
    else:
        print("\n[跳过] AI增强未启用")

    # 保存
    step("Step 5: 保存 & 追踪")
    save_recommendations(results)
    add_predictions(results, holding_days=params.get("max_holding_days", 20))
    stats = check_predictions(data)

    # 飞书
    step("Step 6: 飞书推送")
    send_to_feishu(results, stats)

    # 摘要
    print(f"\n  ✅ 完成: {len(data)}支 → {len(filtered)}过滤 → {len(results)}推荐")
    if results:
        for i, r in enumerate(results, 1):
            print(f"    {i}. {r['name']}({r['code']}) 评分{r['score']} "
                  f"¥{r['entry_price']} 止损-{r['stop_pct']}% {r['confidence']}")


def run_backtest(start: str, end: str, stock_count: int = 50):
    """回测模式"""
    from backtest.engine import BacktestEngine
    from backtest.metrics import print_report

    params = load_params()

    print(f"\n  📊 A-Share Quant Platform v2.0 (回测)")
    print(f"  区间: {start} → {end} | 股票数: {stock_count}")

    # 获取成分股
    step("Step 1: 获取成分股")
    stocks = get_hs300_stocks()
    codes = stocks['code'].tolist()[:stock_count]
    names = dict(zip(stocks['code'], stocks['name']))

    # 获取历史数据（回溯到start之前至少250天）
    from datetime import timedelta
    import time as _time
    import akshare as ak
    import pandas as pd
    from data.akshare_fetcher import _format_code

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    fetch_start = (start_dt - timedelta(days=400)).strftime("%Y-%m-%d")

    step("Step 2: 获取历史K线")
    data = {}
    print(f"  拉取 {len(codes)} 支股票 {fetch_start} → {end} 日K线...")

    for i, code in enumerate(codes):
        print(f"\r  数据: {i+1}/{len(codes)} {code}", end="", flush=True)
        try:
            df = ak.stock_zh_a_daily(
                symbol=_format_code(code),
                adjust="qfq",
            )
            if df is not None and len(df) > 100:
                df = df.rename(columns={
                    "date": "date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "volume",
                })
                if "amount" not in df.columns:
                    df["amount"] = df["close"] * df["volume"]
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                # 截取回测区间
                mask = df.index >= fetch_start
                df = df[mask]
                if len(df) > 100:
                    data[code] = df
        except Exception as e:
            print(f"\n  [WARN] {code} 失败: {e}")
        _time.sleep(0.3)

    import pandas as pd
    print(f"\n  获取成功: {len(data)}/{len(codes)} 支")

    if not data:
        print("[ERROR] 无数据可回测")
        return

    # 回测
    step("Step 3: 运行回测")
    engine = BacktestEngine(params=params,
                            trade_cost=params.get("trade_cost", 0.003))
    result = engine.run(data, names_map=names)

    # 报告
    step("Step 4: 回测报告")
    print_report(result)

    return result


def main():
    parser = argparse.ArgumentParser(description="A-Share Quant Platform")
    parser.add_argument("--backtest", action="store_true", help="回测模式")
    parser.add_argument("--start", type=str, default="2024-01-01", help="回测开始日期")
    parser.add_argument("--end", type=str, default="2026-07-01", help="回测结束日期")
    parser.add_argument("--stocks", type=int, default=50, help="回测股票数量")
    args = parser.parse_args()

    if args.backtest:
        run_backtest(args.start, args.end, args.stocks)
    else:
        run_live(stock_count=50)


if __name__ == "__main__":
    main()
