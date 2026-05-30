"""Helpers to detect agent image viewing in scenario event logs (Files, Email, Album)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from are.simulation.types import Action, CompletedEvent, EventType

if TYPE_CHECKING:
    from collections.abc import Collection

_FILE_IMAGE_FUNS = frozenset({"display", "cat", "read_document"})
_EMAIL_IMAGE_FUNS = frozenset({"get_email_by_id", "download_attachments"})


def agent_viewed_image(
    event: CompletedEvent,
    *,
    allow_any_event_type: bool,
    image_paths: frozenset[str],
    photo_ids: frozenset[str],
    email_id: str | None,
) -> bool:
    """True if this event plausibly exposed image content to the agent."""
    if not (allow_any_event_type or event.event_type == EventType.AGENT):
        return False
    act = event.action
    if not isinstance(act, Action):
        return False
    args = (act.resolved_args or act.args) or {}
    if photo_ids and act.class_name == "StatefulAlbumApp" and act.function_name == "view_photo":
        return str(args.get("photo_id", "")) in photo_ids
    if image_paths and act.class_name == "SandboxLocalFileSystem" and act.function_name in _FILE_IMAGE_FUNS:
        return str(args.get("path", "")) in image_paths
    if email_id and act.class_name == "StatefulEmailApp" and str(args.get("email_id", "")) == email_id:
        return act.function_name in _EMAIL_IMAGE_FUNS
    return False


def _view_dedupe_key(event: CompletedEvent) -> str | None:
    """Stable key for counting distinct image exposures."""
    act = event.action
    if not isinstance(act, Action):
        return None
    args = (act.resolved_args or act.args) or {}
    if act.class_name == "StatefulAlbumApp" and act.function_name == "view_photo":
        pid = args.get("photo_id")
        return f"album:{pid}" if pid else None
    if act.class_name == "SandboxLocalFileSystem" and act.function_name in _FILE_IMAGE_FUNS:
        path = args.get("path")
        return f"files:{path}" if path else None
    if act.class_name == "StatefulEmailApp" and act.function_name in _EMAIL_IMAGE_FUNS:
        eid = args.get("email_id")
        return f"email:{eid}" if eid else None
    return None


def log_has_agent_image_view(
    log_entries: list[CompletedEvent],
    *,
    allow_any_event_type: bool,
    image_path: str = "",
    email_id: str = "",
    image_paths: Collection[str] | None = None,
    photo_ids: Collection[str] | None = None,
    min_views: int = 1,
) -> bool:
    """True if the agent visually accessed matching image(s) enough times.

    Email / file scenarios (legacy): pass ``image_path`` and ``email_id``.

    Album scenarios: pass ``photo_ids`` and/or ``image_paths`` (sandbox paths).
    Use ``min_views`` when multiple distinct photos must be inspected.
    """
    paths = (
        frozenset(image_paths) if image_paths is not None else (frozenset({image_path}) if image_path else frozenset())
    )
    ids = frozenset(photo_ids) if photo_ids is not None else frozenset()
    eid = email_id or None

    if not paths and not ids and not eid:
        return False

    seen: set[str] = set()
    for entry in log_entries:
        if not agent_viewed_image(
            entry,
            allow_any_event_type=allow_any_event_type,
            image_paths=paths,
            photo_ids=ids,
            email_id=eid,
        ):
            continue
        key = _view_dedupe_key(entry)
        if key is not None:
            seen.add(key)
    return len(seen) >= min_views
