"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
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
    Path(__file__).parent
    / ".."
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "bookclub_cafe_photo_relocate"
)


@register_scenario("bookclub_cafe_photo_relocate")
class BookclubCafePhotoRelocate(PAREScenario):
    """Agent relocates an existing book-club calendar event and drops the canceling host from the RSVP after a group-chat message with a cafe storefront photo.

In the existing "Saturday Book Club" group chat (Priya, Marco, Dana, and the user), Priya posts: her kitchen flooded and she can't host this Saturday's 4pm meeting; she's at a cafe now that agreed to host the group Saturday at 4pm — "the one in the photo" — and asks the user to update the calendar to that cafe, drop her from the RSVP since she'll be with the plumber all afternoon, and confirm in the chat once it's done. She attaches a storefront photo of the cafe; the cafe's name (e.g., "The Reading Room" on its awning) is only visible in the image — the message never names it. The user already has a "Saturday Book Club" calendar event tagged "bookclub" for this Saturday at 4pm at Priya's place, with Priya, Marco, and Dana as attendees. The assistant must:
1. Read the group-chat message and download/inspect the attached cafe storefront photo via the sandbox file system to identify the cafe by its awning sign.
2. Find the existing Saturday book-club event in the Calendar (via the "bookclub" tag or a "Book Club" search) and propose updating its location to the visually-identified cafe and removing Priya from the attendees, plus replying in the group chat to confirm.
3. After user acceptance, edit the calendar event's location and attendee list (Priya removed), and send the confirmation reply to the group chat.

The cafe storefront photo is seeded in SandboxLocalFileSystem (e.g., `/reading_room_storefront.jpg`) and attached to Priya's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Saturday 4pm meeting, the explicit asks to update the calendar, drop Priya from the RSVP, and confirm in chat, plus the cafe being "the one in the photo" — come from the incoming message, while the image supplies the visual content (the cafe's name on its awning) that the calendar location and the reply both depend on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + attachment download + outbound group reply, multimodal identification of a real-world storefront from a photo, and calendar tag-filter/search + existing-event location-and-attendee edit (removing a canceling host) — a relocate-and-update-RSVP coordination flow rather than a new-event creation, a new-group creation, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # TODO: Initialize scenario specific apps here.
        # Visual assets should be loaded from the asset manifest / resolved asset directory,
        # written into self.files with jpeg_bytes_for_sandbox(...), and then attached
        # through Email, Album, Notes, or Files according to the VisualAssetSpec.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.calendar = StatefulCalendarApp(name="Calendar")

        # Load the cafe storefront photo (asset_id: reading_room_storefront) into the
        # sandbox file system so Step 3 can attach it to Priya's incoming group-chat
        # message; the agent downloads it from there and views it to read the awning
        # sign ("The Reading Room") that names the new venue.
        storefront_path = Path(
            os.getenv(
                "PARE_READING_ROOM_STOREFRONT_PATH",
                str(SCENARIO_ASSET_DIR / "reading_room_storefront.jpg"),
            )
        )
        if not storefront_path.exists():
            raise FileNotFoundError(
                f"Reading Room storefront photo not found: {storefront_path}. "
                f"Place reading_room_storefront.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open("/reading_room_storefront.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(storefront_path.read_bytes()))

        # TODO: Populate apps with scenario specific data here.
        # Messaging participants: the simulated user plus three book-club friends.
        self.messaging.add_users(["Priya Sharma", "Marco Rossi", "Dana Nguyen"])
        priya_id = self.messaging.get_user_id("Priya Sharma")
        marco_id = self.messaging.get_user_id("Marco Rossi")
        dana_id = self.messaging.get_user_id("Dana Nguyen")
        user_id = self.messaging.current_user_id

        # Existing "Saturday Book Club" group chat (user + Priya, Marco, Dana) with a
        # short baseline history from before start_time. Priya's flood/cafe message is
        # a runtime event and is delivered in Step 3, not seeded here.
        self.bookclub_conversation_id = "bookclub_group_chat"
        bookclub_conversation = ConversationV2(
            conversation_id=self.bookclub_conversation_id,
            participant_ids=[user_id, priya_id, marco_id, dana_id],
            title="Saturday Book Club",
        )
        sunday_ts = datetime(2025, 11, 16, 20, 30, 0, tzinfo=UTC).timestamp()
        bookclub_conversation.messages.append(
            MessageV2(
                sender_id=marco_id,
                content="Looking forward to Saturday — I'm halfway through 'The Overstory'.",
                timestamp=sunday_ts,
            )
        )
        dana_ts = sunday_ts + 600
        bookclub_conversation.messages.append(
            MessageV2(
                sender_id=dana_id,
                content="Same! See you all at Priya's at 4pm on Saturday.",
                timestamp=dana_ts,
            )
        )
        bookclub_conversation.update_last_updated(dana_ts)
        self.messaging.add_conversation(bookclub_conversation)

        # Existing "Saturday Book Club" calendar event at Priya's place, tagged
        # "bookclub" so the agent can find it via tag-filter or a "Book Club" search.
        # This Saturday = 2025-11-22 (start_time 2025-11-18 is a Tuesday). 4pm UTC.
        self.bookclub_event_id = self.calendar.add_calendar_event(
            title="Saturday Book Club",
            start_datetime="2025-11-22 16:00:00",
            end_datetime="2025-11-22 17:30:00",
            tag="bookclub",
            description="Monthly book club meetup. Discussing 'The Overstory'.",
            location="Priya's place",
            attendees=["Priya Sharma", "Marco Rossi", "Dana Nguyen"],
        )

        # TODO: Register all apps here in self.apps
        self.apps = [
            self.agent_ui,
            self.system_app,
            self.files,
            self.messaging,
            self.calendar,
        ]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        # Precompute plain sender id outside capture_mode so it is not mistaken for an
        # Event object when passed into environment event args.
        priya_id = messaging_app.name_to_id["Priya Sharma"]

        with EventRegisterer.capture_mode():
            # ENV: Priya posts in the existing "Saturday Book Club" group chat. Her
            # kitchen flooded, she can't host this Saturday's 4pm meeting, she's at a
            # cafe now that agreed to host the group Saturday at 4pm ("the one in the
            # photo"), and she asks the user to update the calendar to that cafe, drop
            # her from the RSVP since she'll be with the plumber all afternoon, and
            # confirm back in the chat. The cafe's name ("The Reading Room") is only
            # visible in the attached storefront photo — never spelled out in the text.
            priya_message_event = messaging_app.create_and_add_message(
                conversation_id=self.bookclub_conversation_id,
                sender_id=priya_id,
                content=(
                    "Hey everyone — my kitchen flooded this morning, I can't host this Saturday's 4pm book "
                    "club. I'm at a cafe right now that agreed to host us Saturday at 4pm; it's the one in "
                    "the photo. Could you update the calendar to that cafe, drop me from the RSVP since I'll "
                    "be with the plumber all afternoon, and confirm back here once it's done? Thanks!"
                ),
                attachment_path="/reading_room_storefront.jpg",
            ).delayed(5)

            # ORACLE: agent reads the Saturday Book Club group chat, motivated by Priya's
            # incoming env message ("update the calendar to that cafe, drop me from the
            # RSVP ... confirm back here once it's done").
            read_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=self.bookclub_conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(priya_message_event, delay_seconds=2)
            )

            # ORACLE: agent inspects the attached storefront photo via the sandbox file
            # system to read the cafe name off the awning ("The Reading Room") — the
            # name is only visible in the image, not in the message text or filename.
            view_storefront_event = (
                files.display(path="/reading_room_storefront.jpg")
                .oracle()
                .depends_on(read_chat_event, delay_seconds=1)
            )

            # ORACLE: agent looks up the existing "bookclub"-tagged Saturday 4pm event,
            # motivated by Priya's env ask to "update the calendar to that cafe, drop me
            # from the RSVP" — the agent needs the event id before it can edit the event.
            find_bookclub_event = (
                calendar_app.get_calendar_events_by_tag(tag="bookclub")
                .oracle()
                .depends_on(view_storefront_event, delay_seconds=1)
            )

            # ORACLE proposal: agent proposes relocating the Saturday Book Club event to
            # "The Reading Room" (read off the awning in the inspected photo) and
            # removing Priya from the attendees, then replying in the group chat.
            # Grounded in priya_message_event ("update the calendar to that cafe, drop
            # me from the RSVP ... confirm back here") and view_storefront_event (cafe
            # name "The Reading Room" read from the awning).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Priya's message in the Saturday Book Club chat says her kitchen flooded and she "
                        "can't host this Saturday's 4pm meetup; she asked me to update the calendar to the "
                        "cafe in her photo, drop her from the RSVP, and confirm back in the chat. I "
                        "inspected the attached storefront photo — the awning reads 'The Reading Room', so "
                        "that's the new venue. I also found the existing 'Saturday Book Club' event "
                        "(tag: bookclub) for this Saturday at 4pm at Priya's place, with Priya, Marco, and "
                        "Dana as attendees.\n\n"
                        "Plan: edit that calendar event's location to 'The Reading Room' and remove Priya "
                        "from the attendees, then reply in the Saturday Book Club chat to confirm. Want me "
                        "to go ahead?"
                    )
                )
                .oracle()
                .depends_on([view_storefront_event, find_bookclub_event], delay_seconds=1)
            )

            # USER accepts the agent's proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please update the calendar and reply in the chat."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # ORACLE: agent edits the existing Saturday Book Club calendar event —
            # relocate to "The Reading Room" (from the inspected photo) and remove Priya
            # from the attendees. User-gated write; depends on the acceptance event.
            edit_calendar_event = (
                calendar_app.edit_calendar_event(
                    event_id=self.bookclub_event_id,
                    location="The Reading Room",
                    attendees=["Marco Rossi", "Dana Nguyen"],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # ORACLE: agent replies in the Saturday Book Club group chat to confirm the
            # calendar was updated and Priya was dropped from the RSVP, as Priya's env
            # message requested ("confirm back here once it's done"). User-gated write;
            # depends on the acceptance event.
            confirm_chat_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=self.bookclub_conversation_id,
                    content=(
                        "Done — I updated the Saturday Book Club calendar event: location is now "
                        "The Reading Room (the cafe from Priya's photo) for this Saturday at 4pm, and "
                        "I removed Priya from the RSVP. See you all there!"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            priya_message_event,
            read_chat_event,
            view_storefront_event,
            find_bookclub_event,
            proposal_event,
            acceptance_event,
            edit_calendar_event,
            confirm_chat_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            # Local imports keep this section self-contained without touching the
            # WARNING-guarded import block at the top of the file.
            from are.simulation.types import Action, EventType

            log_entries = env.event_log.list_view()

            expected_event_id = self.bookclub_event_id
            expected_conversation_id = self.bookclub_conversation_id
            dropped_host = "Priya Sharma"

            # --- Check 1: Proposal ---------------------------------------------
            # Agent offered proactive help to the user via the PARE agent UI.
            # Assert on class/function only; proposal body text is free-form.
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
            #       "Saturday Book Club" event_id, with a non-empty location and
            #       an attendee list that drops the canceling host (Priya Sharma).
            #   (b) messaging: send_message_to_group_conversation targeting the
            #       seeded "Saturday Book Club" group conversation_id.
            calendar_edited = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "edit_calendar_event"
                and e.action.args.get("event_id") == expected_event_id
                and bool(e.action.args.get("location"))
                and dropped_host not in (e.action.args.get("attendees") or [])
                for e in log_entries
            )

            group_reply_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == expected_conversation_id
                for e in log_entries
            )

            task_completed = calendar_edited and group_reply_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not calendar_edited:
                rationale = (
                    "task not completed: calendar event not edited to the seeded "
                    "Saturday Book Club event with a non-empty location and Priya "
                    "Sharma removed from the attendees"
                )
            else:
                rationale = (
                    "task not completed: confirmation reply not sent to the seeded "
                    "Saturday Book Club group conversation"
                )
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
