"""Scenario: Agent shares indoor venue photos with Bob after a Messages request."""

from __future__ import annotations

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import (
    AbstractEnvironment,
    Action,
    Event,
    EventRegisterer,
    EventType,
)

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulAlbumApp,
    StatefulMessagingApp,
)
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS_DIR = Path(__file__).parent / "assets" / "bob_indoor_venue_photos_share"


@register_scenario("bob_indoor_venue_photos_share")
class BobIndoorVenuePhotosShare(PAREScenario):
    """Agent shares indoor venue photos with Bob after he asks via Messages following a garden wedding venue tour.

    The device owner just returned from a garden wedding venue tour. Bob messages
    asking how the tour went and specifically requests the **indoor** shots — the
    dining tables and the indoor reception area (not the outdoor garden photos, which
    he can already picture). The Camera Roll has six of today's tour photos with
    generic ``IMG_*.jpg`` names and capture timestamps only (no captions or tags).
    Three are indoor dining/ballroom shots and three are outdoor garden decoys;
    indoor vs. outdoor cannot be determined from file name or text metadata alone.
    The assistant must:
    1. Read Bob's incoming message and infer the indoor-only request.
    2. Filter the Camera Roll to **today's** photos via ``list_photos(..., taken_on=...)``.
    3. **View** candidate photos and use vision to pick the indoor dining/reception shots.
    4. Propose sending those indoor photos to Bob, then attach after user acceptance.

    Place six ``IMG_*.jpg`` files under ``assets/bob_indoor_venue_photos_share/``
    (see ``PHOTO_FIXTURES``). Entries with ``indoor=True`` are sent to Bob in the
    oracle. This scenario exercises multimodal photo filtering in Album,
    vision-based indoor/outdoor discrimination against same-day decoys, and gated
    photo sharing via Messages. Constraints:
    - Ask one proactive accept/reject question before messaging Bob.
    - Bob only asks the user to send photos; he must not request folder creation, photo moves, or any local reorganization.
    - User responses are limited to accept/reject style..
    """

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps; seed today's venue tour album rows and a Bob conversation.

        Six ``IMG_*.jpg`` fixtures from ``assets/bob_indoor_venue_photos_share/``
        are copied into the sandbox ``/photos`` folder and registered in the Album
        ``Camera Roll`` with capture timestamps only (no captions / tags). Three
        entries (``indoor=True``) are the indoor dining/ballroom shots the agent
        must send to Bob; the other three are outdoor garden decoys. A pre-existing
        Messages conversation with Bob is seeded with one historical message so the
        incoming tour request in Step 3 lands in an existing thread.
        """
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files

        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # Six tour photos taken today (scenario_day). ``indoor=True`` entries are
        # the indoor dining/reception shots Bob asks for; ``indoor=False`` are
        # outdoor garden decoys. ``env_key`` allows a runtime path override.
        photo_fixtures: tuple[dict[str, Any], ...] = (
            {
                "asset": "IMG_2411.jpg",
                "sandbox": "/photos/IMG_2411.jpg",
                "taken_time": "07:15:00",
                "indoor": True,
                "env_key": "PARE_VENUE_INDOOR_DINING_PHOTO_PATH",
            },
            {
                "asset": "IMG_2413.jpg",
                "sandbox": "/photos/IMG_2413.jpg",
                "taken_time": "07:35:00",
                "indoor": True,
                "env_key": "PARE_VENUE_INDOOR_BALLROOM_PHOTO_PATH",
            },
            {
                "asset": "IMG_2416.jpg",
                "sandbox": "/photos/IMG_2416.jpg",
                "taken_time": "07:55:00",
                "indoor": True,
                "env_key": "PARE_VENUE_INDOOR_BANQUET_PHOTO_PATH",
            },
            {
                "asset": "IMG_2410.jpg",
                "sandbox": "/photos/IMG_2410.jpg",
                "taken_time": "08:10:00",
                "indoor": False,
                "env_key": "PARE_VENUE_OUTDOOR_TERRACE_PHOTO_PATH",
            },
            {
                "asset": "IMG_2412.jpg",
                "sandbox": "/photos/IMG_2412.jpg",
                "taken_time": "08:25:00",
                "indoor": False,
                "env_key": "PARE_VENUE_OUTDOOR_ARCH_PHOTO_PATH",
            },
            {
                "asset": "IMG_2415.jpg",
                "sandbox": "/photos/IMG_2415.jpg",
                "taken_time": "08:40:00",
                "indoor": False,
                "env_key": "PARE_VENUE_OUTDOOR_PATHWAY_PHOTO_PATH",
            },
        )

        asset_subdir = _ASSETS_DIR
        self.files.mkdir("/photos", create_parents=True)

        env_dt = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.scenario_day = env_dt.strftime("%Y-%m-%d")

        self.indoor_photo_ids: list[str] = []
        self.oracle_indoor_sandbox_paths: list[str] = []
        self.oracle_outdoor_sandbox_paths: list[str] = []
        self.all_tour_sandbox_paths: list[str] = []

        for entry in photo_fixtures:
            override = os.getenv(entry["env_key"])
            asset_path = Path(override) if override else asset_subdir / entry["asset"]
            if not asset_path.exists():
                raise FileNotFoundError(
                    f"Scenario image not found: {asset_path}. Add {entry['asset']} under {asset_subdir}."
                )
            with self.files.open(entry["sandbox"], "wb") as f:
                f.write(jpeg_bytes_for_sandbox(asset_path.read_bytes()))

            taken_at = f"{self.scenario_day} {entry['taken_time']}"
            file_name = entry["sandbox"].rsplit("/", 1)[-1]
            photo_id = self.album.add_photo_with_time(
                folder="Camera Roll",
                file_path=entry["sandbox"],
                file_name=file_name,
                caption="",
                description="",
                tags=[],
                location=None,
                taken_at=taken_at,
            )
            self.all_tour_sandbox_paths.append(entry["sandbox"])
            if entry["indoor"]:
                self.indoor_photo_ids.append(photo_id)
                self.oracle_indoor_sandbox_paths.append(entry["sandbox"])
            else:
                self.oracle_outdoor_sandbox_paths.append(entry["sandbox"])

        bob_name = "Bob Garcia"
        bob_phone = "+1-555-0142"
        self.messaging.add_contacts([(bob_name, bob_phone)])
        self.bob_id = self.messaging.get_user_id(bob_name)
        if self.bob_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {bob_name}")

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.bob_id],
            title=bob_name,
        )
        yesterday_ts = self.start_time - 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.bob_id,
                content="Good luck at the venue tour today, hope it goes well!",
                timestamp=yesterday_ts,
            )
        )
        conversation.update_last_updated(yesterday_ts)
        self.messaging.add_conversation(conversation)

        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.messaging]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")

        # Resolve Bob's existing conversation and the three indoor photo IDs/paths
        # seeded in init_and_populate_apps (text metadata only; vision is required
        # to tell indoor dining/reception from the outdoor garden decoys).
        conversation_ids = messaging_app.get_existing_conversation_ids([self.bob_id])
        conversation_id = conversation_ids[0]
        indoor_dining_id, indoor_ballroom_id, indoor_banquet_id = self.indoor_photo_ids
        indoor_dining_path, indoor_ballroom_path, indoor_banquet_path = self.oracle_indoor_sandbox_paths

        with EventRegisterer.capture_mode():
            # Environment event: Bob messages asking for today's *indoor* venue tour
            # photos (dining tables + indoor reception), explicitly excluding the
            # outdoor garden shots. This is the exogenous trigger that motivates
            # every downstream oracle action.
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=conversation_id,
                sender_id=self.bob_id,
                content=(
                    "Hey, how did the garden wedding venue tour go? Could you send me "
                    "the indoor shots — the dining tables and the indoor reception area? "
                    "I can already picture the outdoor garden, just need the indoor ones."
                ),
            ).delayed(3)

            # Motivated by Bob's incoming message ("send me the indoor shots ... dining "
            # "tables and the indoor reception area"), the agent reads the conversation.
            read_message_event = (
                messaging_app.read_conversation(conversation_id=conversation_id, offset=0, limit=10)
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            # Bob's message references "today's" tour photos; agent lists Camera Roll
            # filtered to today to gather candidate photo IDs (text metadata only).
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

            # Visual inspection: file names/metadata cannot reveal indoor vs. outdoor,
            # so the agent views each candidate. Inspecting the indoor dining photo.
            view_indoor_dining_event = (
                album_app.view_photo(photo_id=indoor_dining_id)
                .oracle()
                .depends_on(list_today_roll_event, delay_seconds=1)
            )

            # Inspecting the indoor ballroom/reception photo.
            view_indoor_ballroom_event = (
                album_app.view_photo(photo_id=indoor_ballroom_id)
                .oracle()
                .depends_on(view_indoor_dining_event, delay_seconds=1)
            )

            # Inspecting the indoor banquet photo.
            view_indoor_banquet_event = (
                album_app.view_photo(photo_id=indoor_banquet_id)
                .oracle()
                .depends_on(view_indoor_ballroom_event, delay_seconds=1)
            )

            # After vision confirms the three indoor dining/reception shots (and the
            # outdoor garden decoys are excluded), the agent proposes sending them to
            # Bob, citing Bob's explicit request for "the indoor shots — the dining "
            # "tables and the indoor reception area".
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Bob messaged asking for today's indoor venue photos — the dining "
                        "tables and indoor reception area, not the outdoor garden shots. "
                        "I viewed today's Camera Roll and found three indoor dining/reception "
                        "photos. Want me to send those three to Bob?"
                    )
                )
                .oracle()
                .depends_on(view_indoor_banquet_event, delay_seconds=2)
            )

            # User accepts the proposal (accept/reject style per scenario constraints).
            acceptance_event = (
                aui.accept_proposal(content="Yes, please send the three indoor photos to Bob.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Write action (user-gated by acceptance): send first indoor photo to Bob.
            send_indoor_dining_event = (
                messaging_app.send_message(
                    user_id=self.bob_id,
                    content="Here are the indoor venue shots — first the dining room setup.",
                    attachment_path=indoor_dining_path,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Send second indoor photo (ballroom/reception) to Bob.
            send_indoor_ballroom_event = (
                messaging_app.send_message(
                    user_id=self.bob_id,
                    content="And the ballroom reception area.",
                    attachment_path=indoor_ballroom_path,
                )
                .oracle()
                .depends_on(send_indoor_dining_event, delay_seconds=2)
            )

            # Send third indoor photo (banquet hall) to Bob.
            send_indoor_banquet_event = (
                messaging_app.send_message(
                    user_id=self.bob_id,
                    content="And the last indoor banquet hall shot.",
                    attachment_path=indoor_banquet_path,
                )
                .oracle()
                .depends_on(send_indoor_ballroom_event, delay_seconds=2)
            )

        # TODO: Register ALL events here in self.events
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            list_today_roll_event,
            view_indoor_dining_event,
            view_indoor_ballroom_event,
            view_indoor_banquet_event,
            proposal_event,
            acceptance_event,
            send_indoor_dining_event,
            send_indoor_ballroom_event,
            send_indoor_banquet_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal creation and successful completion of the photo-sharing task."""
        try:
            log_entries = env.event_log.list_view()
            expected_indoor_paths = set(self.oracle_indoor_sandbox_paths)

            proposal_found = any(
                entry.event_type == EventType.AGENT
                and isinstance(entry.action, Action)
                and entry.action.class_name == "PAREAgentUserInterface"
                and entry.action.function_name == "send_message_to_user"
                for entry in log_entries
            )

            sent_attachments: set[str] = set()
            for entry in log_entries:
                if (
                    entry.event_type != EventType.AGENT
                    or not isinstance(entry.action, Action)
                    or entry.action.class_name != "StatefulMessagingApp"
                    or entry.action.function_name != "send_message"
                ):
                    continue

                args = entry.action.args or {}
                if args.get("user_id") != self.bob_id:
                    continue

                attachment = args.get("attachment_path")
                if attachment:
                    sent_attachments.add(str(attachment))

            task_completed = expected_indoor_paths <= sent_attachments

            if proposal_found and task_completed:
                return ScenarioValidationResult(success=True)

            failures: list[str] = []
            if not proposal_found:
                failures.append("agent did not proactively propose sending the indoor photos to the user")
            if not task_completed:
                failures.append(
                    "agent did not send all three correct indoor photos to Bob "
                    f"(expected {sorted(expected_indoor_paths)}; "
                    f"sent {sorted(sent_attachments)})"
                )

            return ScenarioValidationResult(
                success=False,
                rationale="; ".join(failures),
            )
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
