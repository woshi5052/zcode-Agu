#!/usr/bin/env python3
"""
A股量化平台 —— 主运行入口
每日流水线: 数据获取 → 过滤 → 策略分析 → AI增强 → 推送飞书
"""

import json
import sys
from datetime import datetime

from config.settings import (
    DEFAULT_PARAMS, REPORTS_DIR, FEISHU_ENABLED,
    AI_ENABLED, STOCK_POOL, IS_GITHUB_ACTIONS,
)
from data.akshare_fetcher import (
    get_hs300_stocks, fetch_stock_pool_data, get_stock_basic_info,
)
from strategies.filters import stock_pool_filter, run_all_filters
from strategies.scoring import run_trend_analysis, run_momentum_analysis, combine_scores
from notification.feishu import send_to_feishu
from tracker.predictor import (
    add_predictions, check_predictions, save_recommendations, load_predictions,
)
from ai.sentiment import enhance_with_sentiment, filter_by_sentiment


def load_params() -> dict:
    """加载策略参数"""
    params_file = REPORTS_DIR.parent / "config" / "params.json"
    try:
        with open(params_file, "r") as f:
            data = json.load(f)
            # 移除注释字段
            return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return DEFAULT_PARAMS


def step(label: str):
    """打印步骤标题"""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def main():
    print(f"\n{'='*60}")
    print(f"  📊 A-Share Quant Platform")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  股票池: {STOCK_POOL}")
    print(f"  飞书: {'启用' if FEISHU_ENABLED else '未启用'}")
    print(f"  AI增强: {'可用' if AI_ENABLED else '未启用'}")
    print(f"  环境: {'GitHub Actions' if IS_GITHUB_ACTIONS else '本地'}")
    print(f"{'='*60}")

    params = load_params()

    # ================================================
    # Step 1: 获取数据
    # ================================================
    step("Step 1: 获取沪深300成分股 + 日K线数据")

    print("  获取成分股列表...")
    stock_list_df = get_hs300_stocks()
    if stock_list_df.empty:
        print("[ERROR] 无法获取成分股列表，退出")
        sys.exit(1)

    stock_codes = stock_list_df["code"].tolist()
    stock_names = dict(zip(stock_list_df["code"], stock_list_df["name"]))
    print(f"  成分股数量: {len(stock_codes)}")

    print("  批量获取日K线...")
    data_dict = fetch_stock_pool_data(stock_codes, max_stocks=300)

    if not data_dict:
        print("[ERROR] 无法获取任何K线数据，退出")
        sys.exit(1)

    # ================================================
    # Step 2: A股过滤
    # ================================================
    step("Step 2: A股过滤 (ST/停牌/涨跌停/流动性/次新股)")

    filtered = stock_pool_filter(data_dict, names_map=stock_names)

    if not filtered:
        print("[WARN] 过滤后无剩余股票")
        # 仍然推送空报告
        send_to_feishu([], check_predictions(data_dict))
        return

    # ================================================
    # Step 3: 策略引擎
    # ================================================
    step("Step 3: 趋势策略引擎分析")

    recommendations = run_trend_analysis(
        filtered,
        names_map=stock_names,
        top_n=params.get("top_n", 5),
        params=params,
    )

    # ================================================
    # Step 4: AI增强 (可选)
    # ================================================
    if AI_ENABLED and recommendations:
        step("Step 4: AI情绪增强 (ModelScope)")
        recommendations = enhance_with_sentiment(recommendations)
        recommendations = filter_by_sentiment(recommendations)
    else:
        print("\n[跳过] AI增强未启用")

    # ================================================
    # Step 5: 保存推荐 + 预测追踪
    # ================================================
    step("Step 5: 保存推荐 & 预测追踪")

    save_recommendations(recommendations)

    # 追踪旧预测
    stats = check_predictions(data_dict)

    # 添加新预测
    if recommendations:
        add_predictions(recommendations, holding_days=params.get("holding_days", 5))
        stats = check_predictions(data_dict)  # 重新计算

    # ================================================
    # Step 6: 飞书推送
    # ================================================
    step("Step 6: 飞书推送")

    send_to_feishu(recommendations, stats)

    # ================================================
    # 打印摘要
    # ================================================
    print(f"\n{'='*60}")
    print(f"  ✅ 运行完成")
    print(f"  总股票: {len(data_dict)} → 过滤后: {len(filtered)} → 推荐: {len(recommendations)}")
    if stats:
        print(f"  历史统计: {stats['resolved']}结算 | {stats['wr']}%胜率 | PF {stats['pf']}")

    if recommendations:
        print(f"\n  📈 今日推荐:")
        for i, r in enumerate(recommendations, 1):
            ai_tag = r.get("ai_sentiment", "")
            print(f"    {i}. {r['name']}({r['code']}) | "
                  f"¥{r['entry_price']} | "
                  f"止盈+{r['target_pct']}% | "
                  f"止损-{r['stop_pct']}% | "
                  f"R/R {r['rr_ratio']} | "
                  f"{r['confidence']} {ai_tag}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
