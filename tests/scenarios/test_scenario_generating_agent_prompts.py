"""Tests for pare.scenarios.generator.prompt.scenario_generating_agent_prompts."""

from __future__ import annotations

from pathlib import Path

from pare.scenarios.generator.scenario_generator import (
    build_import_instructions_block,
    prepare_prompt_context_data,
)
from pare.scenarios.generator.utils.apps_init_instructions import ScenarioWithAllPAREApps


def test_generator_context_includes_multimodal_apps_and_imports() -> None:
    """The generator context should expose Album and Files image tools."""
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
    assert "Socially plausible action ownership" in prompts.SCENARIO_DESCRIPTION_SYSTEM_PROMPT
    assert "must not ask them to reorganize" in prompts.SCENARIO_DESCRIPTION_USER_PROMPT
    assert "rice_cooker_photo_cart_suggestion.py" in prompts.SCENARIO_DESCRIPTION_USER_PROMPT
    assert "calendar_conflict_urgent_reschedule.py" not in prompts.SCENARIO_DESCRIPTION_SYSTEM_PROMPT
    assert "image inspection" in prompts.VALIDATION_SYSTEM_PROMPT.lower()
    assert hasattr(prompts, "ASSET_PLANNING_SYSTEM_PROMPT")
    assert "assets.json" in prompts.ASSET_PLANNING_SYSTEM_PROMPT


def test_original_seed_template_contains_multimodal_placeholders() -> None:
    """The reset seed should nudge generation toward image assets and visual validation."""
    seed = Path("pare/scenarios/generator/utils/original_seed_scenario.py").read_text(encoding="utf-8")

    assert "SandboxLocalFileSystem" in seed
    assert "Visual assets" in seed
    assert "log_has_agent_image_view" in seed
    assert "image inspection" in seed
