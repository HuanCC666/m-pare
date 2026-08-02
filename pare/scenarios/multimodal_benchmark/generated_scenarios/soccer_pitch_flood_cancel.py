"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer, EventType

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulCalendarApp,
    StatefulMessagingApp,
)
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

# TODO: replace these with paths from the resolved VisualAssetSpec / asset manifest.
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "soccer_pitch_flood_cancel"
)


@register_scenario("soccer_pitch_flood_cancel")
class SoccerPitchFloodCancel(PAREScenario):
    """Agent cancels a Saturday soccer game after a group-chat message and a photo showing a waterlogged pitch.

In the existing "Saturday Soccer Crew" group chat (Marco, Dana, and the user), Marco posts that he just walked by the pitch and attaches a photo of it; he says the pitch looks unplayable, asks the assistant to confirm from the photo whether this Saturday's 10am game should be called off, and if so to delete the matching calendar event and reply in the group chat so everyone knows it's cancelled. The pitch's actual condition (standing water and mud across the field) is only visible in the photo — the message never uses the word "flooded" or "waterlogged," it only says "looks unplayable" and asks the assistant to confirm from the image. The user already has a "Saturday Soccer at Riverside Pitch 3" calendar event tagged "sports" for this Saturday at 10am. The assistant must:
1. Read the group-chat message and download/inspect the attached pitch photo via the sandbox file system.
2. Visually confirm the pitch is waterlogged/unplayable so the cancellation proposal is grounded in what the image actually shows, not just Marco's opinion.
3. Search the calendar for the Saturday soccer event (by "soccer" search or the "sports" tag) and proactively propose deleting it and replying in the group chat to confirm the cancellation.
4. After user acceptance, delete the calendar event and send the cancellation reply to the group chat.

The pitch photo is seeded in SandboxLocalFileSystem (e.g., `/riverside_pitch_flooded.jpg`) and attached to Marco's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Saturday 10am game, the asks to confirm from the photo, delete the event, and reply in the group chat — come from the incoming message, while the image supplies the visual content (the waterlogged field condition) that the cancellation proposal depends on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + attachment download + outbound group reply, multimodal condition assessment of an outdoor field from a photo, and calendar search + event deletion — a condition-grounded cancellation flow rather than a new-event creation, an existing-event edit, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Visual asset: waterlogged pitch photo ---------------------------
        # Load the pitch photo from the resolved asset manifest directory and write it
        # into the sandbox filesystem so Step 3 can attach it to Marco's incoming
        # group-chat message and the agent can view it via files.display(...).
        self.pitch_photo_sandbox_path = "/riverside_pitch_flooded.jpg"
        pitch_asset_path = SCENARIO_ASSET_DIR / "riverside_pitch_flooded.jpg"
        if not pitch_asset_path.exists():
            raise FileNotFoundError(
                f"Scenario image not found: {pitch_asset_path}. Add riverside_pitch_flooded.jpg "
                f"under {SCENARIO_ASSET_DIR}."
            )
        pitch_bytes = jpeg_bytes_for_sandbox(pitch_asset_path.read_bytes())
        with self.files.open(self.pitch_photo_sandbox_path, "wb") as f:
            f.write(pitch_bytes)

        # --- Messaging: "Saturday Soccer Crew" group chat --------------------
        # Pre-existing group conversation among Marco, Dana, and the user with
        # baseline planning history. The triggering pitch photo + unplayable
        # message from Marco is delivered as an environment event in Step 3,
        # not here.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.friend_names = ["Marco Reyes", "Dana Cole"]
        self.messaging.add_users(self.friend_names)
        self.poster_name = "Marco Reyes"
        self.poster_id = self.messaging.get_user_id(self.poster_name)
        if self.poster_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {self.poster_name}")
        self.other_friend_id = self.messaging.get_user_id("Dana Cole")
        if self.other_friend_id is None:
            raise RuntimeError("Failed to resolve messaging user id for Dana Cole")

        group_conversation = ConversationV2(
            participant_ids=[
                self.messaging.current_user_id,
                self.poster_id,
                self.other_friend_id,
            ],
            title="Saturday Soccer Crew",
        )
        # Baseline planning history (pre-existing, before start_time).
        last_week_ts = self.start_time - 6 * 86_400
        yesterday_ts = self.start_time - 86_400
        baseline_messages = [
            MessageV2(
                sender_id=self.poster_id,
                content="Same time this Saturday at 10am, Riverside Pitch 3 — who's in?",
                timestamp=last_week_ts,
            ),
            MessageV2(
                sender_id=self.other_friend_id,
                content="I'm in. Bringing water and extra ball.",
                timestamp=last_week_ts + 3600,
            ),
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="In. See you Saturday at Riverside Pitch 3.",
                timestamp=yesterday_ts,
            ),
        ]
        for msg in baseline_messages:
            group_conversation.messages.append(msg)
        group_conversation.update_last_updated(yesterday_ts)
        self.messaging.add_conversation(group_conversation)
        self.group_conversation_id = group_conversation.conversation_id

        # --- Calendar: pre-existing Saturday soccer event --------------------
        # The user already has this event on their calendar; Step 3 will propose
        # deleting it after the photo confirms the pitch is unplayable.
        self.calendar = StatefulCalendarApp(name="Calendar")
        self.soccer_event_id = self.calendar.add_calendar_event(
            title="Saturday Soccer at Riverside Pitch 3",
            start_datetime="2025-11-22 10:00:00",
            end_datetime="2025-11-22 11:30:00",
            tag="sports",
            description="Weekly Saturday soccer game with Marco and Dana at Riverside Pitch 3.",
            location="Riverside Pitch 3",
            attendees=["Marco Reyes", "Dana Cole"],
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.calendar]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        # Plain values precomputed outside capture_mode so we never pass Event
        # objects into app tool APIs.
        group_conversation_id = self.group_conversation_id
        poster_id = self.poster_id
        pitch_photo_sandbox_path = self.pitch_photo_sandbox_path
        soccer_event_id = self.soccer_event_id

        marco_message = (
            "Hey everyone — I just walked by Riverside Pitch 3 and the field looks unplayable. "
            "I attached a photo so you can see for yourself. Can the assistant confirm from the "
            "photo whether we should call off this Saturday's 10am game? If yes, please delete "
            "the calendar event and reply here in the group chat so we all know it's cancelled. Thanks!"
        )

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT -------------------------------------
            # Exogenous trigger: Marco posts the pitch photo + "looks unplayable" ask
            # in the existing "Saturday Soccer Crew" group chat. Template-covered
            # env event (create_and_add_message) and the only env signal in the flow.
            incoming_pitch_message_event = messaging_app.create_and_add_message(
                conversation_id=group_conversation_id,
                sender_id=poster_id,
                content=marco_message,
                attachment_path=pitch_photo_sandbox_path,
            ).delayed(3)

            # --- ORACLE EVENTS ----------------------------------------------------
            # Motivation: incoming_pitch_message_event just delivered Marco's new
            # group-chat message ("Can the assistant confirm from the photo ...");
            # the agent reads the conversation to retrieve the message text + the
            # attached pitch photo (MMObservation exposes the image attachment).
            read_group_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=group_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_pitch_message_event, delay_seconds=3)
            )

            # Motivation: read_group_chat_event surfaced Marco's pitch photo
            # attachment (riverside_pitch_flooded.jpg); the agent displays the
            # image from the sandbox FS to visually confirm the field condition
            # (standing water / mud) before any cancellation proposal.
            inspect_pitch_photo_event = (
                files.display(path=pitch_photo_sandbox_path)
                .oracle()
                .depends_on(read_group_chat_event, delay_seconds=2)
            )

            # Motivation: Marco's env message asks the agent to "delete the
            # calendar event" for "this Saturday's 10am game"; the agent searches
            # the calendar by the "sports" tag to retrieve the matching event and
            # its event_id before proposing deletion.
            search_calendar_sports_event = (
                calendar_app.get_calendar_events_by_tag(tag="sports")
                .oracle()
                .depends_on(inspect_pitch_photo_event, delay_seconds=2)
            )

            # Motivation: grounded by incoming_pitch_message_event ("confirm from
            # the photo whether we should call off this Saturday's 10am game ...
            # delete the calendar event and reply here in the group chat") and by
            # the visual evidence from inspect_pitch_photo_event (waterlogged
            # pitch) plus search_calendar_sports_event (found the Saturday 10am
            # "Saturday Soccer at Riverside Pitch 3" event). Agent proposes the
            # cancellation plan and asks for user acceptance before writing.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Marco just posted in the Saturday Soccer Crew group chat with a photo "
                        "of Riverside Pitch 3 — the image shows standing water and mud across "
                        "the field, so it's clearly unplayable. He asked me to confirm from the "
                        "photo, and if it's unplayable, delete this Saturday's 10am calendar "
                        "event and reply in the group chat. I found the 'Saturday Soccer at "
                        "Riverside Pitch 3' event (tag: sports) on the calendar. Want me to "
                        "delete that event and post a cancellation reply in the group chat?"
                    )
                )
                .oracle()
                .depends_on(search_calendar_sports_event, delay_seconds=2)
            )

            # Motivation: user accepts the proposal_event plan (delete event +
            # reply in group chat), gating the write actions below.
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes, please delete the Saturday soccer calendar event and reply in "
                        "the group chat to confirm the cancellation."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Motivation: acceptance_event approved deleting the event; the agent
            # deletes the soccer event (event_id revealed by
            # search_calendar_sports_event earlier) from the calendar.
            delete_soccer_event_event = (
                calendar_app.delete_calendar_event(event_id=soccer_event_id)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: acceptance_event + Marco's env ask ("reply here in the
            # group chat so we all know it's cancelled"); agent posts the
            # cancellation reply in the same group conversation.
            send_group_reply_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=group_conversation_id,
                    content=(
                        "Confirmed from Marco's photo — Riverside Pitch 3 is waterlogged and "
                        "unplayable, so this Saturday's 10am soccer game is cancelled. I've "
                        "deleted the calendar event. See you all next week!"
                    ),
                )
                .oracle()
                .depends_on(delete_soccer_event_event, delay_seconds=2)
            )

        # Register ALL events so they actually execute.
        self.events: list[Event] = [
            incoming_pitch_message_event,
            read_group_chat_event,
            inspect_pitch_photo_event,
            search_calendar_sports_event,
            proposal_event,
            acceptance_event,
            delete_soccer_event_event,
            send_group_reply_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            agent_events = [e for e in log_entries if e.event_type == EventType.AGENT]

            soccer_event_id = self.soccer_event_id
            group_conversation_id = self.group_conversation_id

            # Check 1 — Proposal: agent proactively offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). Content is flexible;
            # we only assert the app/class/function identity.
            proposal_found = any(
                e.action is not None
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_events
            )

            # Check 2 — Task: agent completed the promised cancellation flow —
            # BOTH (a) deleted the specific seeded Saturday soccer calendar event
            # by its event_id, AND (b) posted the cancellation reply in the same
            # "Saturday Soccer Crew" group conversation. Fold both required
            # writes into a single task_completed boolean.
            event_deleted = any(
                e.action is not None
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "delete_calendar_event"
                and e.action.args.get("event_id") == soccer_event_id
                for e in agent_events
            )
            group_replied = any(
                e.action is not None
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == group_conversation_id
                for e in agent_events
            )
            task_completed = event_deleted and group_replied

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not event_deleted:
                    rationale = (
                        "task not completed: soccer calendar event "
                        f"{soccer_event_id} not deleted"
                    )
                else:
                    rationale = (
                        "task not completed: cancellation reply not posted to "
                        f"group conversation {group_conversation_id}"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)
            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
