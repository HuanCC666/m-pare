"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, Event, EventRegisterer, EventType

# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulCalendarApp,
    StatefulMessagingApp,
)
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario

# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "rainy_picnic_pavilion_calendar_edit"
)


@register_scenario("rainy_picnic_pavilion_calendar_edit")
class RainyPicnicPavilionCalendarEdit(PAREScenario):
    """Agent updates a shared picnic's calendar location after a group-chat message and an attached pavilion photo.

A friend posts in the "Riverside Park Picnic" group chat: rain is moving Saturday's noon picnic to one of the park pavilions, and they attach a photo of the pavilion they have in mind. The pavilion's identity (red-roofed, beside the pond) is only visible in the image — the message just says "one of the pavilions." The user already has a "Saturday Picnic at Riverside Park meadow" event on their calendar for that day. The assistant must:
1. Read the group-chat message and inspect the attached pavilion photo (viewed via the sandbox file system).
2. Visually identify the pavilion from the photo (red roof, by the pond) so the new location can be described accurately.
3. Search the calendar for the Saturday picnic event and propose updating its location to the visually-identified pavilion, plus replying in the group chat to ack.
4. After user acceptance, edit the calendar event's location and send the ack reply to the group chat.

The pavilion photo is seeded in SandboxLocalFileSystem (e.g., `/riverside_pavilion.jpg`) and attached to the incoming group-chat message; the agent accesses it by reading the conversation and viewing the image. Actionable specifics — the Saturday noon picnic, the request to "update the calendar invite" and "ack in the group chat" — come from the incoming message, while the image supplies the visual content (which pavilion: red roof by the pond) that the text does not name, and the calendar edit + reply both depend on that visually-grounded description.

This scenario exercises messaging group-chat read + reply with an image attachment, multimodal identification of an outdoor location from a photo, and calendar search + event-location edit — a shared-activity rescheduling flow rather than a new-event creation or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Visual asset: pavilion photo -------------------------------------
        # Load the pavilion photo from the resolved asset manifest directory and write
        # it into the sandbox filesystem so Step 3 can attach it to the incoming
        # group-chat message and the agent can view it via files.display(...).
        self.pavilion_sandbox_path = "/riverside_pavilion.jpg"
        pavilion_asset_path = SCENARIO_ASSET_DIR / "riverside_pavilion.jpg"
        if not pavilion_asset_path.exists():
            raise FileNotFoundError(
                f"Scenario image not found: {pavilion_asset_path}. Add riverside_pavilion.jpg "
                f"under {SCENARIO_ASSET_DIR}."
            )
        pavilion_bytes = jpeg_bytes_for_sandbox(pavilion_asset_path.read_bytes())
        with self.files.open(self.pavilion_sandbox_path, "wb") as f:
            f.write(pavilion_bytes)

        # --- Messaging: "Riverside Park Picnic" group chat --------------------
        # Pre-existing group conversation with baseline planning history. The
        # triggering rain/pavilion message + photo attachment is delivered as an
        # environment event in Step 3, not here.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.friend_names = ["Maya Chen", "Devon Park"]
        self.messaging.add_users(self.friend_names)
        self.poster_name = "Maya Chen"
        self.poster_id = self.messaging.get_user_id(self.poster_name)
        if self.poster_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {self.poster_name}")
        self.other_friend_id = self.messaging.get_user_id("Devon Park")
        if self.other_friend_id is None:
            raise RuntimeError("Failed to resolve messaging user id for Devon Park")

        group_conversation = ConversationV2(
            participant_ids=[
                self.messaging.current_user_id,
                self.poster_id,
                self.other_friend_id,
            ],
            title="Riverside Park Picnic",
        )
        # Baseline planning history (pre-existing, before start_time).
        last_week_ts = self.start_time - 6 * 86_400
        yesterday_ts = self.start_time - 86_400
        baseline_messages = [
            MessageV2(
                sender_id=self.poster_id,
                content="Who's in for a picnic at Riverside Park this Saturday around noon?",
                timestamp=last_week_ts,
            ),
            MessageV2(
                sender_id=self.other_friend_id,
                content="I'm in! The meadow by the south entrance has plenty of space.",
                timestamp=last_week_ts + 3600,
            ),
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="Sounds great, let's plan for noon on Saturday at the meadow.",
                timestamp=yesterday_ts,
            ),
        ]
        for msg in baseline_messages:
            group_conversation.messages.append(msg)
        group_conversation.update_last_updated(yesterday_ts)
        self.messaging.add_conversation(group_conversation)
        self.group_conversation_id = group_conversation.conversation_id

        # --- Calendar: pre-existing Saturday picnic event ---------------------
        # The user already has this event on their calendar; Step 3 will propose
        # editing its location to the visually-identified pavilion.
        self.calendar = StatefulCalendarApp(name="Calendar")
        self.picnic_event_id = self.calendar.add_calendar_event(
            title="Saturday Picnic at Riverside Park meadow",
            start_datetime="2025-11-22 12:00:00",
            end_datetime="2025-11-22 14:00:00",
            tag="Social",
            description="Saturday picnic with Maya and Devon at the Riverside Park meadow.",
            location="Riverside Park meadow",
            attendees=["Maya Chen", "Devon Park"],
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

        # Precomputed plain values (avoid passing Event objects into app methods).
        group_conversation_id = self.group_conversation_id
        poster_id = self.poster_id
        pavilion_sandbox_path = self.pavilion_sandbox_path
        picnic_event_id = self.picnic_event_id

        with EventRegisterer.capture_mode():
            # --- ENVIRONMENT: Maya posts the rain/pavilion update + photo in the
            # "Riverside Park Picnic" group chat. This is the exogenous trigger; the
            # pavilion's identity (red roof by the pond) is only in the attached image.
            incoming_pavilion_message_event = messaging_app.create_and_add_message(
                conversation_id=group_conversation_id,
                sender_id=poster_id,
                content=(
                    "Heads up — rain is moving into Saturday's noon picnic, so the meadow is off. "
                    "Let's relocate to one of the park pavilions; I've attached a photo of the one I have in mind. "
                    "Could you update the calendar invite with the new spot once we settle on it, and ack back here in the group chat?"
                ),
                attachment_path=pavilion_sandbox_path,
            ).delayed(5)

            # --- ORACLE READ: Agent reads the "Riverside Park Picnic" group chat to
            # observe Maya's message ("rain is moving ... update the calendar invite ...
            # ack back here in the group chat") and the attached pavilion photo.
            read_group_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=group_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_pavilion_message_event, delay_seconds=3)
            )

            # --- ORACLE READ (visual inspection): Agent displays the attached
            # pavilion photo via the sandbox file system to visually identify which
            # pavilion Maya means (dark-red roof beside a pond) before proposing a
            # location description.
            view_pavilion_photo_event = (
                files.display(path=pavilion_sandbox_path)
                .oracle()
                .depends_on(read_group_chat_event, delay_seconds=2)
            )

            # --- ORACLE READ: Agent searches the calendar for the Saturday picnic
            # event (Maya's message asked to "update the calendar invite"), to find
            # the specific event_id whose location should be updated.
            search_picnic_event = (
                calendar_app.search_events(query="Picnic")
                .oracle()
                .depends_on(view_pavilion_photo_event, delay_seconds=2)
            )

            # --- ORACLE PROPOSAL: Grounded by Maya's env message ("rain is moving ...
            # update the calendar invite ... ack back here in the group chat") and the
            # visually-identified pavilion (red roof by the pond), agent proposes
            # updating the Saturday picnic calendar event's location and replying in
            # the group chat.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya posted in the Riverside Park Picnic group chat that rain is moving Saturday's noon "
                        "picnic to a park pavilion, and asked me to update the calendar invite and ack the group. "
                        "I inspected the attached photo — it's the dark-red-roofed pavilion beside the pond. "
                        "Want me to update your \"Saturday Picnic at Riverside Park meadow\" calendar event's "
                        "location to that pavilion and reply to the group chat to confirm?"
                    )
                )
                .oracle()
                .depends_on(search_picnic_event, delay_seconds=2)
            )

            # --- USER ACCEPTANCE: User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please update the calendar location to that pavilion and ack the group chat."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- ORACLE WRITE (user-gated): Agent edits the Saturday picnic calendar
            # event's location to the visually-identified pavilion. Depends on
            # acceptance; event_id was revealed by the earlier search_events read.
            edit_calendar_location_event = (
                calendar_app.edit_calendar_event(
                    event_id=picnic_event_id,
                    location="Riverside Park — red-roofed pavilion by the pond",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # --- ORACLE WRITE (user-gated): Agent replies in the "Riverside Park
            # Picnic" group chat to ack the relocation, per Maya's request ("ack back
            # here in the group chat"). Depends on acceptance and the calendar edit.
            ack_group_chat_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=group_conversation_id,
                    content=(
                        "Acked — I've updated the calendar invite to the red-roofed pavilion by the pond for "
                        "Saturday noon. See you there!"
                    ),
                )
                .oracle()
                .depends_on(edit_calendar_location_event, delay_seconds=2)
            )

        self.events: list[Event] = [
            incoming_pavilion_message_event,
            read_group_chat_event,
            view_pavilion_photo_event,
            search_picnic_event,
            proposal_event,
            acceptance_event,
            edit_calendar_location_event,
            ack_group_chat_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            # Structural identifiers the task writes must target.
            expected_picnic_event_id = self.picnic_event_id
            expected_group_conversation_id = self.group_conversation_id

            # --- Check 1: Proposal ---------------------------------------------
            # Agent offered proactive help to the user via the PARE agent UI.
            # We assert on class/function only; proposal body text is free-form.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2: Task -------------------------------------------------
            # The promised task has two coordinated, user-gated writes that both
            # must succeed (folded into a single boolean):
            #   (a) calendar: edit_calendar_event targeting the seeded Saturday
            #       picnic event_id (location must be supplied, but its text is
            #       free-form and not asserted).
            #   (b) messaging: send_message_to_group_conversation targeting the
            #       seeded "Riverside Park Picnic" group conversation_id.
            calendar_edited = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "edit_calendar_event"
                and e.action.args.get("event_id") == expected_picnic_event_id
                and bool(e.action.args.get("location"))
                for e in log_entries
            )

            group_ack_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == expected_group_conversation_id
                for e in log_entries
            )

            task_completed = calendar_edited and group_ack_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not calendar_edited:
                rationale = (
                    "task not completed: calendar event not edited to the seeded "
                    "picnic event with a non-empty location"
                )
            else:
                rationale = (
                    "task not completed: ack reply not sent to the seeded "
                    "Riverside Park Picnic group conversation"
                )
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
