"""
ModelScope API 客户端
"""

import os
import time
from datetime import datetime

from config.settings import MODELSCOPE_SENTIMENT_MODEL

# ModelScope SDK Token
TOKEN = os.getenv("MODELSCOPE_TOKEN", "")

# 缓存：避免重复调用
_cache: dict = {}
_cache_ttl: dict = {}


def _is_cached(key: str, ttl_minutes: int = 60) -> bool:
    """检查缓存是否有效"""
    if key not in _cache:
        return False
    elapsed = (datetime.now() - _cache_ttl.get(key, datetime.min)).seconds
    return elapsed < ttl_minutes * 60


def _set_cache(key: str, value):
    """设置缓存"""
    _cache[key] = value
    _cache_ttl[key] = datetime.now()


def is_available() -> bool:
    """检查 ModelScope API 是否可用"""
    return bool(TOKEN)


def analyze_sentiment(text: str) -> dict | None:
    """
    使用 ModelScope 情感分析模型分析文本情绪

    返回 {"label": "positive/negative/neutral", "score": float} 或 None
    """
    if not is_available():
        return None

    if not text or not text.strip():
        return None

    # 检查缓存
    cache_key = f"sent_{hash(text)}"
    if _is_cached(cache_key, ttl_minutes=120):
        return _cache[cache_key]

    try:
        from modelscope.pipelines import pipeline
        from modelscope.utils.constant import Tasks

        # 懒加载 pipeline
        sentiment_pipe = pipeline(
            Tasks.sentiment_classification,
            model=MODELSCOPE_SENTIMENT_MODEL,
        )

        result = sentiment_pipe(input=text[:512])  # 截断过长的文本

        if result and "scores" in result and "labels" in result:
            # 取最高分
            idx = result["scores"].index(max(result["scores"]))
            output = {
                "label": result["labels"][idx],
                "score": round(result["scores"][idx], 4),
            }
            _set_cache(cache_key, output)
            return output

    except ImportError:
        print("[WARN] modelscope 未安装，AI功能禁用")
        return None
    except Exception as e:
        print(f"[WARN] ModelScope 情感分析失败: {e}")
        return None

    return None


def analyze_stock_sentiment(code: str, name: str) -> dict:
    """
    分析股票的综合情绪
    目前基于股票名称做基础判断，后续可接入新闻API
    """
    result = {
        "code": code,
        "name": name,
        "sentiment": "neutral",
        "score": 0.5,
        "source": "ModelScope",
    }

    if not is_available():
        result["source"] = "disabled"
        return result

    # 尝试用名称做情绪分析（简单示例）
    text = f"{name}股票近期表现"
    sentiment = analyze_sentiment(text)

    if sentiment:
        result["sentiment"] = sentiment["label"]
        result["score"] = sentiment["score"]

    return result
