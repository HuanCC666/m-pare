"""Tests for the stateful album app navigation and photo data."""

from __future__ import annotations

from typing import Any

import pytest
from are.simulation.types import (
    Action,
    CompletedEvent,
    EventMetadata,
    EventType,
)

from pare.apps.album.app import StatefulAlbumApp
from pare.apps.album.states import FolderList, PhotoDetail, PhotoList
from pare.apps.album.types import FolderKind, PhotoStatus, ReturnedPhotos
from pare.apps.proactive_aui import PAREAgentUserInterface
from pare.apps.system import HomeScreenSystemApp
from pare.environment import StateAwareEnvironmentWrapper


def _photo_list_state(app: StatefulAlbumApp) -> PhotoList:
    state = app.current_state
    assert isinstance(state, PhotoList)
    return state


def _photo_detail_state(app: StatefulAlbumApp) -> PhotoDetail:
    state = app.current_state
    assert isinstance(state, PhotoDetail)
    return state


def _folder_list_state(app: StatefulAlbumApp) -> FolderList:
    state = app.current_state
    assert isinstance(state, FolderList)
    return state


def _make_event(app: StatefulAlbumApp, func: Any, return_value: Any = None, **kwargs: Any) -> CompletedEvent:
    action = Action(function=func, args={"self": app, **kwargs}, app=app)

    metadata = EventMetadata()
    metadata.return_value = return_value

    return CompletedEvent(
        event_type=EventType.USER,
        action=action,
        metadata=metadata,
        event_time=0,
        event_id="album-test-event",
    )


def _seed_photo(
    app: StatefulAlbumApp,
    *,
    folder: str = "Camera Roll",
    file_path: str = "/sandbox/img.jpg",
    caption: str = "",
    tags: list[str] | None = None,
    taken_at: str = "2024-06-01 10:00:00",
    status: str = PhotoStatus.NORMAL.value,
) -> str:
    return app.add_photo_with_time(
        folder=folder,
        file_path=file_path,
        caption=caption,
        tags=tags or [],
        taken_at=taken_at,
        status=status,
    )


@pytest.fixture
def album_app() -> StatefulAlbumApp:
    return StatefulAlbumApp(name="album")


@pytest.fixture
def env_with_album() -> StateAwareEnvironmentWrapper:
    env = StateAwareEnvironmentWrapper()
    system_app = HomeScreenSystemApp(name="HomeScreen")
    aui_app = PAREAgentUserInterface()
    album = StatefulAlbumApp(name="album")
    env.register_apps([system_app, aui_app, album])
    env._open_app("album")
    return env


def test_starts_in_camera_roll_list(album_app: StatefulAlbumApp) -> None:
    assert isinstance(album_app.current_state, PhotoList)
    assert album_app.current_state.folder == "Camera Roll"
    assert album_app.navigation_stack == []


def test_open_photo_transitions_to_detail(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, caption="Sunset", taken_at="2024-01-02 12:00:00")
    photo = album_app.get_photo_by_id(pid)

    album_app.current_state.open(pid)
    event = _make_event(
        album_app,
        album_app.current_state.open,
        return_value=photo,
        photo_id=pid,
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoDetail)
    assert album_app.current_state.photo_id == pid
    assert album_app.navigation_stack
    assert isinstance(album_app.navigation_stack[-1], PhotoList)


def test_list_folders_transitions_to_folder_list(album_app: StatefulAlbumApp) -> None:
    album_app.current_state.list_folders()
    event = _make_event(
        album_app,
        album_app.current_state.list_folders,
        return_value=album_app.list_folders(),
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, FolderList)


def test_folder_open_transitions_to_photo_list(album_app: StatefulAlbumApp) -> None:
    album_app.new_folder("Trips")
    _seed_photo(album_app, folder="Trips", file_path="/sandbox/t1.jpg", taken_at="2024-03-01 08:00:00")
    album_app.set_current_state(FolderList())

    photos = album_app.open_folder("Trips")
    event = _make_event(
        album_app,
        album_app.current_state.open,
        return_value=photos,
        folder="Trips",
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoList)
    assert album_app.current_state.folder == "Trips"


def test_go_back_from_folder_list(album_app: StatefulAlbumApp) -> None:
    assert isinstance(album_app.current_state, PhotoList)

    album_app.set_current_state(FolderList())
    assert len(album_app.navigation_stack) == 1

    album_app.go_back()
    assert isinstance(album_app.current_state, PhotoList)
    assert album_app.current_state.folder == "Camera Roll"
    assert len(album_app.navigation_stack) == 0


def test_list_photos_pagination(album_app: StatefulAlbumApp) -> None:
    for i in range(12):
        _seed_photo(
            album_app,
            file_path=f"/sandbox/p{i}.jpg",
            taken_at=f"2024-01-{1 + (i % 28):02d} 12:00:00",
        )
    result = album_app.list_photos("Camera Roll", offset=0, limit=10)
    assert result.total_photos == 12
    assert len(result.photos) == 10
    assert result.photos_range == (0, 10)


def test_get_and_load_state_preserves_photos(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, caption="Persist me", taken_at="2024-05-10 15:00:00")
    state = album_app.get_state()

    new_app = StatefulAlbumApp(name="album")
    new_app.load_state(state)

    loaded = new_app.get_photo_by_id(pid)
    assert loaded is not None
    assert loaded.caption == "Persist me"


def test_move_photo_transitions_to_destination_list(album_app: StatefulAlbumApp) -> None:
    album_app.new_folder("Archive")
    pid = _seed_photo(album_app, folder="Camera Roll", file_path="/sandbox/move_me.jpg")

    album_app.set_current_state(PhotoDetail(pid))
    album_app.current_state.move("Archive")
    event = _make_event(
        album_app,
        album_app.current_state.move,
        return_value=pid,
        dest_folder_name="Archive",
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoList)
    assert album_app.current_state.folder == "Archive"
    assert album_app.get_photo_by_id(pid) is not None
    assert album_app.folders["Camera Roll"].get_photo_by_id(pid) is None
    assert album_app.folders["Archive"].get_photo_by_id(pid) is not None


def test_delete_with_stack_pops_to_previous_list(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, file_path="/sandbox/del.jpg")

    assert isinstance(album_app.current_state, PhotoList)
    album_app.set_current_state(PhotoDetail(pid))
    assert len(album_app.navigation_stack) == 1

    album_app.current_state.delete()
    event = _make_event(album_app, album_app.current_state.delete, return_value=pid)
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoList)
    assert len(album_app.navigation_stack) == 0
    with pytest.raises(KeyError):
        album_app.get_photo_by_id(pid)


def test_duplicate_transitions_to_new_detail(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, caption="Original", file_path="/sandbox/dup.jpg", taken_at="2024-02-01 09:00:00")
    album_app.set_current_state(PhotoDetail(pid))

    new_id = album_app.duplicate_photo("Camera Roll", pid)
    event = _make_event(
        album_app,
        album_app.current_state.duplicate,
        return_value=new_id,
        photo_id=new_id,
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoDetail)
    assert album_app.current_state.photo_id == new_id
    assert new_id != pid
    dup = album_app.get_photo_by_id(new_id)
    assert dup.caption == "Copy of Original"


def test_list_photos_no_navigation_change(album_app: StatefulAlbumApp) -> None:
    for i in range(5):
        _seed_photo(album_app, file_path=f"/sandbox/n{i}.jpg", taken_at=f"2024-04-{i + 1:02d} 10:00:00")

    assert isinstance(album_app.current_state, PhotoList)
    result = _photo_list_state(album_app).list_photos(offset=0, limit=3)
    assert isinstance(result, ReturnedPhotos)
    assert result.total_photos == 5

    event = _make_event(
        album_app,
        album_app.current_state.list_photos,
        return_value=result,
        offset=0,
        limit=3,
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoList)
    assert album_app.current_state.folder == "Camera Roll"


def test_refresh_keeps_photo_detail(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, caption="Before", taken_at="2024-07-01 11:00:00")
    album_app.set_current_state(PhotoDetail(pid))

    album_app.update_photo(pid, caption="After")
    refreshed = _photo_detail_state(album_app).refresh()
    assert refreshed.caption == "After"

    event = _make_event(album_app, album_app.current_state.refresh, return_value=refreshed)
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoDetail)
    assert album_app.current_state.photo_id == pid


def test_search_does_not_change_navigation_state(album_app: StatefulAlbumApp) -> None:
    _seed_photo(album_app, caption="mountain hike", taken_at="2024-08-01 08:00:00")
    assert isinstance(album_app.current_state, PhotoList)

    matches = _photo_list_state(album_app).search("mountain")
    assert len(matches) == 1

    event = _make_event(
        album_app,
        album_app.current_state.search,
        return_value=matches,
        keyword="mountain",
    )
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoList)


def test_favorite_surfaces_in_smart_folder(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, file_path="/sandbox/fav.jpg", taken_at="2024-09-01 12:00:00")
    album_app.set_photo_status(pid, PhotoStatus.FAVORITED.value)

    favs = album_app.list_photos("Favorites", offset=0, limit=20)
    assert favs.total_photos >= 1
    assert any(p.photo_id == pid for p in favs.photos)


def test_search_photos_by_tags_match_all(album_app: StatefulAlbumApp) -> None:
    a = _seed_photo(
        album_app,
        file_path="/sandbox/a.jpg",
        tags=["beach", "2024"],
        taken_at="2024-10-01 10:00:00",
    )
    _seed_photo(
        album_app,
        file_path="/sandbox/b.jpg",
        tags=["beach"],
        taken_at="2024-10-02 10:00:00",
    )

    both = album_app.search_photos_by_tags(["beach", "2024"], match_all=True)
    ids = {p.photo_id for p in both}
    assert ids == {a}


def test_cannot_delete_system_folder(album_app: StatefulAlbumApp) -> None:
    with pytest.raises(KeyError):
        album_app.delete_folder("Camera Roll")


def test_rename_user_folder(album_app: StatefulAlbumApp) -> None:
    album_app.new_folder("OldAlbum")
    _seed_photo(album_app, folder="OldAlbum", file_path="/sandbox/x.jpg", taken_at="2024-11-01 10:00:00")

    new_name = album_app.rename_folder("OldAlbum", "NewAlbum")
    assert new_name == "NewAlbum"
    assert "OldAlbum" not in album_app.folders
    assert "NewAlbum" in album_app.folders


def test_photo_detail_on_enter_loads_metadata(album_app: StatefulAlbumApp) -> None:
    pid = _seed_photo(album_app, caption="Detail load", taken_at="2024-12-01 10:00:00")
    detail = PhotoDetail(pid)
    detail.bind_to_app(album_app)
    assert detail._photo is None
    detail.on_enter()
    assert detail._photo is not None
    assert detail._photo.photo_id == pid
    assert detail._photo.caption == "Detail load"


def test_delete_with_empty_stack_does_not_navigate_away(album_app: StatefulAlbumApp) -> None:
    """With an empty stack, delete does not trigger go_back (handler is gated on stack)."""
    pid = _seed_photo(album_app, file_path="/sandbox/alone.jpg")
    detail = PhotoDetail(pid)
    detail.bind_to_app(album_app)
    detail.on_enter()
    album_app.current_state = detail
    album_app.navigation_stack.clear()

    event = _make_event(album_app, detail.delete, return_value=pid)
    album_app.handle_state_transition(event)

    assert isinstance(album_app.current_state, PhotoDetail)
    assert album_app.get_photo_by_id(pid) is not None


class TestAlbumIntegration:
    def test_open_favorite_flow(self, env_with_album: StateAwareEnvironmentWrapper) -> None:
        app = env_with_album.get_app_with_class(StatefulAlbumApp)
        pid = _seed_photo(app, caption="integration shot", taken_at="2024-06-15 14:00:00")

        _photo_list_state(app).open(pid)
        assert isinstance(app.current_state, PhotoDetail)

        _photo_detail_state(app).favorite()

        listed = app.list_photos("Favorites", offset=0, limit=50)
        assert any(p.photo_id == pid for p in listed.photos)
        assert app.get_photo_by_id(pid).status == PhotoStatus.FAVORITED

    def test_folder_navigation_flow(self, env_with_album: StateAwareEnvironmentWrapper) -> None:
        app = env_with_album.get_app_with_class(StatefulAlbumApp)
        app.new_folder("Work")
        pid = _seed_photo(app, folder="Work", file_path="/sandbox/w.jpg", taken_at="2024-06-20 09:00:00")

        _photo_list_state(app).list_folders()
        app.set_current_state(FolderList())

        _folder_list_state(app).open("Work")
        assert isinstance(app.current_state, PhotoList)
        assert app.current_state.folder == "Work"

        rows = _photo_list_state(app).list_photos(offset=0, limit=10)
        assert rows.total_photos == 1
        assert rows.photos[0].photo_id == pid


def test_list_folders_includes_kinds(album_app: StatefulAlbumApp) -> None:
    infos = album_app.list_folders()
    kinds = {i.name: i.kind for i in infos}
    assert kinds["Camera Roll"] == FolderKind.SYSTEM
    assert kinds["Favorites"] == FolderKind.SMART
