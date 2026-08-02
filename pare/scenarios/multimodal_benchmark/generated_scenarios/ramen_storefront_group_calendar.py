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
    / "ramen_storefront_group_calendar"
)


@register_scenario("ramen_storefront_group_calendar")
class RamenStorefrontGroupCalendar(PAREScenario):
    """Agent coordinates a Friday ramen outing by reading a storefront photo, starting a new group chat, and adding a tagged calendar event with attendees.

Marco messages in the user's existing 1:1 Messages thread: "Friday 7pm, wanna check this place out? Start a group with me and Dana so we can plan the meetup, and add it to the calendar." He attaches a photo of a ramen shop storefront but never names the restaurant in the text. The shop's identity (a "Koji Ramen" sign over a red awning with a steaming-bowl logo) is only visible in the image. The assistant must:
1. Read Marco's message and inspect the attached storefront photo (downloaded via the sandbox file system) to identify the venue as Koji Ramen — the visual grounding for the calendar title/location and the reply.
2. Look up Dana in messaging contacts via fuzzy lookup, then propose creating a new group conversation with Marco and Dana plus a Friday 7pm "Ramen at Koji Ramen" calendar event tagged "social" with Marco and Dana as attendees.
3. After user acceptance, create the group conversation, add the tagged calendar event with attendees, and reply in the new group chat with the plan.

The storefront photo is seeded in SandboxLocalFileSystem (e.g., `/koji_ramen_storefront.jpg`) and attached to Marco's incoming message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Friday 7pm time, and the explicit asks to "start a group with me and Dana" and "add it to the calendar" — come from the incoming message, while the image supplies the visual content (Koji Ramen storefront with the red awning and sign) that the calendar title/location and the reply's venue reference depend on and that cannot be known from the filename or message text alone.

This scenario exercises messaging conversation read + attachment download + fuzzy contact lookup (`lookup_user_id`) + new group conversation creation + group reply, multimodal identification of a real-world storefront from a photo, and calendar event creation with attendees and a tag — a new-outing coordination flow rather than an existing-event edit, a participant-add-to-existing-chat, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging: seed Marco + Dana as known contacts and a prior 1:1 thread with Marco
        # so Marco's incoming storefront-photo message lands in an existing conversation.
        # The triggering message itself is delivered as an environment event in Step 3.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.messaging.add_users(["Marco Rivera", "Dana Chen"])
        self.marco_id = self.messaging.get_user_id("Marco Rivera")
        self.dana_id = self.messaging.get_user_id("Dana Chen")
        if self.marco_id is None or self.dana_id is None:
            raise RuntimeError("Failed to resolve messaging user ids for Marco/Dana")
        self.user_id = self.messaging.current_user_id

        marco_conversation = ConversationV2(
            participant_ids=[self.user_id, self.marco_id],
            title="Marco Rivera",
        )
        last_week_ts = self.start_time - 5 * 86_400
        marco_conversation.messages.append(
            MessageV2(
                sender_id=self.marco_id,
                content="Hey, are you free this weekend?",
                timestamp=last_week_ts,
            )
        )
        marco_conversation.messages.append(
            MessageV2(
                sender_id=self.user_id,
                content="Maybe — what did you have in mind?",
                timestamp=last_week_ts + 600,
            )
        )
        marco_conversation.update_last_updated(last_week_ts + 600)
        self.messaging.add_conversation(marco_conversation)
        self.marco_conversation_id = marco_conversation.conversation_id

        # Calendar: seed a few baseline events. Friday 7pm (2025-11-21 19:00 UTC) is
        # intentionally left open so the agent can create the ramen outing there.
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
                event_id="baseline_dentist",
                title="Dentist appointment",
                start_datetime=datetime(2025, 11, 19, 14, 0, 0, tzinfo=UTC).timestamp(),
                end_datetime=datetime(2025, 11, 19, 15, 0, 0, tzinfo=UTC).timestamp(),
                tag="personal",
                location="Bright Smile Dental",
                attendees=[],
            ),
            CalendarEvent(
                event_id="baseline_lunch_sarah",
                title="Lunch with Sarah",
                start_datetime=datetime(2025, 11, 20, 12, 30, 0, tzinfo=UTC).timestamp(),
                end_datetime=datetime(2025, 11, 20, 13, 30, 0, tzinfo=UTC).timestamp(),
                tag="social",
                location="Green Leaf Cafe",
                attendees=["Sarah Kim"],
            ),
        ]
        for event in baseline_events:
            self.calendar.events[event.event_id] = event

        # Visual asset: load the Koji Ramen storefront photo into the sandbox file system
        # so Marco's incoming message (Step 3) can attach it and the agent can download/view.
        koji_asset_path = SCENARIO_ASSET_DIR / "koji_ramen_storefront.jpg"
        if not koji_asset_path.exists():
            raise FileNotFoundError(
                f"Koji Ramen storefront image not found: {koji_asset_path}. "
                f"Place koji_ramen_storefront.jpg under {SCENARIO_ASSET_DIR}."
            )
        self.koji_ramen_sandbox_path = "/koji_ramen_storefront.jpg"
        with self.files.open(self.koji_ramen_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(koji_asset_path.read_bytes()))

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

        # Pre-compute plain values (ids/paths) outside capture_mode so we never
        # accidentally pass Event objects into tool APIs that expect strings.
        marco_conversation_id = self.marco_conversation_id
        marco_id = self.marco_id
        dana_id = self.dana_id
        koji_photo_path = self.koji_ramen_sandbox_path
        # Friday 7pm following the start_time week (start_time = Tue 2025-11-18 09:00 UTC).
        ramen_start_datetime = "2025-11-21 19:00:00"
        ramen_end_datetime = "2025-11-21 21:00:00"

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Marco sends a storefront photo + plan request in the existing 1:1 thread.
            # This is the exogenous trigger; it carries the Friday 7pm time and the
            # "start a group with me and Dana" / "add it to the calendar" asks, plus
            # the attached Koji Ramen storefront image the agent must inspect.
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=marco_conversation_id,
                sender_id=marco_id,
                content=(
                    "Friday 7pm, wanna check this place out? Start a group with me "
                    "and Dana so we can plan the meetup, and add it to the calendar."
                ),
                attachment_path=koji_photo_path,
            ).delayed(5)

            # --- ORACLE EVENTS ---
            # Read the 1:1 thread so the agent observes Marco's message text and the
            # attached storefront photo. Motivated by incoming_message_event
            # ("Friday 7pm ... Start a group with me and Dana ... add it to the calendar").
            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=marco_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            # Inspect the attached storefront photo via Files so the agent can read
            # the "KOJI RAMEN" sign / red awning and identify the venue visually.
            # Motivated by the attachment in incoming_message_event ("wanna check
            # this place out?") — the venue name is only visible in the image.
            view_storefront_event = (
                files.display(path=koji_photo_path)
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            # Fuzzy lookup Dana in messaging contacts so the agent can resolve her
            # user_id for the group conversation. Motivated by Marco's explicit ask
            # in incoming_message_event ("Start a group with me and Dana").
            lookup_dana_event = (
                messaging_app.lookup_user_id(user_name="Dana")
                .oracle()
                .depends_on(view_storefront_event, delay_seconds=1)
            )

            # Proposal: cite Marco's message (Friday 7pm, group with me and Dana,
            # add to calendar) and the visual identification of Koji Ramen from the
            # storefront photo. No write happens here — only after user acceptance.
            # Grounded in incoming_message_event + view_storefront_event +
            # lookup_dana_event.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Marco messaged in your 1:1 thread: \"Friday 7pm, wanna check "
                        "this place out? Start a group with me and Dana so we can plan "
                        "the meetup, and add it to the calendar.\" I inspected the "
                        "attached storefront photo — it's Koji Ramen (red awning with a "
                        "steaming-bowl logo, KOJI RAMEN sign above the entrance). I also "
                        "looked up Dana in your messaging contacts. Want me to create a "
                        "new group chat with Marco and Dana titled \"Ramen at Koji Ramen\" "
                        "and add a Friday 7pm \"Ramen at Koji Ramen\" calendar event tagged "
                        "\"social\" at Koji Ramen with Marco and Dana as attendees, then "
                        "reply in the new group chat with the plan?"
                    )
                )
                .oracle()
                .depends_on(lookup_dana_event, delay_seconds=2)
            )

            # User accepts the proposal (motivated by proposal_event above).
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes — please create the group chat with Marco and Dana and add "
                        "the Friday 7pm Koji Ramen calendar event, then reply in the "
                        "group chat with the plan."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- User-gated write actions (all depend on acceptance_event) ---
            # Create the new group conversation with Marco and Dana. Motivated by
            # Marco's "Start a group with me and Dana" in incoming_message_event
            # and the user's acceptance.
            create_group_event = (
                messaging_app.create_group_conversation(
                    user_ids=[marco_id, dana_id], title="Ramen at Koji Ramen"
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Add the Friday 7pm "Ramen at Koji Ramen" calendar event tagged
            # "social" with Marco and Dana as attendees. Motivated by Marco's
            # "add it to the calendar" in incoming_message_event + the visual
            # identification of Koji Ramen (view_storefront_event) + acceptance.
            add_calendar_event_event = (
                calendar_app.add_calendar_event(
                    title="Ramen at Koji Ramen",
                    start_datetime=ramen_start_datetime,
                    end_datetime=ramen_end_datetime,
                    tag="social",
                    location="Koji Ramen",
                    attendees=["Marco Rivera", "Dana Chen"],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Reply in the new group chat with the plan. Motivated by Marco's
            # "Start a group with me and Dana so we can plan the meetup" in
            # incoming_message_event + acceptance. The conversation_id is resolved
            # at runtime by the agent from the group created by create_group_event;
            # placeholder used here per established pattern.
            send_group_msg_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id="",
                    content=(
                        "Hey Marco and Dana — setting up Friday's ramen meetup. I "
                        "checked out the storefront photo Marco sent: it's Koji Ramen "
                        "(red awning, KOJI RAMEN sign). Plan is Friday 7pm at Koji "
                        "Ramen. I've added a \"Ramen at Koji Ramen\" calendar event "
                        "tagged social with both of you as attendees — see you there."
                    ),
                )
                .oracle()
                .depends_on(
                    [create_group_event, add_calendar_event_event], delay_seconds=2
                )
            )

        # Register ALL events so they actually execute.
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            view_storefront_event,
            lookup_dana_event,
            proposal_event,
            acceptance_event,
            create_group_event,
            add_calendar_event_event,
            send_group_msg_event,
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
            # Agent offered proactive help to the user via PAREAgentUserInterface.send_message_to_user.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # --- Check 2: Task completed ---
            # The promised task folds three coordinated writes into a single check:
            #   (a) create a new group conversation with Marco and Dana,
            #   (b) add the Friday 7pm "Ramen at Koji Ramen" calendar event tagged
            #       "social" with Marco Rivera and Dana Chen as attendees,
            #   (c) reply in the new group chat with the plan.
            # All three must pass for task_completed to be True.

            # (a) Group conversation created with both Marco and Dana as participants.
            group_conversation_created = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "create_group_conversation"
                and set(_arg(e, "user_ids") or []).issuperset(
                    {self.marco_id, self.dana_id}
                )
                for e in agent_entries
            )

            # (b) Calendar event added: Friday 7pm (2025-11-21 19:00:00), tag "social",
            # location references Koji Ramen, attendees include Marco Rivera and Dana Chen.
            calendar_event_added = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "add_calendar_event"
                and _arg(e, "start_datetime") == "2025-11-21 19:00:00"
                and _arg(e, "tag") == "social"
                and "Koji Ramen" in str(_arg(e, "location") or "")
                and set(_arg(e, "attendees") or []).issuperset(
                    {"Marco Rivera", "Dana Chen"}
                )
                for e in agent_entries
            )

            # (c) Reply posted in the new group conversation.
            group_reply_sent = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                for e in agent_entries
            )

            task_completed = (
                group_conversation_created
                and calendar_event_added
                and group_reply_sent
            )

            success = proposal_found and task_completed

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append("no proactive proposal found")
                if not group_conversation_created:
                    failed_checks.append(
                        "task not completed: group conversation with Marco and Dana not created"
                    )
                if not calendar_event_added:
                    failed_checks.append(
                        "task not completed: Friday 7pm social calendar event at Koji Ramen with Marco and Dana not added"
                    )
                if not group_reply_sent:
                    failed_checks.append(
                        "task not completed: reply not posted in the new group conversation"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
