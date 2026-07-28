# 📊 A-Share Quant Platform

> A股量化分析平台 —— 每日自动分析沪深300，推送飞书，部署在ModelScope

[![Daily Update](https://github.com/yourname/ashare-quant/actions/workflows/daily_update.yml/badge.svg)](https://github.com/yourname/ashare-quant/actions)
[![ModelScope](https://img.shields.io/badge/ModelScope-托管-blue)](https://modelscope.cn)

---

## 架构

```
沪深300成分股 → AKShare数据 → A股过滤 → 趋势策略引擎 → AI情绪增强 → 飞书推送
                                    ↓
                              ModelScope 托管 + GitHub Actions 调度
```

## 功能

- 🔄 **每日自动运行**：交易日 15:30 收盘后自动分析
- 📊 **趋势策略**：Supertrend + MA + ATR + RSI 多指标共振
- 🧠 **AI增强**：ModelScope 中文情感分析模型辅助判断
- 📱 **飞书推送**：每日推荐 + 历史胜率统计
- 📈 **预测追踪**：止盈/止损/到期自动结算，计算 Profit Factor
- 📉 **可视化面板**：Streamlit 展示推荐历史、胜率曲线

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 飞书群机器人 Webhook（必填）
export FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# ModelScope Token（可选，用于AI增强）
export MODELSCOPE_TOKEN="ms-xxxxxxxxxxxxxxxx"
```

### 3. 本地运行

```bash
python runner.py
```

### 4. 启动可视化面板

```bash
streamlit run app.py
```

## 文件结构

```
ashare-quant/
├── .github/workflows/
│   └── daily_update.yml       # GitHub Actions 定时调度
├── config/
│   ├── settings.py            # 全局配置
│   └── params.json            # 策略参数
├── data/
│   └── akshare_fetcher.py     # AKShare 数据获取
├── strategies/
│   ├── indicators.py          # 技术指标库
│   ├── filters.py             # A股过滤 (ST/涨跌停/流动性)
│   ├── trend_engine.py        # 趋势策略引擎
│   └── scoring.py             # 综合评分
├── ai/
│   ├── modelscope_client.py   # ModelScope API
│   └── sentiment.py           # AI情绪增强
├── notification/
│   └── feishu.py              # 飞书推送
├── tracker/
│   └── predictor.py           # 预测追踪 + 统计
├── reports/                   # 输出结果
├── app.py                     # Streamlit 面板
└── runner.py                  # 主入口
```

## GitHub Actions 配置

在仓库 Settings → Secrets 中添加：

| Secret | 说明 |
|--------|------|
| `FEISHU_WEBHOOK` | 飞书群机器人 Webhook 地址 |
| `MODELSCOPE_TOKEN` | ModelScope SDK Token（可选） |

## ModelScope 部署

1. 在 [modelscope.cn](https://modelscope.cn) 创建模型仓库
2. 推送代码到 ModelScope
3. 在 ModelScope Spaces 部署 Streamlit 面板

## 免责声明

⚠️ 本项目仅供学习研究，**不构成任何投资建议**。量化交易存在固有风险，过往业绩不代表未来表现。

## License

MIT
