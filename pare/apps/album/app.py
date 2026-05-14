from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from are.simulation.apps.app import Protocol
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, disable_events
from are.simulation.utils import get_state_dict, uuid_hex
from are.simulation.utils.type_utils import type_check

if TYPE_CHECKING:
    from are.simulation.types import CompletedEvent

from are.simulation.apps import SandboxLocalFileSystem, VirtualFileSystem

from pare.apps.album.states import FolderList, PhotoDetail, PhotoList
from pare.apps.album.types import FolderInfo, FolderKind, Photo, PhotoStatus, ReturnedPhotos
from pare.apps.core import StatefulApp
from pare.apps.tool_decorators import pare_event_registered

logger = logging.getLogger(__name__)


class PhotosFolder:
    """Container managing photos within a single folder.

    The folder owns *references* to Photo metadata objects; the underlying image
    bytes always live in the connected filesystem at ``photo.file_path``.
    """

    def __init__(self, folder_name: str, kind: FolderKind = FolderKind.USER) -> None:
        """Initialize a photo folder.

        Args:
            folder_name (str): Name of the folder.
            kind (FolderKind): Folder kind (system/smart/user).
        """
        self.folder_name = folder_name
        self.kind = kind
        self.photos: dict[str, Photo] = {}

    def add_photo(self, photo: Photo) -> None:
        """Add a photo to this folder.

        Args:
            photo (Photo): Photo to add.
        """
        if not isinstance(photo, Photo):
            raise TypeError(f"Photo must be an instance of Photo, got {type(photo)}.")
        self.photos[photo.photo_id] = photo

    def remove_photo(self, photo_id: str) -> bool:
        """Remove a photo by ID.

        Args:
            photo_id (str): ID of photo to remove.

        Returns:
            bool: True if removed, False if not found.
        """
        if photo_id not in self.photos:
            return False
        del self.photos[photo_id]
        return True

    def get_photos(self, offset: int = 0, limit: int = 10) -> ReturnedPhotos:
        """Retrieve paginated photos, most recently taken first.

        Args:
            offset (int): Starting index.
            limit (int): Maximum number of photos to return.

        Returns:
            ReturnedPhotos: Paginated result container.
        """
        if not isinstance(offset, int):
            raise TypeError(f"Offset must be an integer, got {type(offset)}.")
        if offset < 0:
            raise ValueError("Offset must be non-negative.")
        if offset > len(self.photos):
            raise ValueError("Offset must be less than the number of photos.")

        ordered = sorted(self.photos.values(), key=lambda p: p.taken_at, reverse=True)
        total = len(ordered)
        end = min(offset + limit, total)
        returned = ordered[offset:end]

        return ReturnedPhotos(
            photos=returned,
            photos_range=(offset, end),
            total_returned_photos=len(returned),
            total_photos=total,
        )

    def get_photo_by_id(self, photo_id: str) -> Photo | None:
        """Lookup a photo by ID.

        Args:
            photo_id (str): Target photo ID.

        Returns:
            Photo | None: The photo, or None if not found.
        """
        return self.photos.get(photo_id)

    def search_photos(self, query: str, include_hidden: bool = False) -> list[Photo]:
        """Search photos within this folder using a text query.

        Searches file name, caption, description, tags, and location -- all of
        which are plain text, so this is a cheap operation (no image bytes loaded).

        Args:
            query (str): Search query.
            include_hidden (bool): If False, photos in HIDDEN status are excluded.

        Returns:
            list[Photo]: Matched photos.
        """
        out: list[Photo] = []
        for p in self.photos.values():
            if not include_hidden and p.status == PhotoStatus.HIDDEN:
                continue
            if p.matches(query):
                out.append(p)
        return out

    def get_state(self) -> dict[str, Any]:
        """Serialize folder state.

        Returns:
            dict[str, Any]: Serialized state.
        """
        state = get_state_dict(self, ["folder_name", "photos"])
        state["kind"] = self.kind.value
        return state

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Deserialize folder state.

        Args:
            state_dict (dict[str, Any]): State to load.
        """
        self.folder_name = state_dict["folder_name"]
        self.kind = FolderKind(state_dict.get("kind", FolderKind.USER.value))
        self.photos = {pid: Photo(**pdata) for pid, pdata in state_dict["photos"].items()}
        self.photos = dict(sorted(self.photos.items(), key=lambda item: item[1].taken_at, reverse=True))


@dataclass
class StatefulAlbumApp(StatefulApp):
    """A photo Album app that manages user's photos and folder organization.

    Mirrors the design of ``StatefulNotesApp`` but for images. Key design goal:
    keep multimodal token usage low by exposing a rich *text-metadata* index
    over the photos so the agent can locate the right photo via cheap text
    search before ever loading image bytes.

    Folder kinds:
    - ``SYSTEM``: "Camera Roll" (default destination for new photos) and
      "Hidden" (holds photos with status=HIDDEN). Cannot be deleted or renamed.
    - ``SMART``: "Favorites", "Recents", "Screenshots". Auto-populated from
      photo metadata; not directly mutable.
    - ``USER``: Any folder created via ``new_folder``.

    Photo statuses (``PhotoStatus``):
    - ``NORMAL``: default.
    - ``FAVORITED``: surfaces in the ``Favorites`` smart folder.
    - ``HIDDEN``: only visible inside the ``Hidden`` folder.
    - ``ARCHIVED``: hidden from main views but recoverable.

    Key Features:
    - Photo Management: create, update metadata, favorite, hide, archive, move, delete.
    - Folder Management: create/delete/rename user folders.
    - Text Search: search by caption / description / tags / location / file name
      without loading image bytes.
    - On-demand Viewing: ``view_photo`` loads the actual image via the filesystem
      app for multimodal inspection.
    - State Management: save and load application state.

    Notes:
    - Photo IDs are auto-generated.
    - Search is case-insensitive and substring-based.
    - Smart folders are read-only.
    """

    name: str | None = None
    view_limit: int = 10
    folders: dict[str, PhotosFolder] = field(default_factory=dict)
    internal_fs: SandboxLocalFileSystem | VirtualFileSystem | None = None

    SYSTEM_FOLDERS: tuple[str, ...] = ("Camera Roll", "Hidden")
    SMART_FOLDERS: tuple[str, ...] = ("Favorites", "Recents", "Screenshots")
    RECENTS_WINDOW_DAYS: int = 30

    def __post_init__(self) -> None:
        """Initialize app with default system and smart folders."""
        super().__init__(self.name or "album")

        for fname in self.SYSTEM_FOLDERS:
            if fname not in self.folders:
                self.folders[fname] = PhotosFolder(fname, kind=FolderKind.SYSTEM)
        for fname in self.SMART_FOLDERS:
            if fname not in self.folders:
                self.folders[fname] = PhotosFolder(fname, kind=FolderKind.SMART)

        self.default_folders = list(self.SYSTEM_FOLDERS) + list(self.SMART_FOLDERS)

        self.load_root_state()

    def connect_to_protocols(self, protocols: dict[Protocol, Any]) -> None:
        """Connect to filesystem protocol so we can view image bytes on demand."""
        file_system = protocols.get(Protocol.FILE_SYSTEM)
        if isinstance(file_system, (SandboxLocalFileSystem, VirtualFileSystem)):
            self.internal_fs = file_system

    def create_root_state(self) -> PhotoList:
        """Return the root navigation state (Camera Roll list)."""
        return PhotoList("Camera Roll")

    def get_state(self) -> dict[str, Any]:
        """Serialize app state."""
        return {
            "view_limit": self.view_limit,
            "folders": {k: v.get_state() for k, v in self.folders.items() if v.kind != FolderKind.SMART},
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Deserialize app state. Smart folders are rebuilt from metadata."""
        self.view_limit = state_dict.get("view_limit", 10)
        # Preserve system + smart shells; reload user folders.
        preserved = {n: f for n, f in self.folders.items() if f.kind == FolderKind.SMART}
        self.folders.clear()
        for folder_name, folder_state in state_dict.get("folders", {}).items():
            folder = PhotosFolder(folder_name)
            folder.load_state(folder_state)
            self.folders[folder_name] = folder
        for sys_name in self.SYSTEM_FOLDERS:
            if sys_name not in self.folders:
                self.folders[sys_name] = PhotosFolder(sys_name, kind=FolderKind.SYSTEM)
        for smart_name in self.SMART_FOLDERS:
            self.folders[smart_name] = preserved.get(smart_name, PhotosFolder(smart_name, kind=FolderKind.SMART))

    def reset(self) -> None:
        """Reset the app to empty state."""
        super().reset()
        for folder in self.folders.values():
            folder.photos.clear()

    def _iter_real_folders(self) -> list[PhotosFolder]:
        """Folders that physically own photos (everything except SMART)."""
        return [f for f in self.folders.values() if f.kind != FolderKind.SMART]

    def _get_photo_from_any_folder(self, photo_id: str) -> tuple[str, Photo] | None:
        """Find a photo across all real folders.

        Args:
            photo_id (str): Photo ID to find.

        Returns:
            tuple[str, Photo] | None: (folder name, photo) if found, else None.
        """
        for f in self._iter_real_folders():
            p = f.get_photo_by_id(photo_id)
            if p is not None:
                return (f.folder_name, p)
        return None

    def _smart_members(self, smart_name: str) -> list[Photo]:
        """Compute the dynamic members of a smart folder from metadata."""
        all_photos: list[Photo] = []
        for f in self._iter_real_folders():
            all_photos.extend(f.photos.values())

        if smart_name == "Favorites":
            return [p for p in all_photos if p.status == PhotoStatus.FAVORITED]
        if smart_name == "Screenshots":
            return [p for p in all_photos if "screenshot" in [t.lower() for t in p.tags]]
        if smart_name == "Recents":
            now = self.time_manager.time()
            window = self.RECENTS_WINDOW_DAYS * 24 * 3600
            return [p for p in all_photos if p.status != PhotoStatus.HIDDEN and (now - p.taken_at) <= window]
        return []

    def open_folder(self, folder: str) -> list[Photo]:
        """Open a folder and return the photos in it.

        For smart folders, the result is computed on the fly from photo metadata.

        Args:
            folder (str): Folder name.

        Returns:
            list[Photo]: Photos in the folder.

        Raises:
            KeyError: If folder does not exist.
        """
        if folder not in self.folders:
            raise KeyError(f"Folder {folder} does not exist")
        if not folder:
            raise ValueError("Folder name must be non-empty")

        f = self.folders[folder]
        if f.kind == FolderKind.SMART:
            return self._smart_members(folder)
        return list(f.photos.values())

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def new_folder(self, folder_name: str) -> str:
        """Create a new empty user folder.

        Args:
            folder_name (str): Name of the new folder.

        Returns:
            str: Name of the newly created folder.

        Raises:
            KeyError: If a folder with that name already exists.
            ValueError: If folder_name is empty.
        """
        if not folder_name or not folder_name.strip():
            raise ValueError("Folder name must be non-empty")
        if folder_name in self.folders:
            raise KeyError(f"Folder {folder_name} already exists")
        self.folders[folder_name] = PhotosFolder(folder_name, kind=FolderKind.USER)
        return folder_name

    @type_check
    @env_tool()
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def delete_folder(self, folder_name: str) -> str:
        """Delete a user folder and all its photos.

        System ("Camera Roll", "Hidden") and smart folders cannot be deleted.

        Args:
            folder_name (str): Folder to delete.

        Returns:
            str: Name of the deleted folder.

        Raises:
            KeyError: If folder does not exist or is a system/smart folder.
        """
        if folder_name not in self.folders:
            raise KeyError(f"Folder {folder_name} does not exist")
        folder = self.folders[folder_name]
        if folder.kind != FolderKind.USER:
            raise KeyError(f"Cannot delete {folder.kind.value} folder {folder_name}")

        folder.photos.clear()
        del self.folders[folder_name]
        logger.debug(f"Deleted folder {folder_name}")
        return folder_name

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def rename_folder(self, folder: str, new_folder: str) -> str:
        """Rename a user folder. System and smart folders cannot be renamed.

        Args:
            folder (str): Folder to rename.
            new_folder (str): New name.

        Returns:
            str: New folder name.

        Raises:
            KeyError: If source missing, target exists, or source is system/smart.
        """
        if folder not in self.folders:
            raise KeyError(f"Folder {folder} does not exist")
        if new_folder in self.folders:
            raise KeyError(f"Folder {new_folder} already exists")
        if self.folders[folder].kind != FolderKind.USER:
            raise KeyError(f"Cannot rename {self.folders[folder].kind.value} folder {folder}")
        self.folders[new_folder] = deepcopy(self.folders[folder])
        self.folders[new_folder].folder_name = new_folder
        del self.folders[folder]
        logger.debug(f"Renamed folder {folder} to {new_folder}")
        return new_folder

    @data_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def add_photo_with_time(
        self,
        folder: str = "Camera Roll",
        file_path: str = "",
        file_name: str = "",
        caption: str = "",
        description: str = "",
        tags: list[str] | None = None,
        location: str | None = None,
        mime_type: str = "image/jpeg",
        width: int | None = None,
        height: int | None = None,
        status: str = PhotoStatus.NORMAL.value,
        taken_at: str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        added_at: str | None = None,
    ) -> str:
        """Seed a photo with explicit timestamps (oracle / fixture helper).

        Args:
            folder (str): Destination folder. Auto-created if missing.
            file_path (str): Path of the image in the connected filesystem.
            file_name (str): Display file name (defaults to last segment of file_path).
            caption (str): Short caption.
            description (str): Longer description, used for search.
            tags (list[str] | None): Tags for filtering / smart folders.
            location (str | None): Location label.
            mime_type (str): MIME type, e.g. ``image/jpeg``.
            width (int | None): Pixel width.
            height (int | None): Pixel height.
            status (str): Photo status (normal/favorited/hidden/archived).
            taken_at (str): When the photo was taken (UTC ``YYYY-MM-DD HH:MM:SS``).
            added_at (str | None): When added to the album. Defaults to ``taken_at``.

        Returns:
            str: ID of the newly created photo.

        Raises:
            ValueError: On bad datetime format or empty file_path.
        """
        if not file_path:
            raise ValueError("file_path must be non-empty")
        try:
            taken_ts = datetime.strptime(taken_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
        except ValueError as e:
            raise ValueError("Invalid datetime format for taken_at. Use YYYY-MM-DD HH:MM:SS") from e
        if added_at is not None:
            try:
                added_ts = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
            except ValueError as e:
                raise ValueError("Invalid datetime format for added_at. Use YYYY-MM-DD HH:MM:SS") from e
        else:
            added_ts = taken_ts

        if folder not in self.folders:
            with disable_events():
                self.new_folder(folder)
        if self.folders[folder].kind == FolderKind.SMART:
            raise ValueError(f"Cannot add photos directly to smart folder {folder}")

        if not file_name:
            file_name = file_path.rsplit("/", 1)[-1]

        photo = Photo(
            photo_id=uuid_hex(self.rng),
            file_name=file_name,
            file_path=file_path,
            caption=caption,
            description=description,
            tags=list(tags) if tags else [],
            location=location,
            mime_type=mime_type,
            width=width,
            height=height,
            status=PhotoStatus(status),
            taken_at=taken_ts,
            added_at=added_ts,
            updated_at=added_ts,
        )
        self.folders[folder].add_photo(photo)
        return photo.photo_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def add_photo(
        self,
        file_path: str,
        folder: str = "Camera Roll",
        caption: str = "",
        description: str = "",
        tags: list[str] | None = None,
        location: str | None = None,
        mime_type: str = "image/jpeg",
    ) -> str:
        """Add a new photo to the album by referencing an image in the filesystem.

        The image bytes are NOT copied -- the photo record only stores a reference
        to ``file_path``, plus searchable text metadata.

        Args:
            file_path (str): Path of the image in the connected filesystem.
            folder (str): Destination folder.
            caption (str): Short caption used for search.
            description (str): Longer description used for search.
            tags (list[str] | None): Tags.
            location (str | None): Location label.
            mime_type (str): MIME type.

        Returns:
            str: ID of the newly created photo.

        Raises:
            KeyError: If folder not found.
            ValueError: If folder is a smart folder or file_path is empty.
        """
        if not file_path or not file_path.strip():
            raise ValueError("file_path must be non-empty")
        if folder not in self.folders:
            raise KeyError(f"Folder {folder} does not exist")
        if self.folders[folder].kind == FolderKind.SMART:
            raise ValueError(f"Cannot add photos directly to smart folder {folder}")

        file_name = file_path.rsplit("/", 1)[-1]
        now = self.time_manager.time()
        photo = Photo(
            photo_id=uuid_hex(self.rng),
            file_name=file_name,
            file_path=file_path,
            caption=caption,
            description=description,
            tags=list(tags) if tags else [],
            location=location,
            mime_type=mime_type,
            taken_at=now,
            added_at=now,
            updated_at=now,
        )
        self.folders[folder].add_photo(photo)
        return photo.photo_id

    @type_check
    @data_tool()
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def get_photo_by_id(self, photo_id: str) -> Photo:
        """Retrieve a photo's metadata by ID.

        This returns text metadata only -- no image bytes are loaded. Use
        ``view_photo`` when visual inspection is required.

        Args:
            photo_id (str): Target photo ID.

        Returns:
            Photo: The photo metadata.

        Raises:
            KeyError: If not found.
            ValueError: If photo_id is empty.
        """
        if not isinstance(photo_id, str):
            raise TypeError(f"Photo ID must be a string, got {type(photo_id)}.")
        if len(photo_id) == 0:
            raise ValueError("Photo ID must be non-empty.")
        result = self._get_photo_from_any_folder(photo_id)
        if result is None:
            raise KeyError(f"Photo {photo_id} not found")
        return result[1]

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def update_photo(
        self,
        photo_id: str,
        caption: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        location: str | None = None,
    ) -> str:
        """Update one or more text-metadata fields of a photo.

        Args:
            photo_id (str): Target photo ID.
            caption (str | None): New caption (omit to leave unchanged).
            description (str | None): New description.
            tags (list[str] | None): Replacement tag list.
            location (str | None): New location.

        Returns:
            str: ID of the updated photo.

        Raises:
            KeyError: If not found.
            ValueError: If no fields are provided.
        """
        if caption is None and description is None and tags is None and location is None:
            raise ValueError("At least one of caption / description / tags / location must be provided.")

        result = self._get_photo_from_any_folder(photo_id)
        if result is None:
            raise KeyError(f"Photo {photo_id} not found")
        folder, photo = result

        if caption is not None:
            photo.caption = caption
        if description is not None:
            photo.description = description
        if tags is not None:
            photo.tags = list(tags)
        if location is not None:
            photo.location = location

        photo.updated_at = self.time_manager.time()
        self.folders[folder].photos[photo.photo_id] = photo
        return photo_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def delete_photo(self, photo_id: str) -> str:
        """Delete a photo from the album.

        Args:
            photo_id (str): ID of photo to delete.

        Returns:
            str: ID of the deleted photo.

        Raises:
            KeyError: If not found.
        """
        result = self._get_photo_from_any_folder(photo_id)
        if result is None:
            raise KeyError(f"Photo {photo_id} not found")
        folder, _ = result
        self.folders[folder].remove_photo(photo_id)
        return photo_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def list_photos(self, folder: str, offset: int = 0, limit: int = 10) -> ReturnedPhotos:
        """List photos in a folder (paginated, newest-taken first).

        Smart folders are computed on the fly from photo metadata.

        Args:
            folder (str): Folder to list.
            offset (int): Starting index.
            limit (int): Maximum number to return.

        Returns:
            ReturnedPhotos: Paginated result.

        Raises:
            ValueError: If folder not found.
        """
        if folder not in self.folders:
            raise ValueError(f"Folder {folder} not found")
        f = self.folders[folder]
        if f.kind == FolderKind.SMART:
            ordered = sorted(self._smart_members(folder), key=lambda p: p.taken_at, reverse=True)
            total = len(ordered)
            end = min(offset + limit, total)
            return ReturnedPhotos(
                photos=ordered[offset:end],
                photos_range=(offset, end),
                total_returned_photos=len(ordered[offset:end]),
                total_photos=total,
            )
        return f.get_photos(offset, limit)

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def list_folders(self) -> list[FolderInfo]:
        """List all folders with their kind and photo count.

        Returns:
            list[FolderInfo]: Folder descriptors.
        """
        out: list[FolderInfo] = []
        for name, f in self.folders.items():
            count = len(self._smart_members(name)) if f.kind == FolderKind.SMART else len(f.photos)
            out.append(FolderInfo(name=name, kind=f.kind, photo_count=count))
        return out

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def move_photo(self, photo_id: str, source_folder_name: str, dest_folder_name: str) -> str:
        """Move a photo between (non-smart) folders.

        Args:
            photo_id (str): Photo to move.
            source_folder_name (str): Source folder.
            dest_folder_name (str): Destination folder.

        Returns:
            str: Photo ID.

        Raises:
            KeyError: If folders or photo not found.
            ValueError: If either folder is a smart folder.
        """
        if source_folder_name not in self.folders:
            raise KeyError(f"Folder {source_folder_name} not found.")
        if dest_folder_name not in self.folders:
            raise KeyError(f"Folder {dest_folder_name} not found.")
        if self.folders[source_folder_name].kind == FolderKind.SMART:
            raise ValueError(f"Cannot move from smart folder {source_folder_name}")
        if self.folders[dest_folder_name].kind == FolderKind.SMART:
            raise ValueError(f"Cannot move into smart folder {dest_folder_name}")
        photo = self.folders[source_folder_name].get_photo_by_id(photo_id)
        if photo is None:
            raise KeyError(f"Photo {photo_id} not found in folder {source_folder_name}.")
        self.folders[dest_folder_name].add_photo(photo)
        self.folders[source_folder_name].remove_photo(photo_id)
        return photo_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def duplicate_photo(self, folder_name: str, photo_id: str) -> str:
        """Duplicate a photo's metadata record in the same folder.

        The underlying image file is *not* copied -- both records share
        ``file_path``. Useful for variant captions / tags.

        Args:
            folder_name (str): Folder of the source photo.
            photo_id (str): Source photo ID.

        Returns:
            str: New photo ID.
        """
        if folder_name not in self.folders:
            raise KeyError(f"Folder {folder_name} not found.")
        current = self.folders[folder_name].get_photo_by_id(photo_id)
        if current is None:
            raise KeyError(f"Photo {photo_id} not found in folder {folder_name}.")

        new_id = uuid_hex(self.rng)
        new_photo = Photo(
            photo_id=new_id,
            file_name=current.file_name,
            file_path=current.file_path,
            caption=f"Copy of {current.caption}" if current.caption else "",
            description=current.description,
            tags=list(current.tags),
            location=current.location,
            mime_type=current.mime_type,
            width=current.width,
            height=current.height,
            status=PhotoStatus.NORMAL,
            taken_at=current.taken_at,
            added_at=self.time_manager.time(),
            updated_at=self.time_manager.time(),
        )
        self.folders[folder_name].add_photo(new_photo)
        return new_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.WRITE, event_type=EventType.AGENT)
    def set_photo_status(self, photo_id: str, status: str) -> str:
        """Change a photo's status (normal / favorited / hidden / archived).

        Use this to favorite, unfavorite, hide, unhide, or archive a photo.
        Setting status to NORMAL clears favorited/hidden/archived.

        Args:
            photo_id (str): Photo ID.
            status (str): One of ``normal``, ``favorited``, ``hidden``, ``archived``.

        Returns:
            str: Photo ID.

        Raises:
            KeyError: If photo not found.
            ValueError: If status is not a valid PhotoStatus value.
        """
        try:
            new_status = PhotoStatus(status)
        except ValueError as e:
            valid = ", ".join(s.value for s in PhotoStatus)
            raise ValueError(f"Invalid status '{status}'. Must be one of: {valid}") from e

        result = self._get_photo_from_any_folder(photo_id)
        if result is None:
            raise KeyError(f"Photo {photo_id} not found")
        folder, photo = result
        photo.status = new_status
        photo.updated_at = self.time_manager.time()
        self.folders[folder].photos[photo.photo_id] = photo
        return photo_id

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def search_photos(self, query: str, include_hidden: bool = False) -> list[Photo]:
        """Search ALL folders for photos whose text metadata matches ``query``.

        IMPORTANT: This is a *text-only* search over captions, descriptions,
        tags, file names, and locations. No image bytes are loaded. Prefer
        calling this (or ``search_photos_by_tags`` / ``search_photos_in_folder``)
        BEFORE ever calling ``view_photo`` -- it's the cheap way to narrow down
        candidates and keep multimodal token usage low.

        Args:
            query (str): Search query string.
            include_hidden (bool): If False (default), hidden photos are skipped.

        Returns:
            list[Photo]: Matching photos across all folders (no duplicates).
        """
        seen: set[str] = set()
        results: list[Photo] = []
        for f in self._iter_real_folders():
            for p in f.search_photos(query, include_hidden=include_hidden):
                if p.photo_id in seen:
                    continue
                seen.add(p.photo_id)
                results.append(p)
        return results

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def search_photos_in_folder(self, query: str, folder_name: str, include_hidden: bool = False) -> list[Photo]:
        """Search photos in a specific folder by text metadata.

        Args:
            query (str): Search query.
            folder_name (str): Folder to search.
            include_hidden (bool): Include HIDDEN photos.

        Returns:
            list[Photo]: Matching photos.

        Raises:
            KeyError: If folder not found.
        """
        if folder_name not in self.folders:
            raise KeyError(f"Folder {folder_name} not found.")
        f = self.folders[folder_name]
        if f.kind == FolderKind.SMART:
            return [p for p in self._smart_members(folder_name) if p.matches(query)]
        return f.search_photos(query, include_hidden=include_hidden)

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def search_photos_by_tags(
        self, tags: list[str], match_all: bool = False, include_hidden: bool = False
    ) -> list[Photo]:
        """Tag-based filter across all folders.

        Args:
            tags (list[str]): Tags to match (case-insensitive).
            match_all (bool): If True, photo must have every tag. If False (default),
                photo must have at least one of the tags.
            include_hidden (bool): Include HIDDEN photos.

        Returns:
            list[Photo]: Matching photos.
        """
        if not tags:
            return []
        wanted = {t.lower() for t in tags if t}
        results: list[Photo] = []
        seen: set[str] = set()
        for f in self._iter_real_folders():
            for p in f.photos.values():
                if not include_hidden and p.status == PhotoStatus.HIDDEN:
                    continue
                ptags = {t.lower() for t in p.tags}
                ok = (wanted <= ptags) if match_all else bool(wanted & ptags)
                if ok and p.photo_id not in seen:
                    seen.add(p.photo_id)
                    results.append(p)
        return results

    @type_check
    @app_tool()
    @pare_event_registered(operation_type=OperationType.READ, event_type=EventType.AGENT)
    def view_photo(self, photo_id: str) -> str:
        """Load and display the actual image bytes for ``photo_id``.

        Use this only AFTER ``search_photos`` (or similar) has narrowed the
        candidates -- this is the expensive multimodal operation that surfaces
        the image into the agent's context via the connected filesystem app.

        Args:
            photo_id (str): Photo ID to view.

        Returns:
            str: The file path that was displayed (the filesystem app emits
                 the actual image content as a separate event).

        Raises:
            KeyError: If photo not found.
            RuntimeError: If no filesystem is connected.
        """
        result = self._get_photo_from_any_folder(photo_id)
        if result is None:
            raise KeyError(f"Photo {photo_id} not found")
        _, photo = result
        if self.internal_fs is None:
            raise RuntimeError("No filesystem connected; cannot view photo.")
        with disable_events():
            self.internal_fs.display(photo.file_path)
        return photo.file_path

    def _resolve_photo_id(self, args: dict[str, Any], metadata: object | None) -> str | None:
        """Extract photo_id from args or metadata (mirror of note app helper)."""
        photo_id = args.get("photo_id")
        if isinstance(photo_id, str):
            return photo_id
        if isinstance(metadata, str):
            return metadata
        return None

    def handle_state_transition(self, event: CompletedEvent) -> None:
        """Map backend operations to navigation state transitions."""
        current_state = self.current_state
        fname = event.function_name()

        if current_state is None or fname is None:
            return

        action = event.action
        args = action.resolved_args or action.args
        metadata_value = event.metadata.return_value if event.metadata else None

        if isinstance(current_state, PhotoList):
            self._handle_photo_list_transition(fname, args, metadata_value)
        elif isinstance(current_state, PhotoDetail):
            self._handle_photo_detail_transition(fname, args, metadata_value)
        elif isinstance(current_state, FolderList):
            self._handle_folder_list_transition(fname, args, metadata_value)

    def _handle_photo_list_transition(self, fname: str, args: dict[str, Any], metadata: object | None) -> None:
        """Transitions from the photo list view."""
        if fname == "open":
            photo_id = self._resolve_photo_id(args, metadata)
            if photo_id:
                self.set_current_state(PhotoDetail(photo_id))
            return
        if fname == "list_folders":
            self.set_current_state(FolderList())

    def _handle_photo_detail_transition(self, fname: str, args: dict[str, Any], metadata: object | None) -> None:
        """Transitions from the photo detail view."""
        if fname == "delete" and self.navigation_stack:
            with disable_events():
                self.go_back()
            return
        if fname == "duplicate":
            photo_id = self._resolve_photo_id(args, metadata)
            if photo_id:
                self.set_current_state(PhotoDetail(photo_id))
            return
        if fname == "move":
            dest = args.get("dest_folder_name")
            if isinstance(dest, str):
                self.set_current_state(PhotoList(dest))

    def _handle_folder_list_transition(self, fname: str, args: dict[str, Any], metadata: object | None) -> None:
        """Transitions from the folder list view."""
        if fname == "open":
            folder = args.get("folder")
            if isinstance(folder, str):
                self.set_current_state(PhotoList(folder))
