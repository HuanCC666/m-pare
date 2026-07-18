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
import os

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
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"
DEFAULT_LOCAL_TRAILHEAD_PHOTO_PATH = (
    Path(__file__).resolve().parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "hike_trailhead_change_add_rider"
    / "pine_hollow_trailhead.jpg"
)


@register_scenario("hike_trailhead_change_add_rider")
class HikeTrailheadChangeAddRider(PAREScenario):
    """Agent reschedules a Saturday hike's trailhead and start time, and adds a new rider to the group chat, after a friend's message and attached trailhead photo.

In the existing "Saturday Hike Crew" group chat (with Marco and Priya), Marco posts: Cedar Ridge trailhead lot is closed for repaving, so let's meet at the north entrance instead — he attaches a photo of the meeting spot (a trailhead with a large carved wooden bear statue and a "Pine Hollow" entrance sign). He also asks the user to push the start from 8:00am to 8:30am so his friend Dana can make it, and to please add Dana to this chat so she can see the carpool plan. The user already has a "Saturday Hike — Cedar Ridge Loop" calendar event for this Saturday at 8:00am. The assistant must:
1. Read the group chat message and inspect the attached trailhead photo (downloaded via the sandbox file system).
2. Visually identify the new meeting spot as the Pine Hollow trailhead (bear statue + sign) so the calendar location and group reply can name it accurately — the message only says "the north entrance" and never names Pine Hollow.
3. Look up Dana in messaging contacts, then propose adding her to the group chat, updating the calendar event's location to "Pine Hollow Trailhead (north entrance, bear statue)" and start time to 8:30am, and replying in the group chat to confirm the new plan.
4. After user acceptance, add Dana to the group conversation, edit the calendar event's location and start time, and send the confirmation reply to the group chat.

The trailhead photo is seeded in SandboxLocalFileSystem (e.g., `/pine_hollow_trailhead.jpg`) and attached to Marco's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the closed Cedar Ridge lot, the 8:30am start change, the explicit ask to "add Dana to this chat," and the request to meet at the trailhead in the photo — come from the incoming message, while the image supplies the visual content (Pine Hollow trailhead with the carved bear statue) that the calendar location edit and reply both depend on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + add-participant + reply with an image attachment, multimodal identification of an outdoor meeting spot from a photo, and calendar search + event time-and-location edit — a shared-activity rescheduling plus group-roster update flow rather than a new-event creation, a location-only edit, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging: pre-existing "Saturday Hike Crew" group chat (user, Marco, Priya) -
        # Dana is seeded as a contact but NOT yet a participant in this conversation; the
        # agent must look her up and (after user acceptance) add her in Step 3. Marco's
        # runtime message (Step 3) with the trailhead photo + 8:30am ask + "add Dana" is
        # injected into this same conversation, so the agent reads an existing group
        # thread rather than a fresh one.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.messaging.add_users(["Marco Reyes", "Priya Patel", "Dana Whitfield"])
        marco_id = self.messaging.get_user_id("Marco Reyes")
        priya_id = self.messaging.get_user_id("Priya Patel")
        dana_id = self.messaging.get_user_id("Dana Whitfield")
        if marco_id is None or priya_id is None or dana_id is None:
            raise RuntimeError(
                "Failed to resolve seeded messaging user ids for Marco/Priya/Dana"
            )
        self.marco_user_id = marco_id
        self.priya_user_id = priya_id
        self.dana_user_id = dana_id
        self.current_user_id = self.messaging.current_user_id

        hike_chat = ConversationV2(
            participant_ids=[self.current_user_id, marco_id, priya_id],
            title="Saturday Hike Crew",
        )
        # Baseline prior group-chat history (before start_time) establishing the original
        # 8:00am Cedar Ridge trailhead lot plan that the runtime message will revise.
        prior_ts = self.start_time - 2 * 86_400  # two days before start_time (Sunday)
        hike_chat.messages.append(
            MessageV2(
                sender_id=marco_id,
                content=(
                    "Are we still on for the Cedar Ridge Loop this Saturday? Thinking "
                    "8:00am at the Cedar Ridge trailhead lot — we can carpool from there."
                ),
                timestamp=prior_ts,
            )
        )
        hike_chat.messages.append(
            MessageV2(
                sender_id=priya_id,
                content="8:00am works for me. I can drive if we end up needing one car.",
                timestamp=prior_ts + 900,
            )
        )
        hike_chat.messages.append(
            MessageV2(
                sender_id=self.current_user_id,
                content="Sounds good — 8:00am at the Cedar Ridge trailhead lot. See you both Saturday.",
                timestamp=prior_ts + 1800,
            )
        )
        hike_chat.update_last_updated(prior_ts + 1800)
        self.messaging.add_conversation(hike_chat)
        self.hike_conversation_id = hike_chat.conversation_id

        # --- Calendar: pre-existing "Saturday Hike — Cedar Ridge Loop" event -----------
        # This is the event whose location + start time the agent must edit in Step 3
        # (location -> Pine Hollow Trailhead (north entrance, bear statue),
        # start 8:00am -> 8:30am). start_time is Tuesday 2025-11-18; "this Saturday" is
        # 2025-11-22.
        self.calendar = StatefulCalendarApp(name="Calendar")
        self.hike_event_id = self.calendar.add_calendar_event(
            title="Saturday Hike — Cedar Ridge Loop",
            start_datetime="2025-11-22 08:00:00",
            end_datetime="2025-11-22 11:00:00",
            tag="personal",
            description="Group hike with Marco and Priya. Meet at Cedar Ridge trailhead lot and carpool to the loop trailhead.",
            location="Cedar Ridge Trailhead lot",
            attendees=["Marco Reyes", "Priya Patel"],
        )

        # --- Visual asset: load pine_hollow_trailhead.jpg into sandbox Files -----------
        # The trailhead photo is attached to Marco's runtime group-chat message (Step 3)
        # and inspected by the agent via Files.display(...). The image supplies the visual
        # content (carved wooden bear statue + "PINE HOLLOW" entrance sign) that the
        # calendar location edit and group reply both depend on; the filename and message
        # text ("the north entrance") do not name Pine Hollow.
        local_photo_path = Path(
            os.getenv(
                "PARE_HIKE_TRAILHEAD_PHOTO_LOCAL_PATH",
                str(DEFAULT_LOCAL_TRAILHEAD_PHOTO_PATH),
            )
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Pine Hollow trailhead photo not found: {local_photo_path}. "
                f"Place pine_hollow_trailhead.jpg under {DEFAULT_LOCAL_TRAILHEAD_PHOTO_PATH.parent}."
            )
        with self.files.open("/pine_hollow_trailhead.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))
        self.trailhead_photo_sandbox_path = "/pine_hollow_trailhead.jpg"

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

        # Plain values (computed outside capture_mode so we never pass Event objects into tool calls).
        hike_conversation_id = self.hike_conversation_id
        marco_user_id = self.marco_user_id
        dana_user_id = self.dana_user_id
        hike_event_id = self.hike_event_id
        trailhead_photo_path = self.trailhead_photo_sandbox_path

        marco_message = (
            "Heads up for Saturday — the Cedar Ridge trailhead lot is closed for repaving, so let's "
            "meet at the north entrance instead. I've attached a photo of the meeting spot so we all "
            "know where to gather. Also, can we push the start from 8:00am to 8:30am so my friend Dana "
            "can make it? Please add Dana to this chat so she can see the carpool plan. Thanks!"
        )

        with EventRegisterer.capture_mode():
            # --- ENVIRONMENT EVENT: Marco posts the trailhead photo + 8:30am ask + "add Dana"
            # request in the existing "Saturday Hike Crew" group chat. Has a notification template
            # entry for StatefulMessagingApp.create_and_add_message in both user/agent streams.
            marco_trailhead_message_event = messaging_app.create_and_add_message(
                conversation_id=hike_conversation_id,
                sender_id=marco_user_id,
                content=marco_message,
                attachment_path=trailhead_photo_path,
            ).delayed(5)

            # --- ORACLE READ: Agent reads the newly-active "Saturday Hike Crew" group chat to
            # consume Marco's incoming request (motivated by the create_and_add_message
            # notification citing "meet at the north entrance", "push the start ... to 8:30am",
            # and "add Dana to this chat").
            read_group_event = (
                messaging_app.read_conversation(
                    conversation_id=hike_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(marco_trailhead_message_event, delay_seconds=2)
            )

            # --- ORACLE READ (visual inspection): Agent displays the attached trailhead photo
            # via the sandbox file system to visually identify the new meeting spot (Pine Hollow
            # trailhead with the carved wooden bear statue + "PINE HOLLOW" entrance sign), since
            # Marco's message only says "the north entrance" and never names Pine Hollow.
            view_trailhead_photo_event = (
                files.display(path=trailhead_photo_path)
                .oracle()
                .depends_on(read_group_event, delay_seconds=2)
            )

            # --- ORACLE READ: Agent searches the calendar for the Saturday hike event to find
            # the specific event_id whose location + start time should be edited (motivated by
            # Marco's message asking to "push the start from 8:00am to 8:30am" — the seeded
            # event is titled "Saturday Hike — Cedar Ridge Loop").
            search_hike_event = (
                calendar_app.search_events(query="Saturday Hike")
                .oracle()
                .depends_on(view_trailhead_photo_event, delay_seconds=2)
            )

            # --- ORACLE READ: Agent looks up "Dana Whitfield" in Messaging contacts to resolve
            # the user id needed to add her to the group (motivated by Marco's explicit request
            # "add Dana to this chat so she can see the carpool plan").
            lookup_dana_event = (
                messaging_app.lookup_user_id(user_name="Dana Whitfield")
                .oracle()
                .depends_on(search_hike_event, delay_seconds=2)
            )

            # --- ORACLE PROPOSAL: Agent proposes adding Dana to the group chat, updating the
            # calendar event's location to the visually-identified Pine Hollow trailhead and
            # start time to 8:30am, and replying in the group chat to confirm. Grounded in
            # marco_trailhead_message_event ("Cedar Ridge trailhead lot is closed for repaving",
            # "meet at the north entrance", "push the start from 8:00am to 8:30am",
            # "add Dana to this chat") and view_trailhead_photo_event (Pine Hollow trailhead
            # with carved bear statue + "PINE HOLLOW" entrance sign).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Marco just posted in the Saturday Hike Crew chat: the Cedar Ridge trailhead lot "
                        "is closed for repaving, so he wants to meet at the north entrance instead, push "
                        "the start from 8:00am to 8:30am so his friend Dana can make it, and have me add "
                        "Dana to this chat so she can see the carpool plan. I opened the attached photo — "
                        "it's the Pine Hollow trailhead (the carved wooden bear statue and \"PINE HOLLOW\" "
                        "entrance sign). Want me to add Dana Whitfield to this group chat, update your "
                        "\"Saturday Hike — Cedar Ridge Loop\" calendar event's location to "
                        "\"Pine Hollow Trailhead (north entrance, bear statue)\" and start time to "
                        "8:30am, and reply in the group chat to confirm the new plan?"
                    )
                )
                .oracle()
                .depends_on(lookup_dana_event, delay_seconds=2)
            )

            # --- USER ACCEPTANCE: User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes, please add Dana to the chat, update the calendar event's location to "
                        "Pine Hollow Trailhead and start time to 8:30am, and reply in the group chat."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- ORACLE WRITE (user-gated): Agent adds Dana Whitfield to the existing
            # "Saturday Hike Crew" group conversation. Depends on acceptance; dana_user_id was
            # resolved by lookup_dana_event.
            add_dana_event = (
                messaging_app.add_participant_to_conversation(
                    conversation_id=hike_conversation_id,
                    user_id=dana_user_id,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- ORACLE WRITE (user-gated): Agent edits the "Saturday Hike — Cedar Ridge Loop"
            # calendar event's location and start time. Depends on acceptance; hike_event_id was
            # revealed by search_hike_event; the Pine Hollow location comes from the inspected
            # photo and the 8:30am start from Marco's message.
            edit_hike_event = (
                calendar_app.edit_calendar_event(
                    event_id=hike_event_id,
                    location="Pine Hollow Trailhead (north entrance, bear statue)",
                    start_datetime="2025-11-22 08:30:00",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- ORACLE WRITE (user-gated): Agent replies in the "Saturday Hike Crew" group chat
            # to confirm the new trailhead, start time, and Dana's addition. Depends on both
            # writes so Dana (now in the chat) and the updated calendar state are in sync.
            reply_group_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=hike_conversation_id,
                    content=(
                        "Sounds good Marco — I've added Dana to the chat and updated the calendar event: "
                        "we'll meet at 8:30am at the Pine Hollow Trailhead (north entrance, by the carved "
                        "bear statue) instead of the Cedar Ridge lot. See you all Saturday!"
                    ),
                )
                .oracle()
                .depends_on([add_dana_event, edit_hike_event], delay_seconds=1)
            )

        self.events: list[Event] = [
            marco_trailhead_message_event,
            read_group_event,
            view_trailhead_photo_event,
            search_hike_event,
            lookup_dana_event,
            proposal_event,
            acceptance_event,
            add_dana_event,
            edit_hike_event,
            reply_group_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            # Structural identifiers the task writes must target.
            expected_hike_conversation_id = self.hike_conversation_id
            expected_hike_event_id = self.hike_event_id
            expected_dana_user_id = self.dana_user_id
            expected_start_datetime = "2025-11-22 08:30:00"

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
            # The promised task has three coordinated, user-gated writes that all
            # must succeed (folded into a single boolean):
            #   (a) messaging: add_participant_to_conversation targeting the seeded
            #       "Saturday Hike Crew" group conversation_id with Dana's user_id.
            #   (b) calendar: edit_calendar_event targeting the seeded Saturday
            #       hike event_id with a non-empty location and the 8:30am start
            #       time on 2025-11-22 (start_datetime text is structural here
            #       because it is the specific time change the narrative requires).
            #   (c) messaging: send_message_to_group_conversation targeting the
            #       seeded "Saturday Hike Crew" group conversation_id (reply body
            #       text is free-form and not asserted).
            dana_added = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "add_participant_to_conversation"
                and e.action.args.get("conversation_id") == expected_hike_conversation_id
                and e.action.args.get("user_id") == expected_dana_user_id
                for e in log_entries
            )

            calendar_edited = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "edit_calendar_event"
                and e.action.args.get("event_id") == expected_hike_event_id
                and bool(e.action.args.get("location"))
                and e.action.args.get("start_datetime") == expected_start_datetime
                for e in log_entries
            )

            group_reply_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == expected_hike_conversation_id
                for e in log_entries
            )

            task_completed = dana_added and calendar_edited and group_reply_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not dana_added:
                rationale = (
                    "task not completed: Dana was not added to the seeded "
                    "Saturday Hike Crew group conversation"
                )
            elif not calendar_edited:
                rationale = (
                    "task not completed: calendar event not edited to the seeded "
                    "hike event with a non-empty location and 8:30am start on "
                    "2025-11-22"
                )
            else:
                rationale = (
                    "task not completed: confirmation reply not sent to the seeded "
                    "Saturday Hike Crew group conversation"
                )
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
