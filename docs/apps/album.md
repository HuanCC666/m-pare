# Stateful Album App

`pare.apps.album.app.StatefulAlbumApp` layers PARE navigation on top of a simulated photo library. It opens in `PhotoList("Camera Roll")` and moves between the per-folder grid, single-photo detail, and the folder picker based on completed user tools.

The app keeps **metadata** (caption, description, tags, location, file name, status) in memory and only references image bytes on disk via `file_path`. Text search is cheap; loading pixels is reserved for explicit `view` / `view_photo` calls so multimodal runs stay bounded.

## Folders and photo status

- **System folders** (`FolderKind.SYSTEM`): `Camera Roll` (default for new photos) and `Hidden` (photos with hidden status). Cannot be deleted or renamed.
- **Smart folders** (`FolderKind.SMART`): `Favorites`, `Recents`, `Screenshots` — membership is derived from metadata and time windows, not direct edits. You cannot add or move photos into a smart folder via the album API.
- **User folders**: created with `new_folder` from the folder list (or the agent API); can be renamed or deleted when empty of policy constraints.

Photo **status** (`PhotoStatus`): `normal`, `favorited` (surfaces in Favorites), `hidden` (mainly visible under Hidden), `archived` (out of default flows but recoverable via status).

## Navigation states

### PhotoList

Grid/list context for one folder (`context.folder`, defaulting to the folder name passed at construction, usually `Camera Roll` at root).

| Tool | Backend call(s) | Returns | Navigation effect |
| --- | --- | --- | --- |
| `list_photos(offset, limit)` | `StatefulAlbumApp.list_photos(folder, offset, limit)` | `ReturnedPhotos` | Remains in `PhotoList` |
| `open(photo_id)` | `StatefulAlbumApp.get_photo_by_id(photo_id)` | `Photo` (metadata only) | → `PhotoDetail(photo_id)` |
| `search(keyword, include_hidden)` | `StatefulAlbumApp.search_photos_in_folder(...)` | `list[Photo]` | Remains in `PhotoList` |
| `search_by_tags(tags, match_all)` | `StatefulAlbumApp.search_photos_by_tags(...)` | `list[Photo]` | Remains in `PhotoList` |
| `list_folders()` | `StatefulAlbumApp.list_folders()` | `list[FolderInfo]` | → `FolderList` |

### PhotoDetail

Single-photo screen (`context.photo_id`). Metadata is loaded on enter; image bytes are not loaded until `view`.

| Tool | Backend call(s) | Returns | Navigation effect |
| --- | --- | --- | --- |
| `refresh()` | `StatefulAlbumApp.get_photo_by_id(photo_id)` | `Photo` | Remains in `PhotoDetail` |
| `view()` | `StatefulAlbumApp.view_photo(photo_id)` | `str` (file path displayed) | Remains in `PhotoDetail` |
| `favorite()` | `StatefulAlbumApp.set_photo_status(photo_id, "favorited")` | `str` (photo id) | Remains in `PhotoDetail` |
| `unfavorite()` | `StatefulAlbumApp.set_photo_status(photo_id, "normal")` | `str` | Remains in `PhotoDetail` |
| `hide()` | `StatefulAlbumApp.set_photo_status(photo_id, "hidden")` | `str` | Remains in `PhotoDetail` |
| `archive()` | `StatefulAlbumApp.set_photo_status(photo_id, "archived")` | `str` | Remains in `PhotoDetail` |
| `delete()` | `StatefulAlbumApp.delete_photo(photo_id)` | `str` | On success pops navigation stack (`go_back`) |
| `duplicate()` | Resolves folder + `StatefulAlbumApp.duplicate_photo(...)` | `str` (new photo id) | → `PhotoDetail(new_id)` |
| `move(dest_folder_name)` | Resolves folder + `StatefulAlbumApp.move_photo(...)` | `str` | → `PhotoList(dest_folder_name)` |

### FolderList

Album-wide folder picker and global text search entry point.

| Tool | Backend call(s) | Returns | Navigation effect |
| --- | --- | --- | --- |
| `list_folders()` | `StatefulAlbumApp.list_folders()` | `list[FolderInfo]` | Remains in `FolderList` |
| `open(folder)` | `StatefulAlbumApp.open_folder(folder)` | `list[Photo]` | → `PhotoList(folder)` |
| `new_folder(folder_name)` | `StatefulAlbumApp.new_folder(folder_name)` | `str` | Remains in `FolderList` |
| `search(keyword, include_hidden)` | `StatefulAlbumApp.search_photos(...)` | `list[Photo]` | Remains in `FolderList` |

## Summary table

| State | Context | Transitions out | Typical self-loops |
| --- | --- | --- | --- |
| **PhotoList** | `folder` | `open` → `PhotoDetail`, `list_folders` → `FolderList` | `list_photos`, `search`, `search_by_tags` |
| **PhotoDetail** | `photo_id` | `delete` → previous state, `duplicate` → `PhotoDetail(new)`, `move` → `PhotoList(dest)` | `refresh`, `view`, status toggles |
| **FolderList** | — | `open` → `PhotoList(folder)` | `list_folders`, `new_folder`, `search` |

## Navigation helpers

- Transitions are implemented in `StatefulAlbumApp.handle_state_transition` and the `_handle_*_transition` helpers.
- `go_back()` is provided by `StatefulApp` when the stack has history (same pattern as other stateful apps).
- After `move`, the UI context switches to the destination folder’s `PhotoList` so the user continues browsing there.

## Agent-level API (non-navigation tools)

In addition to the screen-bound `@user_tool` methods above, the album registers `@app_tool` handlers on `StatefulAlbumApp` for benchmark agents: e.g. `add_photo`, `get_photo_by_id`, `update_photo`, `list_photos`, `list_folders`, `move_photo`, `duplicate_photo`, `set_photo_status`, `search_photos`, `search_photos_in_folder`, `search_photos_by_tags`, `view_photo`, `new_folder`, `delete_folder`, `rename_folder`. Fixture/oracle seeding may use `add_photo_with_time` (`@data_tool`). These do not themselves change navigation state unless the scenario also completes a user-tool event that the transition handler recognizes.
