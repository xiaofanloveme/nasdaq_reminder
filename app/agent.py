from __future__ import annotations

import html
import json
import math
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, TypedDict

import yfinance as yf
from langgraph.graph import END, START, StateGraph

from app.config import Settings, get_settings


class StockAlertState(TypedDict, total=False):
    ticker: str
    run_mode: str
    prev_close: float
    current_price: float
    change_pct: float
    peer_quotes: dict[str, dict[str, float]]
    threshold_met: bool
    should_alert: bool
    alert_bucket: int
    decision: str
    search_queries: list[str]
    raw_search_results: list[dict[str, Any]]
    analysis_brief: str
    email_status: str | None
    timestamp: str
    error: str | None


class AlertStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    run_mode TEXT NOT NULL,
                    current_price REAL NOT NULL,
                    prev_close REAL NOT NULL,
                    change_pct REAL NOT NULL,
                    threshold_met INTEGER NOT NULL,
                    should_alert INTEGER NOT NULL,
                    alert_bucket INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    analysis_brief TEXT NOT NULL,
                    email_status TEXT,
                    payload TEXT NOT NULL
                )
                """
            )

    def bucket_sent_today(self, ticker: str, bucket: int) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM runs
                WHERE ticker = ? AND alert_bucket = ? AND should_alert = 1
                  AND substr(created_at, 1, 10) = ?
                LIMIT 1
                """,
                (ticker, bucket, today),
            ).fetchone()
        return row is not None

    def save(self, state: StockAlertState) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    created_at, ticker, run_mode, current_price, prev_close,
                    change_pct, threshold_met, should_alert, alert_bucket,
                    decision, analysis_brief, email_status, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state["timestamp"],
                    state["ticker"],
                    state["run_mode"],
                    state["current_price"],
                    state["prev_close"],
                    state["change_pct"],
                    int(state["threshold_met"]),
                    int(state["should_alert"]),
                    state["alert_bucket"],
                    state["decision"],
                    state.get("analysis_brief", ""),
                    state.get("email_status"),
                    json.dumps(state, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def _quote(symbol: str) -> dict[str, float]:
    info = yf.Ticker(symbol).fast_info
    current = float(info["last_price"])
    previous = float(info["previous_close"])
    return {
        "current": round(current, 4),
        "previous_close": round(previous, 4),
        "change_pct": round((current - previous) / previous * 100, 2),
    }


def _normalize_news_item(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content", item)
    provider = content.get("provider") or {}
    click = content.get("clickThroughUrl") or content.get("canonicalUrl") or {}
    return {
        "title": content.get("title", "Untitled market update"),
        "url": click.get("url", "") if isinstance(click, dict) else str(click),
        "content": content.get("summary") or content.get("description") or "",
        "source": provider.get("displayName", "Yahoo Finance")
        if isinstance(provider, dict)
        else str(provider),
    }


class CleanSparkAgent:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.store = AlertStore(self.settings.database_path)
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(StockAlertState)
        workflow.add_node("fetch_market_data", self.fetch_market_data)
        workflow.add_node("evaluate_volatility", self.evaluate_volatility)
        workflow.add_node("search_catalysts", self.search_catalysts)
        workflow.add_node("synthesize_brief", self.synthesize_brief)
        workflow.add_node("send_email", self.send_email)
        workflow.add_edge(START, "fetch_market_data")
        workflow.add_edge("fetch_market_data", "evaluate_volatility")
        workflow.add_conditional_edges(
            "evaluate_volatility",
            lambda state: "search" if state["should_alert"] else "finish",
            {"search": "search_catalysts", "finish": END},
        )
        workflow.add_edge("search_catalysts", "synthesize_brief")
        workflow.add_edge("synthesize_brief", "send_email")
        workflow.add_edge("send_email", END)
        return workflow.compile()

    def fetch_market_data(self, state: StockAlertState) -> StockAlertState:
        if state.get("run_mode") == "demo":
            return {
                "prev_close": 13.84,
                "current_price": 15.02,
                "change_pct": 8.53,
                "peer_quotes": {
                    "BTC-USD": {"current": 67348.0, "previous_close": 65592.0, "change_pct": 2.68},
                    "MARA": {"current": 18.91, "previous_close": 18.12, "change_pct": 4.36},
                    "RIOT": {"current": 11.44, "previous_close": 11.10, "change_pct": 3.06},
                },
            }
        symbols = [state["ticker"], "BTC-USD", "MARA", "RIOT"]
        quotes = {symbol: _quote(symbol) for symbol in symbols}
        target = quotes.pop(state["ticker"])
        return {
            "prev_close": target["previous_close"],
            "current_price": target["current"],
            "change_pct": target["change_pct"],
            "peer_quotes": quotes,
        }

    def evaluate_volatility(self, state: StockAlertState) -> StockAlertState:
        threshold = self.settings.alert_threshold
        threshold_met = abs(state["change_pct"]) >= threshold
        bucket = (
            math.floor(abs(state["change_pct"]) / threshold) * int(threshold)
            if threshold_met
            else 0
        )
        duplicate = (
            state.get("run_mode") != "demo"
            and threshold_met
            and self.store.bucket_sent_today(state["ticker"], bucket)
        )
        if not threshold_met:
            decision = f"波动未达到 ±{threshold:.1f}% 阈值，静默结束"
        elif duplicate:
            decision = f"今日 {bucket}% 档位已发送，防抖去重"
        else:
            decision = f"触发 {bucket}% 异动档位，启动归因分析"
        return {
            "threshold_met": threshold_met,
            "should_alert": threshold_met and not duplicate,
            "alert_bucket": bucket,
            "decision": decision,
        }

    def search_catalysts(self, state: StockAlertState) -> StockAlertState:
        queries = [
            f"{state['ticker']} CleanSpark latest news operations SEC filing",
            "Bitcoin price mining difficulty hash rate latest",
            "MARA RIOT bitcoin miners stock news today",
        ]
        results: list[dict[str, Any]] = []
        if self.settings.tavily_api_key:
            from tavily import TavilyClient

            client = TavilyClient(api_key=self.settings.tavily_api_key)
            for query in queries:
                response = client.search(
                    query=query,
                    topic="news",
                    days=2,
                    max_results=3,
                    search_depth="advanced",
                )
                results.extend(response.get("results", []))
        elif state.get("run_mode") == "demo":
            results = [
                {
                    "title": "CleanSpark reports continued fleet expansion",
                    "content": "The company highlighted operating capacity and efficiency progress in its latest update.",
                    "url": "https://www.cleanspark.com/news/",
                    "source": "CleanSpark investor relations",
                },
                {
                    "title": "Bitcoin strength lifts listed miners",
                    "content": "Bitcoin gained 2.7% while MARA and RIOT rose 4.4% and 3.1%, respectively.",
                    "url": "https://finance.yahoo.com/quote/BTC-USD/",
                    "source": "Demo market context",
                },
            ]
        else:
            try:
                results = [
                    _normalize_news_item(item)
                    for item in yf.Ticker(state["ticker"]).news[:8]
                ]
            except Exception:
                results = []
        return {"search_queries": queries, "raw_search_results": results}

    def synthesize_brief(self, state: StockAlertState) -> StockAlertState:
        context = "\n".join(
            f"- {item.get('title')}: {item.get('content', '')}"
            for item in state.get("raw_search_results", [])
        )
        peers = state["peer_quotes"]
        prompt = f"""
你是一名资深美股量化与加密矿业分析师。仅依据给定数据，用中文输出三段式简报，
不超过 300 个汉字；不确定的信息必须明确标注，禁止编造因果关系。

CleanSpark (NASDAQ: CLSK) 当前价 ${state['current_price']:.2f}，
当日波动 {state['change_pct']:+.2f}%。
BTC {peers['BTC-USD']['change_pct']:+.2f}%，MARA {peers['MARA']['change_pct']:+.2f}%，
RIOT {peers['RIOT']['change_pct']:+.2f}%。
资讯：
{context or '未获取到可靠实时资讯。'}

严格使用：
【核心催化剂】
【板块与同业联动】
【短期关注要点】
"""
        if self.settings.openai_api_key:
            from langchain_openai import ChatOpenAI

            response = ChatOpenAI(
                model=self.settings.openai_model,
                api_key=self.settings.openai_api_key,
                temperature=0.1,
            ).invoke(prompt)
            brief = str(response.content)
        else:
            direction = "上涨" if state["change_pct"] > 0 else "下跌"
            peer_avg = (peers["MARA"]["change_pct"] + peers["RIOT"]["change_pct"]) / 2
            relative = state["change_pct"] - peer_avg
            news_note = (
                f"检索到 {len(state.get('raw_search_results', []))} 条相关资讯，"
                "当前为规则归因，具体事件仍需核验。"
                if state.get("raw_search_results")
                else "未获取到可靠实时资讯，暂不将波动归因于具体公司事件。"
            )
            brief = (
                f"【核心催化剂】CLSK 日内{direction} {abs(state['change_pct']):.2f}%。"
                f"BTC 同期 {peers['BTC-USD']['change_pct']:+.2f}%，显示加密资产贝塔是"
                f"{'重要推力' if state['change_pct'] * peers['BTC-USD']['change_pct'] > 0 else '有限解释'}；"
                f"{news_note}\n\n"
                f"【板块与同业联动】MARA {peers['MARA']['change_pct']:+.2f}%，"
                f"RIOT {peers['RIOT']['change_pct']:+.2f}%。CLSK 较两者均值"
                f"{relative:+.2f} 个百分点，{'存在个股超额表现' if relative > 2 else '整体接近板块联动'}。\n\n"
                "【短期关注要点】未来 1–3 个交易日关注 BTC 延续性、矿业难度变化、"
                "公司运营公告及潜在 ATM 融资；上述内容仅为数据归因，不构成投资建议。"
            )
        return {"analysis_brief": brief}

    def send_email(self, state: StockAlertState) -> StockAlertState:
        if state.get("run_mode") == "demo":
            return {"email_status": "preview_only"}
        if not self.settings.email_ready:
            return {"email_status": "preview_only"}
        message = EmailMessage()
        message["Subject"] = (
            f"【异动预警】CleanSpark (CLSK) 今日波动 {state['change_pct']:+.2f}% 归因简报"
        )
        message["From"] = self.settings.smtp_user
        message["To"] = self.settings.alert_recipient
        message.set_content(state["analysis_brief"])
        message.add_alternative(
            f"""
            <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#17231d">
              <p style="color:#5b6b62">CleanSpark · CLSK 异动预警</p>
              <h1 style="font-size:28px">{state['change_pct']:+.2f}% · ${state['current_price']:.2f}</h1>
              <div style="white-space:pre-wrap;line-height:1.7">{html.escape(state['analysis_brief'])}</div>
              <hr><small>自动生成，仅供参考，不构成投资建议。</small>
            </div>
            """,
            subtype="html",
        )
        try:
            if self.settings.smtp_port == 465:
                with smtplib.SMTP_SSL(
                    self.settings.smtp_host, self.settings.smtp_port, timeout=20
                ) as server:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(
                    self.settings.smtp_host, self.settings.smtp_port, timeout=20
                ) as server:
                    server.starttls()
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                    server.send_message(message)
            return {"email_status": "sent"}
        except Exception as exc:
            return {"email_status": f"failed: {type(exc).__name__}"}

    def run(self, mode: str = "live") -> dict[str, Any]:
        initial: StockAlertState = {
            "ticker": self.settings.target_ticker,
            "run_mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "analysis_brief": "",
            "email_status": None,
            "search_queries": [],
            "raw_search_results": [],
        }
        try:
            result = dict(self.graph.invoke(initial))
        except Exception as exc:
            result = {
                **initial,
                "prev_close": 0.0,
                "current_price": 0.0,
                "change_pct": 0.0,
                "peer_quotes": {},
                "threshold_met": False,
                "should_alert": False,
                "alert_bucket": 0,
                "decision": "行情抓取失败",
                "error": f"{type(exc).__name__}: {exc}",
            }
        result["id"] = self.store.save(result)
        return result
