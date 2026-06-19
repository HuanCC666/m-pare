"""Tests for the multimodal scenario generator MVP override."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from pare.scenarios.generator.scenario_generator import (
    build_import_instructions_block,
    prepare_prompt_context_data,
)
from pare.scenarios.generator.utils.apps_init_instructions import ScenarioWithAllPAREApps
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox


def test_generator_context_includes_multimodal_apps_and_imports() -> None:
    """The overridden generator context should expose Album and Files image tools."""
    scenario = ScenarioWithAllPAREApps()
    scenario.initialize()
    app_names = {app.__class__.__name__ for app in scenario.apps}

    assert "StatefulAlbumApp" in app_names
    assert "SandboxLocalFileSystem" in app_names

    selected = ["StatefulEmailApp", "StatefulAlbumApp", "SandboxLocalFileSystem"]
    context = prepare_prompt_context_data(scenario, selected)
    imports = build_import_instructions_block(selected)

    assert "StatefulAlbumApp" in context["selected_apps"]
    assert "SandboxLocalFileSystem" in context["selected_apps"]
    assert "view_photo" in context["allowed_all_tools_block"]
    assert "display" in context["allowed_all_tools_block"]
    assert "from pare.apps import StatefulAlbumApp" in imports
    assert "from are.simulation.apps import SandboxLocalFileSystem" in imports


def test_generator_prompts_are_multimodal_by_default() -> None:
    """The prompt stack should refer to multimodal benchmark examples and asset specs."""
    from pare.scenarios.generator.prompt import scenario_generating_agent_prompts as prompts

    assert "pare/scenarios/multimodal_benchmark" in prompts.GLOBAL_CONTEXT_PROMPT
    assert "VisualAssetSpec" in prompts.SCENARIO_DESCRIPTION_SYSTEM_PROMPT
    assert "rice_cooker_photo_cart_suggestion.py" in prompts.SCENARIO_DESCRIPTION_USER_PROMPT
    assert "calendar_conflict_urgent_reschedule.py" not in prompts.SCENARIO_DESCRIPTION_SYSTEM_PROMPT
    assert "image inspection" in prompts.VALIDATION_SYSTEM_PROMPT.lower()
    assert hasattr(prompts, "ASSET_PLANNING_SYSTEM_PROMPT")
    assert "assets.json" in prompts.ASSET_PLANNING_SYSTEM_PROMPT


def test_local_asset_provider_resolves_manifest_and_visual_qa(tmp_path: Path) -> None:
    """Local assets should resolve from manifest into stable scenario asset paths."""
    from pare.scenarios.generator.assets import LocalAssetProvider, VisualQA

    source = tmp_path / "source.jpg"
    source.write_bytes(jpeg_bytes_for_sandbox(b"not-really-a-jpeg"))
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
                    "ground_truth": {"object": "rice cooker"},
                }
            ]
        }),
        encoding="utf-8",
    )

    provider = LocalAssetProvider(manifest_path=manifest_path, output_dir=tmp_path / "out")
    resolved = provider.resolve_assets()

    assert len(resolved) == 1
    assert resolved[0].asset_id == "rice_cooker_photo"
    assert resolved[0].resolved_path.exists()
    assert resolved[0].sandbox_path == "/photo.jpg"

    result = VisualQA().check(resolved)
    assert result.passed is True
    assert result.errors == []


def test_multimodal_helpers_normalize_jpeg_and_detect_image_view(tmp_path: Path) -> None:
    """MVP helpers should support sandbox JPEG normalization and image-view validation."""
    raw = b"plain bytes"
    normalized = jpeg_bytes_for_sandbox(raw)
    assert normalized == raw

    from are.simulation.types import Action, CompletedEvent, EventType

    class SandboxLocalFileSystem:
        name = "Files"

        def display(self, path: str) -> str:
            return path

    app = SandboxLocalFileSystem()
    action = Action(function=app.display, args={"path": "/photo.jpg"}, app=app)
    event = CompletedEvent(event_type=EventType.AGENT, action=action)

    assert log_has_agent_image_view([event], allow_any_event_type=False, image_path="/photo.jpg")
    assert not log_has_agent_image_view([event], allow_any_event_type=False, image_path="/other.jpg")


def test_debug_orchestrator_threads_resolved_manifest_into_later_steps(tmp_path: Path) -> None:
    """A provided asset manifest should become prompt context for Step 2/3."""
    from pare.scenarios.generator.agent.scenario_generating_agent_orchestrator import (
        ScenarioGeneratingAgentOrchestrator,
    )
    from pare.scenarios.generator.scenario_generator import prepare_prompt_context_data
    from pare.scenarios.generator.utils.apps_init_instructions import ScenarioWithAllPAREApps

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


def test_multimodal_metadata_exists_for_benchmark_scenarios() -> None:
    """Multimodal generation should compare against multimodal benchmark scenarios."""
    metadata_path = Path("pare/scenarios/multimodal_scenario_metadata.json")
    benchmark_dir = Path("pare/scenarios/multimodal_benchmark")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    ids = {entry["scenario_id"] for entry in metadata}
    benchmark_ids: set[str] = set()
    for path in benchmark_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "register_scenario":
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    benchmark_ids.add(node.args[0].value)
                break

    assert benchmark_ids
    assert benchmark_ids <= ids
    assert "rice_cooker_photo_cart_suggestion" in ids
    assert all("description" in entry and entry["description"].strip() for entry in metadata)


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


def test_original_seed_template_contains_multimodal_placeholders() -> None:
    """The reset seed should nudge generation toward image assets and visual validation."""
    seed = Path("pare/scenarios/generator/utils/original_seed_scenario.py").read_text(encoding="utf-8")

    assert "SandboxLocalFileSystem" in seed
    assert "Visual assets" in seed
    assert "log_has_agent_image_view" in seed
    assert "image inspection" in seed
