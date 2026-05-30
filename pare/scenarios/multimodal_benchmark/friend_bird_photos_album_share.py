"""Scenario: Agent finds today's bird photos in Album (by viewing) and sends them via Messages."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, CompletedEvent, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulAlbumApp, StatefulMessagingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS_DIR = Path(__file__).parent / "assets" / "friend_bird_photos_album_share"


class _PhotoFixture(TypedDict):
    asset: str
    sandbox: str
    taken_time: str
    bird: bool
    prior_day: bool
    env_key: str


# Source files live in assets/friend_bird_photos_album_share/ (camera-style IMG_* names).
# Copied to sandbox under /photos/ with the same basename. ``bird=True`` = must send to friend.
PHOTO_FIXTURES: tuple[_PhotoFixture, ...] = (
    {
        "asset": "IMG_20251122_141203.jpg",
        "sandbox": "/photos/IMG_20251122_141203.jpg",
        "taken_time": "14:12:03",
        "bird": True,
        "prior_day": False,
        "env_key": "PARE_SPARROW_PHOTO_PATH",
    },
    {
        "asset": "IMG_20251122_150512.jpg",
        "sandbox": "/photos/IMG_20251122_150512.jpg",
        "taken_time": "15:05:12",
        "bird": True,
        "prior_day": False,
        "env_key": "PARE_HAWK_PHOTO_PATH",
    },
    {
        "asset": "IMG_20251122_134015.jpg",
        "sandbox": "/photos/IMG_20251122_134015.jpg",
        "taken_time": "13:40:15",
        "bird": False,
        "prior_day": False,
        "env_key": "PARE_OUTING_GROUP_PHOTO_PATH",
    },
    {
        "asset": "IMG_20251122_142548.jpg",
        "sandbox": "/photos/IMG_20251122_142548.jpg",
        "taken_time": "14:25:48",
        "bird": False,
        "prior_day": False,
        "env_key": "PARE_OUTING_SELFIE_PHOTO_PATH",
    },
    {
        "asset": "IMG_20251122_161022.jpg",
        "sandbox": "/photos/IMG_20251122_161022.jpg",
        "taken_time": "16:10:22",
        "bird": False,
        "prior_day": False,
        "env_key": "PARE_OUTING_LUNCH_PHOTO_PATH",
    },
    {
        "asset": "IMG_20251121_093000.jpg",
        "sandbox": "/photos/IMG_20251121_093000.jpg",
        "taken_time": "09:30:00",
        "bird": False,
        "prior_day": True,
        "env_key": "PARE_YESTERDAY_PHOTO_PATH",
    },
)


@register_scenario("friend_bird_photos_album_share")
class FriendBirdPhotosAlbumShare(PAREScenario):
    """Agent shares today's bird photos after a friend asks via Messages.

    Scenario environment time is ``start_time`` (2025-11-22 evening). Album entries
    mirror a real camera roll: generic file names and capture timestamps only (no
    captions or tags). The assistant must:
    1. Read the incoming message and infer the request.
    2. Filter Camera Roll to **today's** photos via ``list_photos(..., taken_on=...)``.
    3. **View** candidates from that day and use vision to pick bird shots.
    4. Propose sending them to the friend, then attach after user acceptance.

    Place six ``IMG_*.jpg`` files under ``assets/friend_bird_photos_album_share/``
    (see ``PHOTO_FIXTURES``). Entries with ``bird=True`` are sent to the friend in the oracle.

    Constraints:
    - Ask one proactive accept/reject question before messaging the friend.
    - User responses are limited to accept/reject style.
    """

    start_time = datetime(2025, 11, 22, 18, 30, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    def _jpeg_from_asset(self, env_key: str, asset_path: Path) -> bytes:
        """Load JPEG bytes from ``asset_path`` or ``env_key`` override."""
        override = os.getenv(env_key)
        path = Path(override) if override else asset_path
        if not path.exists():
            raise FileNotFoundError(f"Scenario image not found: {path}. Add {asset_path.name} under {_ASSETS_DIR}.")
        return jpeg_bytes_for_sandbox(path.read_bytes())

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps; seed date-only album rows and a friend conversation."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files

        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.files.mkdir("/photos", create_parents=True)

        env_dt = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.scenario_day = env_dt.strftime("%Y-%m-%d")
        yesterday_day = (env_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        self.bird_photo_ids: list[str] = []
        self.oracle_bird_sandbox_paths: list[str] = []

        for entry in PHOTO_FIXTURES:
            jpeg = self._jpeg_from_asset(entry["env_key"], _ASSETS_DIR / entry["asset"])
            with self.files.open(entry["sandbox"], "wb") as f:
                f.write(jpeg)

            day = yesterday_day if entry["prior_day"] else self.scenario_day
            taken_at = f"{day} {entry['taken_time']}"
            file_name = entry["sandbox"].rsplit("/", 1)[-1]
            photo_id = self.album.add_photo_with_time(
                file_path=entry["sandbox"],
                file_name=file_name,
                caption="",
                description="",
                tags=[],
                location=None,
                taken_at=taken_at,
            )
            if entry["bird"]:
                self.bird_photo_ids.append(photo_id)
                self.oracle_bird_sandbox_paths.append(entry["sandbox"])

        friend_name = "Jamie Lin"
        friend_phone = "+1-555-0198"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        yesterday_ts = self.start_time - 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.friend_id,
                content="Yesterday's hike pics turned out great!",
                timestamp=yesterday_ts,
            )
        )
        conversation.update_last_updated(yesterday_ts)
        self.messaging.add_conversation(conversation)

        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.messaging]

    def build_events_flow(self) -> None:
        """Oracle: message → list today's photos by date → view birds → propose → send."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")

        conversation_ids = messaging_app.get_existing_conversation_ids([self.friend_id])
        conversation_id = conversation_ids[0]
        sparrow_id, hawk_id = self.bird_photo_ids
        bird_path_sparrow, bird_path_hawk = self.oracle_bird_sandbox_paths

        with EventRegisterer.capture_mode():
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=conversation_id,
                sender_id=self.friend_id,
                content=(
                    "Hey! Today was so fun — could you send me the bird photos we took? "
                    "The little one on the railing and the hawk over the sky. "
                    "My mom would love them."
                ),
            ).delayed(3)

            read_message_event = (
                messaging_app.read_conversation(conversation_id=conversation_id, offset=0, limit=10)
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            list_today_roll_event = (
                album_app.list_photos(
                    folder="Camera Roll",
                    offset=0,
                    limit=10,
                    taken_on=self.scenario_day,
                )
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            view_sparrow_event = (
                album_app.view_photo(photo_id=sparrow_id).oracle().depends_on(list_today_roll_event, delay_seconds=1)
            )

            view_hawk_event = (
                album_app.view_photo(photo_id=hawk_id).oracle().depends_on(view_sparrow_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Jamie asked for today's bird photos. I looked through today's Camera Roll "
                        "and found two bird shots. Send both to Jamie?"
                    )
                )
                .oracle()
                .depends_on(view_hawk_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please send both bird photos to Jamie.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            send_first_bird_event = (
                messaging_app.send_message(
                    user_id=self.friend_id,
                    content="Here are today's bird photos — first one from the railing.",
                    attachment_path=bird_path_sparrow,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            send_second_bird_event = (
                messaging_app.send_message(
                    user_id=self.friend_id,
                    content="And the hawk by the stream.",
                    attachment_path=bird_path_hawk,
                )
                .oracle()
                .depends_on(send_first_bird_event, delay_seconds=2)
            )

        self.events = [
            incoming_message_event,
            read_message_event,
            list_today_roll_event,
            view_sparrow_event,
            view_hawk_event,
            proposal_event,
            acceptance_event,
            send_first_bird_event,
            send_second_bird_event,
        ]

    @staticmethod
    def _agent_outbound_message_attachments(
        log_entries: list[CompletedEvent],
        *,
        friend_id: str,
    ) -> set[str]:
        """Collect attachment paths the agent sent to ``friend_id`` via Messaging."""
        paths: set[str] = set()
        for e in log_entries:
            if e.event_type != EventType.AGENT or not isinstance(e.action, Action):
                continue
            if e.action.class_name != "StatefulMessagingApp":
                continue
            if e.action.function_name not in ("send_message", "send_message_to_group_conversation"):
                continue
            args = e.action.args or {}
            if e.action.function_name == "send_message" and args.get("user_id") != friend_id:
                continue
            attachment = args.get("attachment_path")
            if attachment:
                paths.add(str(attachment))
        return paths

    @staticmethod
    def _fixture_paths(*, bird: bool | None = None) -> set[str]:
        """Sandbox paths from ``PHOTO_FIXTURES``, optionally filtered by ``bird`` flag."""
        if bird is None:
            return {e["sandbox"] for e in PHOTO_FIXTURES}
        return {e["sandbox"] for e in PHOTO_FIXTURES if e["bird"] is bird}

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate visual inspection, correct bird attachments, and no wrong sends."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))
            expected_bird_paths = self._fixture_paths(bird=True)
            wrong_paths = self._fixture_paths(bird=False)
            bird_ids = set(self.bird_photo_ids)

            message_read_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "read_conversation"
                for e in log_entries
            )

            album_today_filter_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulAlbumApp"
                and e.action.function_name == "list_photos"
                and (
                    (e.action.args or {}).get("taken_on") == self.scenario_day
                    or self.scenario_day in str((e.action.args or {}).get("min_date", ""))
                    or self.scenario_day in str((e.action.args or {}).get("max_date", ""))
                )
                for e in log_entries
            )

            bird_visual_found = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_paths=expected_bird_paths,
                photo_ids=bird_ids,
                min_views=2,
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            sent_attachments = self._agent_outbound_message_attachments(log_entries, friend_id=self.friend_id)
            correct_bird_photos_sent = expected_bird_paths <= sent_attachments
            wrong_photo_sent = bool(sent_attachments & wrong_paths)
            extra_non_bird_sent = bool(sent_attachments - expected_bird_paths - wrong_paths)

            success = (
                message_read_found
                and album_today_filter_found
                and bird_visual_found
                and proposal_found
                and correct_bird_photos_sent
                and not wrong_photo_sent
                and not extra_non_bird_sent
            )

            if not success:
                failed_checks: list[str] = []
                if not message_read_found:
                    failed_checks.append("agent did not read the friend's message in Messages")
                if not album_today_filter_found:
                    failed_checks.append(
                        f"agent did not list album photos filtered to today ({self.scenario_day}) via list_photos"
                    )
                if not bird_visual_found:
                    failed_checks.append(
                        "agent did not visually inspect both target bird photos (view_photo or Files "
                        "display on both bird image paths)"
                    )
                if not proposal_found:
                    failed_checks.append("agent did not proactively propose sending photos to the user")
                if not correct_bird_photos_sent:
                    missing = sorted(expected_bird_paths - sent_attachments)
                    failed_checks.append(
                        "agent did not send both correct bird photos to the friend "
                        f"(expected {sorted(expected_bird_paths)}; sent {sorted(sent_attachments)}; "
                        f"missing {missing})"
                    )
                if wrong_photo_sent:
                    failed_checks.append(
                        f"agent sent non-bird or wrong-day photos ({sorted(sent_attachments & wrong_paths)})"
                    )
                if extra_non_bird_sent:
                    failed_checks.append(
                        f"agent sent unexpected attachments: {sorted(sent_attachments - expected_bird_paths - wrong_paths)}"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
