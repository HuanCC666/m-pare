"""Type definitions for the Album app.

These are separated to avoid circular imports between app.py and states.py.

Design note (token efficiency):
- A ``Photo`` stores rich *text* metadata (caption, description, tags, location,
  taken_at, mime_type, width, height, ...). Searching this metadata is cheap and
  does not require loading image bytes.
- The actual image content lives in the connected filesystem at ``file_path``.
  Agents should only call ``view_photo`` / filesystem ``display`` when visual
  confirmation is required, *after* narrowing the candidate set via metadata
  search. This keeps multimodal token usage low.
"""

from __future__ import annotations

import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class PhotoStatus(StrEnum):
    """Per-photo state flag.

    A photo can be in exactly one status at a time:
    - ``NORMAL``: visible in its folder and in default views.
    - ``FAVORITED``: also surfaces in the ``Favorites`` smart folder.
    - ``HIDDEN``: only visible inside the ``Hidden`` folder; excluded from search by default.
    - ``ARCHIVED``: removed from main views but recoverable.
    """

    NORMAL = "normal"
    FAVORITED = "favorited"
    HIDDEN = "hidden"
    ARCHIVED = "archived"


class FolderKind(StrEnum):
    """Type of an album folder.

    - ``SYSTEM``: built-in folder ("Camera Roll", "Hidden"). Cannot be deleted or renamed.
    - ``SMART``: automatically populated views ("Favorites", "Recents", "Screenshots").
      Cannot be written to directly; membership is derived from photo metadata.
    - ``USER``: regular user-created folder; fully editable.
    """

    SYSTEM = "system"
    SMART = "smart"
    USER = "user"


@dataclass
class Photo:
    """A single photo with text-searchable metadata.

    Note:
        ``file_path`` references a file in the connected sandbox / virtual
        filesystem. The image bytes themselves are NOT stored on the Photo
        object - callers should use ``view_photo`` to load them on demand.
    """

    photo_id: str
    file_name: str
    file_path: str
    caption: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    location: str | None = None
    mime_type: str = "image/jpeg"
    width: int | None = None
    height: int | None = None
    status: PhotoStatus = PhotoStatus.NORMAL
    taken_at: float = field(default_factory=lambda: time.time())
    added_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    def __post_init__(self) -> None:
        if len(self.photo_id) == 0:
            self.photo_id = uuid.uuid4().hex
        if isinstance(self.status, str):
            self.status = PhotoStatus(self.status)

    def __str__(self) -> str:
        tag_str = ", ".join(self.tags) if self.tags else "-"
        return textwrap.dedent(
            f"""
            ID: {self.photo_id}
            File: {self.file_name} ({self.file_path})
            Caption: {self.caption}
            Description: {self.description}
            Tags: {tag_str}
            Location: {self.location or "-"}
            MIME: {self.mime_type}
            Size: {self.width or "?"}x{self.height or "?"}
            Status: {self.status.value}
            Taken At: {datetime.fromtimestamp(self.taken_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")}
            Added At: {datetime.fromtimestamp(self.added_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")}
            Updated At: {datetime.fromtimestamp(self.updated_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")}
            """
        )

    @property
    def favorited(self) -> bool:
        """Convenience accessor: True when ``status == FAVORITED``."""
        return self.status == PhotoStatus.FAVORITED

    @property
    def hidden(self) -> bool:
        """Convenience accessor: True when ``status == HIDDEN``."""
        return self.status == PhotoStatus.HIDDEN

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match against searchable text metadata.

        Args:
            query (str): Free-text query.

        Returns:
            bool: True if query matches file name, caption, description, any tag, or location.
        """
        q = query.lower()
        if not q:
            return True
        haystack = [
            self.file_name.lower(),
            self.caption.lower(),
            self.description.lower(),
            (self.location or "").lower(),
            *[t.lower() for t in self.tags],
        ]
        return any(q in h for h in haystack)


@dataclass
class ReturnedPhotos:
    """Container for paginated photo results."""

    photos: list[Photo]
    photos_range: tuple[int, int]
    total_returned_photos: int
    total_photos: int


@dataclass
class FolderInfo:
    """Lightweight folder descriptor returned by ``list_folders``."""

    name: str
    kind: FolderKind
    photo_count: int
