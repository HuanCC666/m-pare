"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

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
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "trifle_potluck_calendar_by_attendee"
)


@register_scenario("trifle_potluck_calendar_by_attendee")
class TriflePotluckCalendarByAttendee(PAREScenario):
    """Agent adds a potluck calendar event on behalf of the hosting friend and renames the group chat after identifying the dessert theme from a photo.

In the existing "Potluck Crew" group chat (Priya, Marco, Dana, and the user), Priya posts: she is hosting this Sunday's 6pm potluck at her place and the theme is the dessert in the attached photo. She asks the user to add the potluck to everyone's calendar on her behalf since she will be cooking all day, to rename the group chat to include the dessert theme so the crew remembers what to bring, and to reply in the chat once it is done. The dessert's identity (a layered trifle in a glass bowl — sponge, custard, and berries) is only visible in the photo — the message never names it. The assistant must:
1. Read the group-chat message and download/inspect the attached dessert photo via the sandbox file system.
2. Visually identify the dessert as a trifle so the calendar title and the renamed chat can name the theme accurately.
3. Proactively propose creating a Sunday 6pm "Potluck — Trifle Night" calendar event on Priya's behalf (with Priya, Marco, and Dana as attendees, at Priya's place), renaming the group chat to "Potluck Crew — Trifle Night", and replying in the chat to confirm.
4. After user acceptance, create the event via add_calendar_event_by_attendee on Priya's behalf, rename the conversation, and send the confirmation reply to the group chat.

The trifle photo is seeded in SandboxLocalFileSystem (e.g., `/trifle_dessert.jpg`) and attached to Priya's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Sunday 6pm time, Priya's place as the venue, and the explicit asks to add the event "on my behalf," rename the chat to the theme, and reply — come from the incoming message, while the image supplies the visual content (the layered trifle) that the calendar title and chat rename depend on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + attachment download + conversation-title rename + outbound group reply, multimodal identification of a real-world dessert from a photo, and calendar event creation on behalf of a specific attendee (add_calendar_event_by_attendee) — a host-delegated coordination flow rather than a self-authored event creation, an existing-event edit, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging: seed the existing "Potluck Crew" group chat with Priya, Marco,
        # Dana, and the user. Priya's triggering dessert-photo message is delivered
        # as an environment event in Step 3; here we only seed baseline history so
        # the group conversation already exists before that message lands.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.messaging.add_users(["Priya Shah", "Marco Rivera", "Dana Chen"])
        self.priya_id = self.messaging.get_user_id("Priya Shah")
        self.marco_id = self.messaging.get_user_id("Marco Rivera")
        self.dana_id = self.messaging.get_user_id("Dana Chen")
        if self.priya_id is None or self.marco_id is None or self.dana_id is None:
            raise RuntimeError(
                "Failed to resolve messaging user ids for Priya/Marco/Dana"
            )
        self.user_id = self.messaging.current_user_id

        potluck_chat = ConversationV2(
            participant_ids=[
                self.user_id,
                self.priya_id,
                self.marco_id,
                self.dana_id,
            ],
            title="Potluck Crew",
        )
        last_week_ts = self.start_time - 7 * 86_400
        potluck_chat.messages.append(
            MessageV2(
                sender_id=self.priya_id,
                content="Are we still on for a potluck this Sunday at my place? 6pm as usual?",
                timestamp=last_week_ts,
            )
        )
        potluck_chat.messages.append(
            MessageV2(
                sender_id=self.marco_id,
                content="Sounds good — I'll bring a salad.",
                timestamp=last_week_ts + 1_800,
            )
        )
        potluck_chat.messages.append(
            MessageV2(
                sender_id=self.dana_id,
                content="I can do drinks. Should we pick a theme?",
                timestamp=last_week_ts + 3_600,
            )
        )
        potluck_chat.messages.append(
            MessageV2(
                sender_id=self.user_id,
                content="Let's wait for Priya to pick the theme since she's hosting.",
                timestamp=last_week_ts + 5_400,
            )
        )
        potluck_chat.update_last_updated(last_week_ts + 5_400)
        self.messaging.add_conversation(potluck_chat)
        self.potluck_conversation_id = potluck_chat.conversation_id

        # Calendar: seed a couple of baseline events so the agenda is realistic.
        # Sunday 6pm (2025-11-23 18:00 UTC) is intentionally left open so the agent
        # can create the potluck event there on Priya's behalf in Step 4.
        self.calendar = StatefulCalendarApp(name="Calendar")
        baseline_events = [
            CalendarEvent(
                event_id="baseline_team_standup",
                title="Weekly team standup",
                start_datetime=datetime(2025, 11, 17, 10, 0, 0, tzinfo=UTC).timestamp(),
                end_datetime=datetime(2025, 11, 17, 10, 30, 0, tzinfo=UTC).timestamp(),
                tag="work",
                location="Zoom",
                attendees=["Priya Shah"],
            ),
            CalendarEvent(
                event_id="baseline_yoga",
                title="Yoga class",
                start_datetime=datetime(2025, 11, 22, 9, 0, 0, tzinfo=UTC).timestamp(),
                end_datetime=datetime(2025, 11, 22, 10, 0, 0, tzinfo=UTC).timestamp(),
                tag="personal",
                location="Sunrise Studio",
                attendees=[],
            ),
        ]
        for event in baseline_events:
            self.calendar.events[event.event_id] = event

        # Visual asset: load the trifle dessert photo into the sandbox file system so
        # Priya's incoming group-chat message (Step 3) can attach it and the agent can
        # download and visually inspect it.
        trifle_asset_path = SCENARIO_ASSET_DIR / "trifle_dessert.jpg"
        if not trifle_asset_path.exists():
            raise FileNotFoundError(
                f"Trifle dessert image not found: {trifle_asset_path}. "
                f"Place trifle_dessert.jpg under {SCENARIO_ASSET_DIR}."
            )
        self.trifle_sandbox_path = "/trifle_dessert.jpg"
        with self.files.open(self.trifle_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(trifle_asset_path.read_bytes()))

        # Register all apps here in self.apps
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
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        # Plain values precomputed outside capture_mode (these are stable strings
        # seeded in init_and_populate_apps, not Event objects).
        potluck_conversation_id = self.potluck_conversation_id
        priya_id = self.priya_id
        trifle_sandbox_path = self.trifle_sandbox_path

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Priya posts the triggering photo message in the existing "Potluck Crew"
            # group chat. The message contains the actionable specifics (Sunday 6pm,
            # her place, add-to-calendar "on my behalf", rename chat to the dessert
            # theme, reply in chat) and attaches the trifle photo whose identity is
            # only visually knowable. Has a notification template entry for both
            # user and agent streams (StatefulMessagingApp.create_and_add_message).
            priya_trigger_event = messaging_app.create_and_add_message(
                conversation_id=potluck_conversation_id,
                sender_id=priya_id,
                content=(
                    "Hey crew! I'm hosting this Sunday's potluck at my place, 6pm as usual. "
                    "The theme is the dessert in the attached photo. I'll be cooking all day, "
                    "so could you please add the potluck to everyone's calendar on my behalf, "
                    "rename our group chat to include the dessert theme so we all remember what to bring, "
                    "and reply in the chat once it's done? Thanks!"
                ),
                attachment_path=trifle_sandbox_path,
            ).delayed(5)

            # --- ORACLE EVENTS ---
            # Motivation: priya_trigger_event delivered a new group-chat message in
            # "Potluck Crew" that the agent has not yet read; reading it is required
            # to see Priya's asks and the attached dessert photo.
            read_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=potluck_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(priya_trigger_event, delay_seconds=3)
            )

            # Motivation: read_chat_event exposed an image attachment (trifle photo)
            # in the message; explicitly display the seeded sandbox file so the agent
            # can visually identify the dessert (its identity is not in the message
            # text). This is the multimodal visual-inspection step.
            view_trifle_event = (
                files.display(path=trifle_sandbox_path)
                .oracle()
                .depends_on(read_chat_event, delay_seconds=2)
            )

            # Motivation: view_trifle_event let the agent identify the dessert as a
            # layered trifle; priya_trigger_event's text asked the agent to add the
            # Sunday 6pm potluck to everyone's calendar "on my behalf", rename the
            # chat to include the dessert theme, and reply in the chat. Propose the
            # coordinated plan to the user before performing any write action.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "In the Potluck Crew chat, Priya just posted that she's hosting this Sunday's "
                        "potluck at her place at 6pm, with the dessert in her attached photo as the theme. "
                        "I inspected the photo — it's a layered trifle in a glass bowl (sponge, custard, "
                        "and berries). Priya asked me to add the potluck to everyone's calendar on her "
                        "behalf, rename the group chat to include the dessert theme, and reply in the chat "
                        "once done. Plan: create a Sunday 6pm \"Potluck — Trifle Night\" calendar event on "
                        "Priya's behalf (attendees Priya, Marco, Dana; location Priya's place), rename this "
                        "chat to \"Potluck Crew — Trifle Night\", and send a confirmation reply. OK to proceed?"
                    )
                )
                .oracle()
                .depends_on(view_trifle_event, delay_seconds=2)
            )

            # User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(content="Yes, please do all of that.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Motivation: acceptance_event approved the plan; priya_trigger_event
            # explicitly asked the agent to add the event "on my behalf" (Priya), so
            # use add_calendar_event_by_attendee with who_add="Priya Shah". Sunday
            # 6pm UTC = 2025-11-23 18:00:00; end at 20:00:00. Attendees are the
            # other Potluck Crew members (Priya is auto-added by the API).
            add_potluck_event = (
                calendar_app.add_calendar_event_by_attendee(
                    who_add="Priya Shah",
                    title="Potluck — Trifle Night",
                    start_datetime="2025-11-23 18:00:00",
                    end_datetime="2025-11-23 20:00:00",
                    tag="social",
                    description="Potluck at Priya's place. Theme: trifle night.",
                    location="Priya's place",
                    attendees=["Priya Shah", "Marco Rivera", "Dana Chen"],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: acceptance_event approved the plan; priya_trigger_event
            # asked the agent to "rename our group chat to include the dessert
            # theme". The trifle identity came from view_trifle_event. Rename the
            # existing Potluck Crew conversation accordingly.
            rename_chat_event = (
                messaging_app.change_conversation_title(
                    conversation_id=potluck_conversation_id,
                    title="Potluck Crew — Trifle Night",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: priya_trigger_event asked the agent to "reply in the chat
            # once it's done"; both write actions (add_potluck_event,
            # rename_chat_event) have now completed, so confirm in the group chat.
            confirm_in_chat_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=potluck_conversation_id,
                    content=(
                        "Done! I added a \"Potluck — Trifle Night\" event to everyone's calendar on Priya's "
                        "behalf (Sunday 6pm at Priya's place) and renamed this chat to "
                        "\"Potluck Crew — Trifle Night\" so we remember the theme. See you Sunday!"
                    ),
                )
                .oracle()
                .depends_on([add_potluck_event, rename_chat_event], delay_seconds=2)
            )

        self.events: list[Event] = [
            priya_trigger_event,
            read_chat_event,
            view_trifle_event,
            proposal_event,
            acceptance_event,
            add_potluck_event,
            rename_chat_event,
            confirm_in_chat_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        from are.simulation.types import Action, EventType

        def _arg(event: "Event", name: str) -> Any:
            action = event.action
            resolved = getattr(action, "resolved_args", None) or {}
            if name in resolved:
                return resolved[name]
            args = getattr(action, "args", None) or {}
            return args.get(name)

        try:
            log_entries = env.event_log.list_view()

            agent_entries = [
                e for e in log_entries if e.event_type == EventType.AGENT
            ]

            # --- Check 1: Proposal ---
            # Agent offered proactive help to the user via
            # PAREAgentUserInterface.send_message_to_user. We do NOT keyword-match
            # the proposal text and do NOT validate acceptance.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # --- Check 2: Task completed ---
            # The promised task folds three coordinated writes into a single check:
            #   (a) add the Sunday 6pm "Potluck — Trifle Night" calendar event on
            #       Priya's behalf (who_add="Priya Shah") at Priya's place,
            #   (b) rename the existing Potluck Crew conversation to
            #       "Potluck Crew — Trifle Night",
            #   (c) reply in the group chat to confirm.
            # All three must pass for task_completed to be True.
            potluck_conversation_id = self.potluck_conversation_id

            # (a) Calendar event created via add_calendar_event_by_attendee on
            # Priya's behalf with the trifle-night title, Sunday 6pm start, at
            # Priya's place.
            calendar_event_added = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "add_calendar_event_by_attendee"
                and _arg(e, "who_add") == "Priya Shah"
                and _arg(e, "title") == "Potluck — Trifle Night"
                and _arg(e, "start_datetime") == "2025-11-23 18:00:00"
                and _arg(e, "location") == "Priya's place"
                for e in agent_entries
            )

            # (b) Existing Potluck Crew conversation renamed to the trifle theme.
            chat_renamed = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "change_conversation_title"
                and _arg(e, "conversation_id") == potluck_conversation_id
                and _arg(e, "title") == "Potluck Crew — Trifle Night"
                for e in agent_entries
            )

            # (c) Confirmation reply posted in the Potluck Crew group chat.
            group_reply_sent = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and _arg(e, "conversation_id") == potluck_conversation_id
                for e in agent_entries
            )

            task_completed = (
                calendar_event_added and chat_renamed and group_reply_sent
            )

            success = proposal_found and task_completed

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append("no proactive proposal found")
                if not calendar_event_added:
                    failed_checks.append(
                        "task not completed: Sunday 6pm Potluck — Trifle Night calendar event on Priya's behalf not added"
                    )
                if not chat_renamed:
                    failed_checks.append(
                        "task not completed: Potluck Crew conversation not renamed to Potluck Crew — Trifle Night"
                    )
                if not group_reply_sent:
                    failed_checks.append(
                        "task not completed: confirmation reply not posted in the Potluck Crew group chat"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
