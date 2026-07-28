"""
AI情绪增强层 —— 对候选股进行情绪评分调整
"""

from ai.modelscope_client import analyze_stock_sentiment, is_available


def enhance_with_sentiment(recommendations: list[dict]) -> list[dict]:
    """
    对推荐列表中的股票进行AI情绪增强

    调整逻辑:
      - positive: 评分 +5%
      - negative: 评分 -10%（大幅惩罚）
      - neutral: 不变
    """
    if not is_available():
        print("[INFO] AI情绪分析未启用（MODELSCOPE_TOKEN 未设置）")
        return recommendations

    print(f"  AI情绪分析: {len(recommendations)} 支候选股...")

    for i, rec in enumerate(recommendations):
        code = rec.get("code", "")
        name = rec.get("name", "")
        print(f"    {i+1}. {name} ({code})...", end=" ")

        sentiment = analyze_stock_sentiment(code, name)
        label = sentiment.get("sentiment", "neutral")

        if label == "positive":
            rec["score"] = round(rec["score"] * 1.05, 1)
            rec["ai_sentiment"] = "🟢 正面"
            print("🟢")
        elif label == "negative":
            rec["score"] = round(rec["score"] * 0.90, 1)
            rec["ai_sentiment"] = "🔴 负面"
            print("🔴")
        else:
            rec["ai_sentiment"] = "⚪ 中性"
            print("⚪")

    # 按调整后的评分重新排序
    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations


def filter_by_sentiment(recommendations: list[dict], min_score: float = 40) -> list[dict]:
    """
    按调整后评分过滤，低于阈值的不推荐
    """
    return [r for r in recommendations if r["score"] >= min_score]
