"""
AI 客户端 —— DeepSeek API (OpenAI兼容)
"""

import os
import json
from datetime import datetime

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_cache: dict = {}
_cache_ttl: dict = {}


def _cached(key: str, ttl_minutes: int = 120) -> bool:
    if key not in _cache:
        return False
    return (datetime.now() - _cache_ttl.get(key, datetime.min)).seconds < ttl_minutes * 60


def _set_cache(key: str, value):
    _cache[key] = value
    _cache_ttl[key] = datetime.now()


def is_available() -> bool:
    return bool(DEEPSEEK_API_KEY)


def chat(prompt: str, system: str = "你是A股量化分析师。") -> str | None:
    """调用DeepSeek Chat"""
    if not is_available():
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[WARN] DeepSeek API 失败: {e}")
        return None


def analyze_sentiment(code: str, name: str, price_info: str = "") -> dict:
    """
    用DeepSeek分析股票情绪

    返回 {"label": "positive/negative/neutral", "score": float, "reason": str}
    """
    if not is_available():
        return {"label": "neutral", "score": 0.5, "source": "disabled"}

    cache_key = f"ds_{code}"
    if _cached(cache_key, ttl_minutes=120):
        return _cache[cache_key]

    prompt = f"""分析这只A股：{name}({code})
{price_info}

只回复一个JSON，格式：
{{"sentiment":"看多/看空/中性","confidence":0.0-1.0,"reason":"一句话理由(10字内)"}}"""

    result = chat(prompt)
    if not result:
        return {"label": "neutral", "score": 0.5, "source": "error"}

    # 解析JSON
    try:
        # 提取JSON部分
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(result[start:end])
            sentiment = data.get("sentiment", "中性")
            confidence = float(data.get("confidence", 0.5))
            reason = data.get("reason", "")

            label = "positive" if "看多" in sentiment else ("negative" if "看空" in sentiment else "neutral")
            output = {"label": label, "score": confidence, "reason": reason, "source": "DeepSeek"}
            _set_cache(cache_key, output)
            return output
    except Exception:
        pass

    return {"label": "neutral", "score": 0.5, "source": "parse_error"}
