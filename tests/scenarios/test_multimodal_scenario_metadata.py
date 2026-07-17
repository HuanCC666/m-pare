"""Tests for pare/scenarios/multimodal_scenario_metadata.json coverage."""

from __future__ import annotations

import ast
import json
from pathlib import Path


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
