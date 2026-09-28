# CleanSpark Signal Agent

CleanSpark (NASDAQ: CLSK) 股价异动监控与 Agentic 智能归因预警系统。它按固定间隔采集 CLSK、BTC、MARA 和 RIOT 行情，在日内波动达到阈值时，通过 LangGraph 执行资讯检索、三段式归因和邮件分发。

## 已实现

- yfinance 实时行情采集与前收涨跌幅计算
- 默认 ±5% 触发阈值、5% 档位升级与同日防抖
- Tavily 新闻检索（可选）和 Yahoo Finance 新闻回退
- OpenAI 三段式中文归因（可选）和无需密钥的规则归因
- SMTP SSL / STARTTLS HTML 邮件
- APScheduler 定时监控、SQLite 运行记录
- FastAPI 控制台、实时运行和完整演示模式

## 快速开始

需要 Python 3.12+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m app.main --serve --port 43123
```

打开 <http://localhost:43123>。没有任何 API 密钥时，实时行情、阈值判断、规则归因和历史记录仍然可用；“体验异动演示”会用固定异常行情走完完整流程，且永远不会发送邮件。

也可直接从命令行执行一次：

```bash
python -m app.main          # 实时行情
python -m app.main --demo   # 确定性演示
```

## 启用完整能力

复制 `.env.example` 为 `.env`，按需填写：

- `TAVILY_API_KEY`：过去 24–48 小时的实时催化剂检索
- `OPENAI_API_KEY`：LLM 事实提炼和归因
- `SMTP_*` 与 `ALERT_RECIPIENT`：邮件分发
- `ALERT_THRESHOLD`、`POLL_INTERVAL_MINUTES`：阈值和扫描频率

当 SMTP 端口为 `465` 时使用 SSL；其他端口（通常为 `587`）使用 STARTTLS。密钥不会通过 Web API 暴露。

## Agent 工作流

```text
fetch_market_data → evaluate_volatility
                              ├─ 未触发/重复 → END
                              └─ search_catalysts
                                 → synthesize_brief
                                 → send_email → END
```

运行记录保存在 `data/alerts.db`。生产部署时应由单个调度实例运行，避免多个进程重复扫描；更大规模部署可将防抖存储替换为 Redis。

## 测试

```bash
pytest
```

> 本项目输出仅供信息参考，不构成投资建议。免费行情源可能存在延迟；涉及交易决策时请接入有服务等级保障的证券行情 API。
