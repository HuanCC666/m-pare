"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, Event, EventRegisterer, EventType

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from are.simulation.apps.calendar import CalendarEvent
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
SCENARIO_ASSET_DIR = Path(__file__).resolve().parent.parent / "multimodal_benchmark" / "assets" / "image_assets" / "bike_drivetrain_photo_share"


@register_scenario("bike_drivetrain_photo_share")
class BikeDrivetrainPhotoShare(PAREScenario):
    """Agent sends the user's own drivetrain photo to a bike shop ahead of a tune-up, after a calendar reminder and the shop's photo request.

A calendar reminder fires for the user's "Bike tune-up" appointment tomorrow at 9:00am at GearHub Cycles. Soon after, a message arrives from Marco at GearHub Cycles: "See you tomorrow at 9 for the tune-up. Can you send me a clear photo of your drivetrain (chain and cassette) so I pull the right replacement chain?" The user keeps home-inventory photos of their bike in the sandbox file system. The assistant must:
1. Read tomorrow's calendar events to confirm the tune-up appointment (time, shop, and context).
2. List the sandbox file system to locate a bike drivetrain photo, then display/inspect the image to confirm it shows the chain and cassette — the visual grounding for which photo to send.
3. Proactively propose sending the drivetrain photo to Marco via Messages.
4. After user acceptance, send the message with the photo attached.

The drivetrain photo is seeded in SandboxLocalFileSystem (e.g., `/bike_drivetrain.jpg`) and is the user's own file; the agent accesses it by listing files and inspecting the image. Actionable specifics — the 9:00am appointment, GearHub Cycles, and Marco's request for a drivetrain photo — come from the calendar reminder and the incoming message, while the image supplies the visual content (what the bike's drivetrain actually looks like) that cannot be known from the filename alone. The trigger explicitly asks the user to "send me a clear photo," motivating the file lookup and the outbound message with attachment.

This scenario exercises calendar read (reminder-triggered event lookup), multimodal inspection of a locally seeded file image, and outbound Messaging with an image attachment — a share/reply coordination flow rather than a calendar mutation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging and Calendar apps drive the tune-up coordination flow.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.calendar = StatefulCalendarApp(name="Calendar")

        # Load the user's home-inventory bike drivetrain photo into the sandbox file system.
        local_image_path = SCENARIO_ASSET_DIR / "bike_drivetrain.jpg"
        if not local_image_path.exists():
            raise FileNotFoundError(
                f"Bike drivetrain photo not found: {local_image_path}. "
                f"Place bike_drivetrain.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open("/bike_drivetrain.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_image_path.read_bytes()))

        # Seed Marco (GearHub Cycles mechanic) as a messaging contact and pre-seed a
        # brief prior conversation so the agent has an existing thread to reply on with
        # the photo. The triggering photo-request message from Marco arrives in Step 3.
        self.messaging.add_users(["Marco"])
        marco_id = self.messaging.name_to_id["Marco"]
        user_id = self.messaging.current_user_id
        prior_msg_time = datetime(2025, 11, 15, 14, 30, 0, tzinfo=UTC).timestamp()
        prior_message = MessageV2(
            sender_id=marco_id,
            content="Great — your tune-up is booked for Nov 19 at 9:00am at GearHub Cycles. See you then!",
            timestamp=prior_msg_time,
        )
        marco_conversation = ConversationV2(
            participant_ids=[user_id, marco_id],
            title="Marco",
            messages=[prior_message],
            last_updated=prior_msg_time,
        )
        self.messaging.add_conversation(marco_conversation)
        self.marco_conversation_id = marco_conversation.conversation_id
        self.marco_user_id = marco_id

        # Seed the user's calendar with the "Bike tune-up" appointment tomorrow at 9:00am
        # at GearHub Cycles (start_time is Nov 18 09:00 UTC; appointment is Nov 19 09:00 UTC).
        tune_up_start = datetime(2025, 11, 19, 9, 0, 0, tzinfo=UTC).timestamp()
        tune_up_end = datetime(2025, 11, 19, 10, 0, 0, tzinfo=UTC).timestamp()
        tune_up_event = CalendarEvent(
            title="Bike tune-up",
            start_datetime=tune_up_start,
            end_datetime=tune_up_end,
            tag="Personal",
            description="Bike tune-up at GearHub Cycles with Marco. Drop the bike off in the morning.",
            location="GearHub Cycles",
            attendees=["Marco"],
        )
        self.calendar.set_calendar_event(tune_up_event)
        self.tune_up_event_id = tune_up_event.event_id

        # TODO: Register all apps here in self.apps
        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.calendar]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        # Plain IDs resolved outside capture_mode so we never pass Event objects into app calls.
        marco_id = self.marco_user_id
        marco_conversation_id = self.marco_conversation_id
        drivetrain_photo_path = "/bike_drivetrain.jpg"
        # Tomorrow (Nov 19 2025) window — matches the seeded "Bike tune-up" appointment.
        tomorrow_start = "2025-11-19 00:00:00"
        tomorrow_end = "2025-11-19 23:59:59"

        with EventRegisterer.capture_mode():
            # --- Environment trigger: Marco (GearHub Cycles mechanic) sends a
            # photo request via Messages. This is the exogenous cue that motivates
            # the whole flow and has a notification template for both user/agent streams.
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=marco_conversation_id,
                sender_id=marco_id,
                content=(
                    "See you tomorrow at 9 for the tune-up. Can you send me a clear "
                    "photo of your drivetrain (chain and cassette) so I pull the right "
                    "replacement chain? Thanks!"
                ),
            ).delayed(3)

            # --- Oracle: read the Marco thread to observe the photo-request cue,
            # motivated by the incoming_message_event notification ("New message from Marco").
            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=marco_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            # --- Oracle: confirm tomorrow's tune-up appointment (time, shop, attendee),
            # motivated by the message content "tomorrow at 9 for the tune-up".
            read_tomorrow_calendar_event = (
                calendar_app.get_calendar_events_from_to(
                    start_datetime=tomorrow_start, end_datetime=tomorrow_end, offset=0, limit=10
                )
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            # --- Oracle: list sandbox files to locate a bike drivetrain photo,
            # motivated by the message asking the user to "send me a clear photo of your drivetrain".
            list_files_event = (
                files.ls(path="/", detail=True)
                .oracle()
                .depends_on(read_tomorrow_calendar_event, delay_seconds=2)
            )

            # --- Oracle: display/inspect the located image to confirm it shows the
            # chain and cassette — visual grounding for which photo to send.
            view_drivetrain_photo_event = (
                files.display(path=drivetrain_photo_path)
                .oracle()
                .depends_on(list_files_event, delay_seconds=2)
            )

            # --- Oracle: propose sending the inspected drivetrain photo to Marco.
            # Grounded in incoming_message_event ("send me a clear photo of your
            # drivetrain (chain and cassette)") plus view_drivetrain_photo_event
            # (image shows the chain and cassette) and read_tomorrow_calendar_event
            # (tomorrow 9am tune-up at GearHub Cycles with Marco).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Marco messaged asking for a clear drivetrain photo ahead of "
                        "tomorrow's 9am tune-up at GearHub Cycles. I found "
                        "/bike_drivetrain.jpg in your Files and inspected it — it shows "
                        "the chain and cassette clearly. Want me to send it to Marco?"
                    )
                )
                .oracle()
                .depends_on(view_drivetrain_photo_event, delay_seconds=2)
            )

            # --- User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(content="Yes, please send Marco the drivetrain photo.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- Oracle (WRITE, user-gated): send the drivetrain photo to Marco via Messages.
            # Depends on acceptance_event per the user-gated write rule.
            send_drivetrain_photo_event = (
                messaging_app.send_message(
                    user_id=marco_id,
                    content="Here's the drivetrain photo you asked for — chain and cassette. See you at 9!",
                    attachment_path=drivetrain_photo_path,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

        # Register ALL events so they execute.
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            read_tomorrow_calendar_event,
            list_files_event,
            view_drivetrain_photo_event,
            proposal_event,
            acceptance_event,
            send_drivetrain_photo_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            marco_id = self.marco_user_id
            drivetrain_photo_path = "/bike_drivetrain.jpg"

            # Check 1 — Proposal: agent offered proactive help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). Only AGENT events count.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # Check 2 — Task: agent sent the drivetrain photo to Marco via Messages with
            # the correct recipient and the bike_drivetrain.jpg attachment. This is the
            # user-visible side effect that defines success in the narrative.
            task_completed = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message"
                and e.action.args.get("user_id") == marco_id
                and e.action.args.get("attachment_path") == drivetrain_photo_path
                for e in log_entries
            )

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                else:
                    rationale = (
                        "task not completed: drivetrain photo not sent to Marco via "
                        "Messages with attachment /bike_drivetrain.jpg"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)
            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
