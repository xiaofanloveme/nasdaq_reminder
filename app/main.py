from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent import CleanSparkAgent
from app.config import get_settings

settings = get_settings()
agent = CleanSparkAgent(settings)
scheduler = AsyncIOScheduler(timezone="UTC")


async def scheduled_run() -> None:
    await asyncio.to_thread(agent.run, "live")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.enable_scheduler:
        scheduler.add_job(
            scheduled_run,
            "interval",
            minutes=settings.poll_interval_minutes,
            id="cleanspark-monitor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="CleanSpark Signal Agent",
    description="CLSK 异动监控与 Agentic 智能归因预警系统",
    version="1.0.0",
    lifespan=lifespan,
)


class RunRequest(BaseModel):
    mode: Literal["live", "demo"] = "live"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ticker": settings.target_ticker}


@app.get("/api/status")
def status() -> dict:
    return {
        "ticker": settings.target_ticker,
        "threshold": settings.alert_threshold,
        "poll_interval_minutes": settings.poll_interval_minutes,
        "scheduler_enabled": settings.enable_scheduler,
        "llm_configured": bool(settings.openai_api_key),
        "search_configured": bool(settings.tavily_api_key),
        "email_configured": settings.email_ready,
    }


@app.get("/api/runs")
def runs(limit: int = 20) -> list[dict]:
    return agent.store.recent(min(max(limit, 1), 100))


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    records = [row for row in agent.store.recent(100) if row["id"] == run_id]
    if not records:
        raise HTTPException(status_code=404, detail="Run not found")
    import json

    return json.loads(records[0]["payload"])


@app.post("/api/run")
async def run_agent(request: RunRequest) -> dict:
    return await asyncio.to_thread(agent.run, request.mode)


static_path = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=static_path), name="assets")


@app.get("/")
def index():
    return FileResponse(static_path / "index.html")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run the CleanSpark alert agent")
    parser.add_argument("--demo", action="store_true", help="Run with deterministic demo data")
    parser.add_argument("--serve", action="store_true", help="Start the monitoring dashboard")
    parser.add_argument("--port", type=int, default=43123)
    args = parser.parse_args()
    if args.serve:
        uvicorn.run("app.main:app", host="0.0.0.0", port=args.port)
    else:
        import json

        print(json.dumps(agent.run("demo" if args.demo else "live"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
