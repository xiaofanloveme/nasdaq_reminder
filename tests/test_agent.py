from pathlib import Path

from app.agent import CleanSparkAgent
from app.config import Settings


def make_agent(tmp_path: Path, threshold: float = 5.0) -> CleanSparkAgent:
    return CleanSparkAgent(
        Settings(
            alert_threshold=threshold,
            database_path=tmp_path / "alerts.db",
            enable_scheduler=False,
        )
    )


def test_demo_runs_complete_alert_graph(tmp_path: Path) -> None:
    result = make_agent(tmp_path).run("demo")

    assert result["ticker"] == "CLSK"
    assert result["change_pct"] == 8.53
    assert result["threshold_met"] is True
    assert result["should_alert"] is True
    assert result["alert_bucket"] == 5
    assert "【核心催化剂】" in result["analysis_brief"]
    assert result["email_status"] == "preview_only"


def test_threshold_can_silence_demo_signal(tmp_path: Path) -> None:
    result = make_agent(tmp_path, threshold=10.0).run("demo")

    assert result["threshold_met"] is False
    assert result["should_alert"] is False
    assert result["analysis_brief"] == ""


def test_runs_are_persisted(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.run("demo")
    history = agent.store.recent()

    assert len(history) == 1
    assert history[0]["id"] == result["id"]
    assert history[0]["decision"].startswith("触发")
