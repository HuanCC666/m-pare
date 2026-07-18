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
    / "garden_pest_damage_workday"
)
GARDEN_TOMATO_PEST_SANDBOX_PATH = "/garden_tomato_pest.jpg"


@register_scenario("garden_pest_damage_workday")
class GardenPestDamageWorkday(PAREScenario):
    """Agent coordinates a community-garden pest-cleanup workday after a group-chat message and a photo of damaged tomato plants.

In the existing "Cedar Street Garden Crew" group chat (Priya, Marco, Dana, and the user), Priya posts: she checked the beds this morning and the tomato plants look bad, and she attaches a photo of the affected bed. She asks the user to add a Sunday 8am "Garden workday — tomato bed cleanup" calendar event at the Cedar Street Community Garden, to add Sam Reyes (a new neighbor who texted her separately and offered to help this weekend) to this chat so he can see the plan, and to reply in the chat once it's done. The specific damage (tomato hornworms — large green caterpillars and chewed, ragged leaves) is only visible in the photo — the message just says "look bad" and never names the pest. The assistant must:
1. Read the group-chat message and download/inspect the attached garden photo via the sandbox file system to identify the pest damage so the workday title and reply can name the cleanup target accurately.
2. Look up Sam Reyes in messaging contacts via fuzzy lookup (`lookup_user_id`), then propose creating the Sunday 8am "Garden workday — tomato bed cleanup" calendar event at Cedar Street Community Garden (with Priya, Marco, and Dana as attendees), adding Sam to the group chat, and replying in the chat with the plan.
3. After user acceptance, create the calendar event, add Sam to the group conversation, and send the confirmation reply to the group chat.

The tomato-bed photo is seeded in SandboxLocalFileSystem (e.g., `/garden_tomato_pest.jpg`) and attached to Priya's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Sunday 8am time, the Cedar Street Community Garden venue, Sam Reyes as the neighbor to add, and the explicit asks to add the event, add Sam to the chat, and reply — come from the incoming message, while the image supplies the visual content (the hornworm-chewed tomato leaves) that the workday title and reply's cleanup target depend on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + attachment download + fuzzy contact lookup (`lookup_user_id`) + add-participant-to-existing-conversation + outbound group reply, multimodal identification of real-world plant pest damage from a photo, and calendar event creation with attendees, tag, and location — a community-garden workday coordination flow rather than an existing-event edit, a chat rename, a new-group creation, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging: seed contacts + the pre-existing "Cedar Street Garden Crew"
        # group chat (Priya, Marco, Dana, and the user). Priya's triggering
        # tomato-bed photo message is delivered as an environment event in Step 3;
        # here we only seed baseline history so the conversation already exists.
        # Sam Reyes is added as a contact so the agent can resolve him via
        # lookup_user_id and add him to the group chat in Step 3.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.messaging.add_users(["Priya Patel", "Marco Diaz", "Dana Nguyen", "Sam Reyes"])
        self.priya_id = self.messaging.get_user_id("Priya Patel")
        self.marco_id = self.messaging.get_user_id("Marco Diaz")
        self.dana_id = self.messaging.get_user_id("Dana Nguyen")
        self.sam_id = self.messaging.get_user_id("Sam Reyes")
        if (
            self.priya_id is None
            or self.marco_id is None
            or self.dana_id is None
            or self.sam_id is None
        ):
            raise RuntimeError(
                "Failed to resolve messaging user ids for Priya/Marco/Dana/Sam"
            )
        self.user_id = self.messaging.current_user_id

        garden_chat = ConversationV2(
            participant_ids=[
                self.user_id,
                self.priya_id,
                self.marco_id,
                self.dana_id,
            ],
            title="Cedar Street Garden Crew",
        )
        last_week_ts = self.start_time - 7 * 86_400
        garden_chat.messages.append(
            MessageV2(
                sender_id=self.marco_id,
                content="Saturday's compost turn went well — beds are looking ready for fall planting.",
                timestamp=last_week_ts,
            )
        )
        garden_chat.messages.append(
            MessageV2(
                sender_id=self.dana_id,
                content="I can bring extra stakes and twine next workday.",
                timestamp=last_week_ts + 2_400,
            )
        )
        garden_chat.messages.append(
            MessageV2(
                sender_id=self.priya_id,
                content="Thanks! I'll check the tomato bed later this week and report back.",
                timestamp=last_week_ts + 5_400,
            )
        )
        garden_chat.update_last_updated(last_week_ts + 5_400)
        self.messaging.add_conversation(garden_chat)
        self.garden_conversation_id = garden_chat.conversation_id

        # Calendar: seed a couple of baseline events so the agenda is realistic.
        # Sunday 8am (2025-11-23 08:00 UTC) is intentionally left open so the agent
        # can create the "Garden workday — tomato bed cleanup" event there in Step 3.
        self.calendar = StatefulCalendarApp(name="Calendar")
        baseline_events = [
            CalendarEvent(
                event_id="baseline_team_standup",
                title="Weekly team standup",
                start_datetime=datetime(2025, 11, 17, 10, 0, 0, tzinfo=UTC).timestamp(),
                end_datetime=datetime(2025, 11, 17, 10, 30, 0, tzinfo=UTC).timestamp(),
                tag="work",
                location="Zoom",
                attendees=["Marco Diaz"],
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

        # Visual asset: load the tomato-bed pest photo into the sandbox file system
        # so Priya's incoming group-chat message (Step 3) can attach it and the agent
        # can download and visually inspect it to identify the hornworm damage.
        garden_asset_path = SCENARIO_ASSET_DIR / "garden_tomato_pest.jpg"
        if not garden_asset_path.exists():
            raise FileNotFoundError(
                f"Garden tomato pest image not found: {garden_asset_path}. "
                f"Place garden_tomato_pest.jpg under {SCENARIO_ASSET_DIR}."
            )
        self.garden_tomato_pest_sandbox_path = GARDEN_TOMATO_PEST_SANDBOX_PATH
        with self.files.open(self.garden_tomato_pest_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(garden_asset_path.read_bytes()))

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

        # Plain values precomputed from seeded baseline state (stable string ids);
        # computed outside capture_mode so they are never mistaken for Event objects.
        garden_conversation_id = self.garden_conversation_id
        priya_id = self.priya_id
        sam_id = self.sam_id
        tomato_photo_path = self.garden_tomato_pest_sandbox_path

        with EventRegisterer.capture_mode():
            # === NON-ORACLE ENVIRONMENT EVENT: Priya posts in the Cedar Street Garden Crew
            # group chat with the tomato-bed photo attached. This is the concrete exogenous
            # trigger; it has a notification template entry for StatefulMessagingApp
            # ("create_and_add_message") in both user and agent streams. The message text
            # carries all actionable specifics (Sunday 8am, Cedar Street Community Garden,
            # Sam Reyes, add-event / add-Sam / reply asks); the photo carries the pest
            # damage content that the message only describes as "look bad". ===
            priya_message_event = messaging_app.create_and_add_message(
                conversation_id=garden_conversation_id,
                sender_id=priya_id,
                content=(
                    "I checked the beds this morning and the tomato plants look bad — I attached "
                    "a photo of the affected bed. Can you add a Sunday 8am \"Garden workday — "
                    "tomato bed cleanup\" calendar event at the Cedar Street Community Garden, "
                    "add Sam Reyes (a new neighbor who texted me separately and offered to help "
                    "this weekend) to this chat so he can see the plan, and reply in the chat "
                    "once it's done?"
                ),
                attachment_path=tomato_photo_path,
            ).delayed(5)

            # === Oracle: agent reads the group chat to observe Priya's message + attachment. ===
            # Motivated by priya_message_event: Priya's incoming group-chat message asking the
            # user to "add a Sunday 8am Garden workday ... event", "add Sam Reyes ... to this
            # chat", and "reply in the chat once it's done" — reading reveals the full request
            # and the attached photo message.
            read_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=garden_conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(priya_message_event, delay_seconds=2)
            )

            # === Oracle: agent visually inspects the attached garden photo. ===
            # Motivated by read_chat_event, which exposed Priya's "I attached a photo of the
            # affected bed" plus the image attachment. The message text only says the plants
            # "look bad" and never names the pest, so the workday title / reply cleanup target
            # must be grounded in the photo content (hornworm-chewed tomato leaves).
            view_photo_event = (
                files.display(path=tomato_photo_path)
                .oracle()
                .depends_on(read_chat_event, delay_seconds=1)
            )

            # === Oracle: agent fuzzy-looks up Sam Reyes in messaging contacts. ===
            # Motivated by priya_message_event: "add Sam Reyes (a new neighbor who texted me
            # separately...) to this chat". The agent must resolve Sam's user_id before it can
            # add him to the existing group conversation; lookup_user_id is the fuzzy-contact
            # lookup that reveals the id for a near-match name.
            lookup_sam_event = (
                messaging_app.lookup_user_id(user_name="Sam Reyes")
                .oracle()
                .depends_on(priya_message_event, delay_seconds=1)
            )

            # === Oracle: agent sends a proactive proposal to the user. ===
            # Grounded in priya_message_event ("add a Sunday 8am Garden workday — tomato bed "
            # "cleanup calendar event at the Cedar Street Community Garden", "add Sam Reyes "
            # "... to this chat", "reply in the chat once it's done") and view_photo_event
            # (the hornworm-chewed tomato leaves observed in the attached photo). Sam's id was
            # resolved by lookup_sam_event. The proposal cites the concrete env cue facts
            # (time, venue, neighbor name, three asks) and the visual pest identification.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Priya just posted in the Cedar Street Garden Crew chat: the tomato bed "
                        "looks bad and she attached a photo. I inspected the photo — the leaves "
                        "are chewed and ragged with large green hornworm caterpillars, so this "
                        "is tomato hornworm damage. Priya asked me to (1) add a Sunday 8am "
                        "\"Garden workday — tomato bed cleanup\" calendar event at the Cedar "
                        "Street Community Garden with Priya, Marco, and Dana as attendees, "
                        "(2) add Sam Reyes to the group chat, and (3) reply in the chat once "
                        "done. I looked up Sam Reyes in messaging contacts. Want me to go ahead "
                        "with all three?"
                    )
                )
                .oracle()
                .depends_on([view_photo_event, lookup_sam_event], delay_seconds=2)
            )

            # === User accepts the proposal. ===
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes, please create the workday event, add Sam to the chat, and reply."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # === Oracle (write, user-gated): agent creates the Sunday 8am garden workday
            # calendar event with attendees + location. ===
            # Grounded in acceptance_event + priya_message_event's explicit time/venue/title
            # ask ("Sunday 8am", "Cedar Street Community Garden", "Garden workday — tomato bed
            # cleanup") and view_photo_event (hornworm damage grounds the cleanup target).
            create_workday_event = (
                calendar_app.add_calendar_event_by_attendee(
                    who_add="John Doe",
                    title="Garden workday — tomato bed cleanup",
                    start_datetime="2025-11-23 08:00:00",
                    end_datetime="2025-11-23 10:00:00",
                    tag="personal",
                    location="Cedar Street Community Garden",
                    description=(
                        "Community-garden workday to remove tomato hornworms and clean up the "
                        "chewed tomato bed. Priya, Marco, and Dana confirmed; Sam Reyes joining "
                        "to help."
                    ),
                    attendees=["Priya Patel", "Marco Diaz", "Dana Nguyen"],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # === Oracle (write, user-gated): agent adds Sam Reyes to the existing group
            # conversation. ===
            # Grounded in acceptance_event + priya_message_event ("add Sam Reyes ... to this "
            # "chat"); sam_id was resolved by lookup_sam_event so the agent acts on the
            # correct current target identity.
            add_sam_event = (
                messaging_app.add_participant_to_conversation(
                    conversation_id=garden_conversation_id,
                    user_id=sam_id,
                )
                .oracle()
                .depends_on([acceptance_event, lookup_sam_event], delay_seconds=1)
            )

            # === Oracle (write, user-gated): agent replies in the group chat with the plan. ===
            # Grounded in acceptance_event + priya_message_event ("reply in the chat once it's "
            # "done") and view_photo_event (hornworm damage identified from the photo). Depends
            # on the two prior write events so the reply reflects completed state.
            reply_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=garden_conversation_id,
                    content=(
                        "Done! I inspected Priya's photo — the tomato bed has tomato hornworm "
                        "damage (chewed ragged leaves and large green caterpillars). I added a "
                        "Sunday 8am \"Garden workday — tomato bed cleanup\" calendar event at "
                        "the Cedar Street Community Garden with Priya, Marco, and Dana as "
                        "attendees, and added Sam Reyes to this chat so he can see the plan."
                    ),
                )
                .oracle()
                .depends_on([create_workday_event, add_sam_event], delay_seconds=2)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            priya_message_event,
            read_chat_event,
            view_photo_event,
            lookup_sam_event,
            proposal_event,
            acceptance_event,
            create_workday_event,
            add_sam_event,
            reply_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            agent_events = [e for e in log_entries if e.event_type == EventType.AGENT]

            garden_conversation_id = self.garden_conversation_id
            sam_id = self.sam_id

            # Check 1 — Proposal: the proactive agent offered help to the user
            # via PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                e.action is not None
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_events
            )

            # Check 2 — Task: the agent completed the three promised user-visible
            # side effects after the proposal:
            #   (a) created the Sunday 8am "Garden workday — tomato bed cleanup"
            #       calendar event at Cedar Street Community Garden,
            #   (b) added Sam Reyes to the existing garden group conversation, and
            #   (c) replied in the garden group chat.
            # All three writes must appear in the AGENT event log with the required
            # structural identifiers; free-form text bodies are not asserted.
            workday_event_created = False
            sam_added = False
            group_reply_sent = False

            for e in agent_events:
                action = e.action
                if action is None:
                    continue
                class_name = action.class_name
                function_name = action.function_name
                args = action.args or {}

                if (
                    class_name == "StatefulCalendarApp"
                    and function_name == "add_calendar_event_by_attendee"
                ):
                    if (
                        args.get("title") == "Garden workday — tomato bed cleanup"
                        and args.get("start_datetime") == "2025-11-23 08:00:00"
                        and args.get("location") == "Cedar Street Community Garden"
                    ):
                        workday_event_created = True

                elif (
                    class_name == "StatefulMessagingApp"
                    and function_name == "add_participant_to_conversation"
                ):
                    if (
                        args.get("conversation_id") == garden_conversation_id
                        and args.get("user_id") == sam_id
                    ):
                        sam_added = True

                elif (
                    class_name == "StatefulMessagingApp"
                    and function_name == "send_message_to_group_conversation"
                ):
                    if args.get("conversation_id") == garden_conversation_id:
                        group_reply_sent = True

            task_completed = workday_event_created and sam_added and group_reply_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not workday_event_created:
                rationale = (
                    "task not completed: Sunday 8am 'Garden workday — tomato bed "
                    "cleanup' calendar event at Cedar Street Community Garden not "
                    "created"
                )
            elif not sam_added:
                rationale = "task not completed: Sam Reyes not added to garden chat"
            else:
                rationale = "task not completed: no reply sent to garden group chat"
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
