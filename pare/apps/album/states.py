from __future__ import annotations

from typing import TYPE_CHECKING, cast

from are.simulation.agents.llm.types import MMObservation  # noqa: TC002 - runtime import required for get_type_hints()
from are.simulation.types import OperationType, disable_events

from pare.apps.album.types import (  # noqa: TC001 - runtime import required for get_type_hints()
    FolderInfo,
    Photo,
    ReturnedPhotos,
)
from pare.apps.core import AppState
from pare.apps.tool_decorators import pare_event_registered, user_tool

if TYPE_CHECKING:
    from pare.apps.album.app import StatefulAlbumApp


class PhotoList(AppState):
    """State representing the photo grid within a folder.

    From this state the user can list, search, open a photo, or jump to the
    folder list. Search here is text-only and very cheap -- prefer it before
    asking the agent to actually view image bytes.
    """

    def __init__(self, folder: str = "Camera Roll") -> None:
        """Initialize the list view.

        Args:
            folder (str): Folder name to display.
        """
        super().__init__()
        self.folder = folder

    def on_enter(self) -> None:
        """Lifecycle hook when entering PhotoList."""
        pass

    def on_exit(self) -> None:
        """Lifecycle hook when leaving PhotoList."""
        pass

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def go_back(self) -> None:
        """Navigate back to the previous state."""
        return None

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def list_photos(
        self,
        offset: int = 0,
        limit: int = 10,
        min_date: str | None = None,
        max_date: str | None = None,
        taken_on: str | None = None,
    ) -> ReturnedPhotos:
        """Return paginated photos in the current folder.

        Args:
            offset (int): Starting index for pagination.
            limit (int): Maximum number of photos to return.
            min_date (str | None): Inclusive lower bound on capture time (UTC).
            max_date (str | None): Exclusive upper bound on capture time (UTC).
            taken_on (str | None): Filter to a single calendar day (``YYYY-MM-DD``).

        Returns:
            ReturnedPhotos: Paginated photos container.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).list_photos(
                self.folder,
                offset,
                limit,
                min_date=min_date,
                max_date=max_date,
                taken_on=taken_on,
            )

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def open(self, photo_id: str) -> Photo:
        """Open a photo's detail view (metadata only, no image bytes).

        Args:
            photo_id (str): ID of the photo to open.

        Returns:
            Photo: Photo metadata.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).get_photo_by_id(photo_id)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def search(self, keyword: str, include_hidden: bool = False) -> list[Photo]:
        """Search photos in the current folder by text metadata.

        Use this to locate a photo quickly without loading image bytes.

        Args:
            keyword (str): Search keyword.
            include_hidden (bool): Include hidden photos.

        Returns:
            list[Photo]: Matching photos.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).search_photos_in_folder(
                keyword, self.folder, include_hidden=include_hidden
            )

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def search_by_tags(self, tags: list[str], match_all: bool = False) -> list[Photo]:
        """Filter photos by tags across the whole album.

        Args:
            tags (list[str]): Tags to filter by.
            match_all (bool): If True, photo must have every tag.

        Returns:
            list[Photo]: Matching photos.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).search_photos_by_tags(tags, match_all=match_all)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def list_folders(self) -> list[FolderInfo]:
        """List all folders.

        Returns:
            list[FolderInfo]: Folder descriptors.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).list_folders()


class PhotoDetail(AppState):
    """State showing the detail view of a single photo.

    On entering this state, only metadata is loaded. ``view`` must be invoked
    explicitly to load the actual image bytes (multimodal cost).
    """

    def __init__(self, photo_id: str) -> None:
        """Initialize PhotoDetail.

        Args:
            photo_id (str): ID of the photo being viewed.
        """
        super().__init__()
        self.photo_id = photo_id
        self._photo: Photo | None = None

    def on_enter(self) -> None:
        """Lifecycle hook when entering PhotoDetail."""
        with disable_events():
            self._photo = cast("StatefulAlbumApp", self.app).get_photo_by_id(self.photo_id)

    def on_exit(self) -> None:
        """Lifecycle hook when leaving PhotoDetail."""
        pass

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def go_back(self) -> None:
        """Navigate back to the previous state."""
        return None

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def refresh(self) -> Photo:
        """Reload metadata for this photo.

        Returns:
            Photo: Updated photo metadata.
        """
        with disable_events():
            self._photo = cast("StatefulAlbumApp", self.app).get_photo_by_id(self.photo_id)
        return cast("Photo", self._photo)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def view(self) -> MMObservation:
        """Display the actual image bytes for this photo.

        Only call this when visual inspection is required -- it is the
        expensive multimodal operation.

        Returns:
            MMObservation: Photo metadata plus an image attachment for the model.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).view_photo(self.photo_id)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def favorite(self) -> str:
        """Mark this photo as favorited.

        Returns:
            str: Photo ID.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).set_photo_status(self.photo_id, "favorited")

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def unfavorite(self) -> str:
        """Clear the favorited flag on this photo.

        Returns:
            str: Photo ID.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).set_photo_status(self.photo_id, "normal")

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def hide(self) -> str:
        """Hide this photo from default views.

        Returns:
            str: Photo ID.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).set_photo_status(self.photo_id, "hidden")

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def archive(self) -> str:
        """Archive this photo.

        Returns:
            str: Photo ID.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).set_photo_status(self.photo_id, "archived")

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def delete(self) -> str:
        """Delete this photo from the album.

        Returns:
            str: ID of the deleted photo.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).delete_photo(self.photo_id)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def duplicate(self) -> str:
        """Create a duplicate metadata record in the same folder.

        Returns:
            str: New photo ID.
        """
        with disable_events():
            result = cast("StatefulAlbumApp", self.app)._get_photo_from_any_folder(self.photo_id)
            if result is None:
                raise KeyError(f"Photo {self.photo_id} not found")
            folder_name, _ = result
            return cast("StatefulAlbumApp", self.app).duplicate_photo(folder_name, self.photo_id)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def move(self, dest_folder_name: str) -> str:
        """Move this photo to another folder.

        Args:
            dest_folder_name (str): Destination folder name.

        Returns:
            str: Photo ID.
        """
        with disable_events():
            result = cast("StatefulAlbumApp", self.app)._get_photo_from_any_folder(self.photo_id)
            if result is None:
                raise KeyError(f"Photo {self.photo_id} not found")
            source_folder_name, _ = result
            return cast("StatefulAlbumApp", self.app).move_photo(self.photo_id, source_folder_name, dest_folder_name)


class FolderList(AppState):
    """State displaying the list of album folders."""

    def on_enter(self) -> None:
        """Lifecycle hook when entering FolderList."""
        pass

    def on_exit(self) -> None:
        """Lifecycle hook when leaving FolderList."""
        pass

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def list_folders(self) -> list[FolderInfo]:
        """Return all folders.

        Returns:
            list[FolderInfo]: Folder descriptors.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).list_folders()

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def open(self, folder: str) -> list[Photo]:
        """Open the selected folder.

        Args:
            folder (str): Folder name.

        Returns:
            list[Photo]: Photos in the folder.
        """
        return cast("StatefulAlbumApp", self.app).open_folder(folder)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.WRITE)
    def new_folder(self, folder_name: str) -> str:
        """Create a new user folder.

        Args:
            folder_name (str): Folder name.

        Returns:
            str: Name of the created folder.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).new_folder(folder_name)

    @user_tool()
    @pare_event_registered(operation_type=OperationType.READ)
    def search(self, keyword: str, include_hidden: bool = False) -> list[Photo]:
        """Search photos across all folders.

        Args:
            keyword (str): Search keyword.
            include_hidden (bool): Include hidden photos.

        Returns:
            list[Photo]: Matching photos.
        """
        with disable_events():
            return cast("StatefulAlbumApp", self.app).search_photos(keyword, include_hidden=include_hidden)
