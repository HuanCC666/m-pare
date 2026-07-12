"""Tests for pare.scenarios.generator.agent.scenario_generating_agent_orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pare.scenarios.generator.scenario_generator import prepare_prompt_context_data
from pare.scenarios.generator.utils.apps_init_instructions import ScenarioWithAllPAREApps
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_debug_orchestrator_threads_resolved_manifest_into_later_steps(tmp_path: Path) -> None:
    """A provided asset manifest should become prompt context for Step 2/3."""
    from pare.scenarios.generator.agent.scenario_generating_agent_orchestrator import (
        ScenarioGeneratingAgentOrchestrator,
    )

    source = tmp_path / "source.jpg"
    source.write_bytes(jpeg_bytes_for_sandbox(b"rice cooker fixture"))
    manifest_path = tmp_path / "assets.json"
    manifest_path.write_text(
        json.dumps({
            "assets": [
                {
                    "asset_id": "rice_cooker_photo",
                    "filename": "rice_cooker.jpg",
                    "source_path": str(source),
                    "sandbox_path": "/photo.jpg",
                    "delivery": "email_attachment",
                    "visual_requirements": ["compact white rice cooker"],
                    "ground_truth": {"object": "rice cooker", "price": "$49.90", "stock": "many"},
                }
            ]
        }),
        encoding="utf-8",
    )
    scenario = ScenarioWithAllPAREApps()
    scenario.initialize()
    prompt_context = prepare_prompt_context_data(
        scenario,
        ["SandboxLocalFileSystem", "StatefulEmailApp", "StatefulShoppingApp"],
    )

    orchestrator = ScenarioGeneratingAgentOrchestrator(
        trajectory_dir=tmp_path / "trajectory",
        debug_prompts=True,
        max_iterations=1,
        prompt_context=prompt_context,
        asset_manifest_path=manifest_path,
        asset_dir=tmp_path / "resolved",
    )
    result = orchestrator.run()

    resolved_manifest = tmp_path / "trajectory" / "resolved_assets.json"
    assert resolved_manifest.exists()
    assert (tmp_path / "resolved" / "rice_cooker.jpg").exists()

    step2 = result["steps"][2]
    step3 = result["steps"][3]
    step2_user_prompt = step2.conversation[1]["content"]
    step3_user_prompt = step3.conversation[1]["content"]

    assert "Resolved visual assets" in step2_user_prompt
    assert "rice_cooker_photo" in step2_user_prompt
    assert str(tmp_path / "resolved" / "rice_cooker.jpg") in step2_user_prompt
    assert "Resolved visual assets" in step3_user_prompt
    assert "/photo.jpg" in step3_user_prompt


def test_debug_orchestrator_threads_generated_assets_into_later_steps(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Generated assets should flow through the same resolved-asset context."""
    from pare.scenarios.generator.agent.scenario_generating_agent_orchestrator import (
        ScenarioGeneratingAgentOrchestrator,
    )
    from pare.scenarios.generator.agent.step_agents import StepResult

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario = ScenarioWithAllPAREApps()
    scenario.initialize()
    prompt_context = prepare_prompt_context_data(
        scenario,
        ["SandboxLocalFileSystem", "StatefulEmailApp", "StatefulShoppingApp"],
    )

    def fake_image_client(*, prompt: str, model: str) -> bytes:
        return f"{model}:{prompt}".encode()

    orchestrator = ScenarioGeneratingAgentOrchestrator(
        trajectory_dir=tmp_path / "trajectory",
        debug_prompts=True,
        max_iterations=1,
        prompt_context=prompt_context,
        asset_provider="openai-image",
        image_model="test-image-model",
        image_client=fake_image_client,
    )

    asset_plan_content = json.dumps({
        "assets": [
            {
                "asset_id": "rice_cooker_photo",
                "filename": "rice_cooker.jpg",
                "sandbox_path": "/photo.jpg",
                "delivery": "email_attachment",
                "kind": "photo_like",
                "generation_prompt": "A compact white rice cooker.",
                "ground_truth": {"object": "rice cooker"},
            }
        ]
    })
    orchestrator.asset_planning_agent.run = lambda **_: StepResult(
        name="Step 1.5: Visual Asset Plan",
        content=asset_plan_content,
        iterations=1,
        notes={},
        conversation=[{"role": "assistant", "content": asset_plan_content}],
    )

    result = orchestrator.run()

    resolved_manifest = tmp_path / "trajectory" / "resolved_assets.json"
    resolved_data = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    assert resolved_data["assets"][0]["provider_metadata"]["provider"] == "openai-image"
    assert resolved_data["assets"][0]["provider_metadata"]["model"] == "test-image-model"

    step2_user_prompt = result["steps"][2].conversation[1]["content"]
    step3_user_prompt = result["steps"][3].conversation[1]["content"]
    assert "Resolved visual assets" in step2_user_prompt
    assert "openai-image" in step2_user_prompt
    assert "test-image-model" in step3_user_prompt


def test_orchestrator_uses_multimodal_metadata_by_default(tmp_path: Path) -> None:
    """The multimodal override should use multimodal metadata for Step 1 uniqueness."""
    from pare.scenarios.generator.agent.scenario_generating_agent_orchestrator import (
        ScenarioGeneratingAgentOrchestrator,
    )

    orchestrator = ScenarioGeneratingAgentOrchestrator(
        trajectory_dir=tmp_path / "trajectory",
        debug_prompts=True,
        max_iterations=1,
    )

    assert orchestrator.scenario_metadata_path.name == "multimodal_scenario_metadata.json"
