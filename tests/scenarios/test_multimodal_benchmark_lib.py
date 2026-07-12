"""Tests for pare.scenarios.multimodal_benchmark.lib helpers."""

from __future__ import annotations

from pathlib import Path

from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox


def test_multimodal_helpers_normalize_jpeg_and_detect_image_view(tmp_path: Path) -> None:
    """Helpers should support sandbox JPEG normalization and image-view validation."""
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
