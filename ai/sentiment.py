"""
AI情绪增强层 —— DeepSeek 驱动
"""

from ai.deepseek_client import analyze_sentiment, is_available


def enhance_with_sentiment(recommendations: list[dict]) -> list[dict]:
    """
    对Top推荐用DeepSeek做情绪增强

    调整：
      - 看多 → 评分 +5%
      - 看空 → 评分 -10%，可能踢出
      - 中性 → 不变
    """
    if not is_available():
        print("[INFO] AI情绪未启用 (DEEPSEEK_API_KEY 未设置)")
        return recommendations

    print(f"  AI情绪分析 (DeepSeek): {len(recommendations)} 支...")

    for i, rec in enumerate(recommendations):
        code = rec.get("code", "")
        name = rec.get("name", "")
        price_info = f"现价¥{rec.get('entry_price', '?')} 止损-{rec.get('stop_pct', '?')}%"

        print(f"    {i+1}. {name} ({code})...", end=" ", flush=True)
        result = analyze_sentiment(code, name, price_info)

        label = result.get("label", "neutral")
        reason = result.get("reason", "")

        if label == "positive":
            rec["score"] = round(rec["score"] * 1.05, 1)
            rec["ai_sentiment"] = f"🟢 {reason}"
            print(f"🟢 {reason}")
        elif label == "negative":
            rec["score"] = round(rec["score"] * 0.90, 1)
            rec["ai_sentiment"] = f"🔴 {reason}"
            print(f"🔴 {reason}")
        else:
            rec["ai_sentiment"] = f"⚪ {reason}" if reason else "⚪ 中性"
            print(f"⚪ {reason}")

    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations


def filter_by_sentiment(recommendations: list[dict], min_score: float = 40) -> list[dict]:
    """过滤低分推荐（AI下调后可能不达标）"""
    return [r for r in recommendations if r["score"] >= min_score]
