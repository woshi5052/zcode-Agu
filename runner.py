#!/usr/bin/env python3
"""
A股量化平台 v2.1 —— 大盘过滤 + 统一止损 + 仓位管理
"""

import json
import sys
import argparse
import time as _time
from datetime import datetime, timedelta

from config.settings import (
    DEFAULT_PARAMS, REPORTS_DIR, FEISHU_ENABLED,
    AI_ENABLED, STOCK_POOL, IS_GITHUB_ACTIONS,
)
from data.akshare_fetcher import (
    get_hs300_stocks, fetch_stock_pool_data, _format_code,
)
from strategies.filters import stock_pool_filter
from strategies.scoring import run_trend_analysis
from strategies.trend_engine import TrendEngine
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


def fetch_index_data(data_dict: dict = None) -> "pd.DataFrame | None":
    """获取大盘指数数据，优先ETF，失败用股票池合成"""
    import akshare as ak
    import pandas as pd

    # 方法1: 沪深300ETF
    for symbol in ["sh510300", "sh000300"]:
        try:
            df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
            if df is not None and len(df) > 50:
                df = df.rename(columns={
                    "date": "date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "volume",
                })
                if "amount" not in df.columns:
                    df["amount"] = df["close"] * df["volume"]
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                print(f"  指数: ETF {symbol} {len(df)}条")
                return df
        except Exception:
            pass

    # 方法2: 用股票池等权合成
    if data_dict and len(data_dict) >= 5:
        print("  指数: 合成(股票池等权)...")
        closes = []
        for df in data_dict.values():
            closes.append(df["close"])
        if closes:
            all_dates = sorted(set().union(*[c.index for c in closes]))
            synth = pd.DataFrame(index=all_dates)
            synth["close"] = 0
            count = 0
            for c in closes:
                aligned = c.reindex(all_dates)
                synth["close"] += aligned.fillna(method="ffill").fillna(0)
                count += 1
            synth["close"] /= count
            synth["open"] = synth["close"]
            synth["high"] = synth["close"]
            synth["low"] = synth["close"]
            synth["volume"] = 1
            synth["amount"] = 1
            print(f"  合成指数: {len(synth)}条")
            return synth

    return None


def step(label: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def run_live(stock_count: int = 50):
    """实时模式"""
    params = load_params()

    print(f"\n  📊 A-Share Quant Platform v2.1")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  股票池: {STOCK_POOL} | 飞书: {'启用' if FEISHU_ENABLED else '未启用'}")

    # 获取数据
    step("Step 1: 获取数据")
    stocks = get_hs300_stocks()
    codes = stocks['code'].tolist()[:stock_count]
    names = dict(zip(stocks['code'], stocks['name']))

    data = fetch_stock_pool_data(codes, max_stocks=stock_count)
    if not data:
        print("[ERROR] 无数据")
        return

    # 获取指数 (需要数据池)
    step("Step 0: 大盘状态")
    index_df = fetch_index_data(data)
    engine = TrendEngine(params)
    if index_df is not None:
        engine.set_index_data(index_df)
    regime = engine.get_regime()
    max_pos = engine.get_max_positions()
    can_open = engine.can_open_position()
    print(f"  大盘: {regime} | 仓位: {max_pos} | {'可开仓' if can_open else '禁止开仓'}")

    # 过滤
    step("Step 2: A股过滤")
    filtered = stock_pool_filter(data, names_map=names)

    # 策略
    step("Step 3: 策略分析")
    results = run_trend_analysis(
        filtered, names_map=names,
        top_n=params.get("top_n", 5), params=params,
    )

    # AI增强
    if AI_ENABLED and results:
        step("Step 4: AI增强")
        results = enhance_with_sentiment(results)
        results = filter_by_sentiment(results)

    # 保存追踪
    step("Step 5: 保存 & 追踪")
    save_recommendations(results)
    add_predictions(results, holding_days=params.get("max_holding_days", 20))
    stats = check_predictions(data)

    # 飞书推送
    step("Step 6: 飞书推送")
    regime_tag = f"[{regime}]" if index_df is not None else ""
    msg = results if can_open else []  # 大盘禁开时不推推荐
    send_to_feishu(msg, stats)

    # 摘要
    print(f"\n  ✅ 完成: 大盘{regime} | {len(data)}支→{len(results)}推荐")
    if results:
        for i, r in enumerate(results, 1):
            print(f"    {i}. {r['name']}({r['code']}) ¥{r['entry_price']} "
                  f"止损-{r['stop_pct']}% 评分{r['score']} {r['confidence']}")


def run_backtest(start: str, end: str, stock_count: int = 50):
    """回测模式"""
    from backtest.engine import BacktestEngine
    from backtest.metrics import print_report
    import akshare as ak
    import pandas as pd

    params = load_params()

    print(f"\n  📊 A-Share Quant Platform v2.1 (回测)")
    print(f"  区间: {start} → {end} | 股票: {stock_count}")

    # 获取成分股
    step("Step 1: 获取成分股+指数")
    stocks = get_hs300_stocks()
    codes = stocks['code'].tolist()[:stock_count]
    names = dict(zip(stocks['code'], stocks['name']))

    # 获取指数（合成）
    index_df = fetch_index_data(data)
    if index_df is None:
        print("  [WARN] 无指数数据，跳过过滤")

    # 获取个股数据
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    fetch_start = (start_dt - timedelta(days=400)).strftime("%Y-%m-%d")

    step("Step 2: 获取个股K线")
    data = {}
    print(f"  拉取 {len(codes)} 支...")
    for i, code in enumerate(codes):
        print(f"\r  数据: {i+1}/{len(codes)} {code}", end="", flush=True)
        try:
            df = ak.stock_zh_a_daily(symbol=_format_code(code), adjust="qfq")
            if df is not None and len(df) > 100:
                df = df.rename(columns={
                    "date": "date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "volume",
                })
                if "amount" not in df.columns:
                    df["amount"] = df["close"] * df["volume"]
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                mask = df.index >= fetch_start
                df = df[mask]
                if len(df) > 100:
                    data[code] = df
        except Exception as e:
            pass
        _time.sleep(0.25)

    print(f"\n  获取: {len(data)}/{len(codes)} 支")

    if len(data) < 5:
        print("[ERROR] 数据不足")
        return

    # 回测
    step("Step 3: 运行回测 (大盘过滤+仓位管理)")
    engine = BacktestEngine(params=params, trade_cost=params.get("trade_cost", 0.003))
    result = engine.run(data, names_map=names, index_df=index_df)

    # 报告
    step("Step 4: 回测报告")
    print_report(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-07-01")
    parser.add_argument("--stocks", type=int, default=50)
    args = parser.parse_args()

    if args.backtest:
        run_backtest(args.start, args.end, args.stocks)
    else:
        run_live(stock_count=50)


if __name__ == "__main__":
    main()
