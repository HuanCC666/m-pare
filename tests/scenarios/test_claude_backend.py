"""Tests for Claude backend usage/cost capture helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pare.scenarios.generator.agent.claude_backend import (
    ClaudeCallResult,
    summarize_usage_records,
)
from pare.scenarios.generator.scenario_generator import prepare_prompt_context_data
from pare.scenarios.generator.utils.apps_init_instructions import ScenarioWithAllPAREApps


def test_summarize_usage_records_aggregates_cost_and_tokens() -> None:
    """Run-level summaries should sum SDK cost estimates and token counters."""
    records = [
        {
            "step_tag": "Step 1",
            "iteration": 1,
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
        {
            "step_tag": "Step 2",
            "iteration": 1,
            "total_cost_usd": 0.02,
            "usage": {"input_tokens": 50, "output_tokens": 30, "cache_read_input_tokens": 10},
        },
    ]

    summary = summarize_usage_records(records)

    assert summary["calls"] == 2
    assert summary["total_cost_usd"] == 0.03
    assert summary["usage_totals"]["input_tokens"] == 150
    assert summary["usage_totals"]["output_tokens"] == 50
    assert summary["usage_totals"]["cache_read_input_tokens"] == 10
    assert len(summary["calls_detail"]) == 2


def test_summarize_usage_records_handles_missing_cost() -> None:
    """Missing cost fields should leave total_cost_usd as None."""
    summary = summarize_usage_records([{"step_tag": "x", "usage": {"input_tokens": 3}}])
    assert summary["calls"] == 1
    assert summary["total_cost_usd"] is None
    assert summary["usage_totals"]["input_tokens"] == 3


def test_debug_orchestrator_writes_cost_summary(tmp_path: Path) -> None:
    """Even debug runs should write a cost.json summary (empty when no LLM calls)."""
    from pare.scenarios.generator.agent.scenario_generating_agent_orchestrator import (
        ScenarioGeneratingAgentOrchestrator,
    )

    scenario = ScenarioWithAllPAREApps()
    scenario.initialize()
    prompt_context = prepare_prompt_context_data(
        scenario,
        ["SandboxLocalFileSystem", "StatefulAlbumApp", "StatefulMessagingApp"],
    )
    orchestrator = ScenarioGeneratingAgentOrchestrator(
        trajectory_dir=tmp_path / "trajectory",
        debug_prompts=True,
        max_iterations=1,
        prompt_context=prompt_context,
    )
    result = orchestrator.run()

    cost_path = tmp_path / "trajectory" / "cost.json"
    assert cost_path.exists()
    summary = json.loads(cost_path.read_text(encoding="utf-8"))
    assert summary["calls"] == 0
    assert summary["total_cost_usd"] is None
    assert result["cost_summary"]["calls"] == 0


def test_claude_call_result_defaults() -> None:
    """ClaudeCallResult should default to empty usage metadata."""
    result = ClaudeCallResult(text="ok")
    assert result.text == "ok"
    assert result.usage == {}


def test_usage_sink_receives_records_from_runtime_config() -> None:
    """Runtime config usage_sink should accept appended call records."""
    from pare.scenarios.generator.agent.claude_backend import ClaudeAgentRuntimeConfig

    sink: list[dict[str, Any]] = []
    config = ClaudeAgentRuntimeConfig(
        cwd=Path("."),
        allowed_tools=["Read"],
        usage_sink=sink,
    )
    assert config.usage_sink is sink
    config.usage_sink.append({"total_cost_usd": 0.5, "usage": {"input_tokens": 1}})
    assert summarize_usage_records(sink)["total_cost_usd"] == 0.5
