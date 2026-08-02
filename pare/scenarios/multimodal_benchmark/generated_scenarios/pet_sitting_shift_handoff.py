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
    / "pet_sitting_shift_handoff"
)


@register_scenario("pet_sitting_shift_handoff")
class PetSittingShiftHandoff(PAREScenario):
    """Agent swaps onto a friend's pet-sitting shift after a group-chat message and a photo of the cat's feeding setup.

In the existing "Pet Sitters Co-op" group chat (Marco, Dana, and the user), Marco posts: a family emergency came up and he's flying out tonight, so he can't cover Dana's cat-sitting shift this Thursday at 6pm that he had signed up for. He attaches a photo of Dana's cat's feeding station (the cat herself plus two color-coded food bins) and asks the user to take the shift, update the existing "Cat-sitting for Dana" calendar event to swap himself out and the user in, remove him from this chat since he'll be offline, and reply in the chat to confirm so Dana knows it's covered. The cat's appearance (an orange tabby with a white front paw) and which bin is the wet vs dry food are only visible in the photo — the message never describes them. The assistant must:
1. Read the group-chat message and download/inspect the attached feeding-station photo via the sandbox file system to identify the cat and the food-bin colors so the reply can acknowledge the routine.
2. Search the calendar for the Thursday "Cat-sitting for Dana" event and propose editing its attendee list to remove Marco and add the user, removing Marco from the group conversation, and replying in the chat to confirm the swap.
3. After user acceptance, edit the calendar event's attendees, remove Marco from the group chat, and send the confirmation reply to the group chat.

The feeding-station photo is seeded in SandboxLocalFileSystem (e.g., `/dana_cat_feeding.jpg`) and attached to Marco's incoming group-chat message; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the Thursday 6pm shift, and the explicit asks to take the shift, update the calendar, remove Marco from the chat, and reply — come from the incoming message, while the image supplies the visual content (the orange tabby and the color-coded food bins) that the reply's confirmation depends on and that cannot be known from the filename or message text alone.

This scenario exercises messaging group-chat read + attachment download + remove-participant + outbound group reply, multimodal identification of a pet and its feeding setup from a photo, and calendar search + existing-event attendee edit — a shift-swap handoff coordination flow rather than a new-event creation, a location edit, or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging app: pre-existing "Pet Sitters Co-op" group chat ---
        # internal_fs wired to the sandbox FS so Step 3 can attach /dana_cat_feeding.jpg
        # and the agent can download + view it for multimodal identification.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # --- Calendar app: pre-existing "Cat-sitting for Dana" shift event ---
        self.calendar = StatefulCalendarApp(name="Calendar")

        # --- Visual asset: Dana's cat feeding-station photo ---
        # Seeded into the sandbox FS as baseline state; Marco's triggering message
        # (Step 3 environment event) will attach it from /dana_cat_feeding.jpg.
        local_image_path = SCENARIO_ASSET_DIR / "dana_cat_feeding.jpg"
        if not local_image_path.exists():
            raise FileNotFoundError(
                f"Dana cat feeding photo not found: {local_image_path}. "
                f"Place dana_cat_feeding.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open("/dana_cat_feeding.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_image_path.read_bytes()))

        # --- Messaging personas + pre-existing group conversation ---
        # Marco and Dana are members of the "Pet Sitters Co-op" group chat with the user.
        self.marco_name = "Marco Reyes"
        self.dana_name = "Dana Whitfield"
        self.messaging.add_users([self.marco_name, self.dana_name])
        self.marco_id = self.messaging.get_user_id(self.marco_name)
        self.dana_id = self.messaging.get_user_id(self.dana_name)
        if self.marco_id is None or self.dana_id is None:
            raise RuntimeError("Failed to resolve Marco/Dana messaging user ids")

        group_conversation = ConversationV2(
            participant_ids=[
                self.messaging.current_user_id,
                self.marco_id,
                self.dana_id,
            ],
            title="Pet Sitters Co-op",
        )
        # Baseline history predates start_time (Tue 2025-11-18 09:00 UTC).
        # Marco's triggering message with the feeding-station photo arrives in Step 3.
        prior_dana_ts = self.start_time - 2 * 86_400  # Sun 2025-11-16 09:00 UTC
        prior_marco_ts = self.start_time - 2 * 86_400 + 3600  # one hour later
        group_conversation.messages.append(
            MessageV2(
                sender_id=self.dana_id,
                content=(
                    "Hey team - is anyone free to cat-sit this Thursday evening? "
                    "Just need someone to drop by at 6pm Thursday for the feeding."
                ),
                timestamp=prior_dana_ts,
            )
        )
        group_conversation.messages.append(
            MessageV2(
                sender_id=self.marco_id,
                content="I can take Thursday's 6pm shift - sign me up!",
                timestamp=prior_marco_ts,
            )
        )
        group_conversation.update_last_updated(prior_marco_ts)
        self.messaging.add_conversation(group_conversation)
        self.group_conversation_id = group_conversation.conversation_id

        # --- Calendar: pre-existing "Cat-sitting for Dana" event on Thursday 6pm ---
        # start_time is Tue 2025-11-18 09:00 UTC; the shift is Thursday 2025-11-20 18:00.
        # Marco is the signed-up sitter; Dana is the cat owner. The user is NOT yet an
        # attendee (Step 3/4 swaps Marco out and the user in).
        thursday_start_ts = datetime(2025, 11, 20, 18, 0, 0, tzinfo=UTC).timestamp()
        thursday_end_ts = datetime(2025, 11, 20, 19, 0, 0, tzinfo=UTC).timestamp()
        cat_sitting_event = CalendarEvent(
            title="Cat-sitting for Dana",
            start_datetime=thursday_start_ts,
            end_datetime=thursday_end_ts,
            tag="pet_sitting",
            description=(
                "Drop-in cat-sitting shift at Dana's apartment. Feed the cat and "
                "refresh water. Marco signed up to cover this shift."
            ),
            location="Dana's apartment",
            attendees=[self.marco_name, self.dana_name],
        )
        self.calendar.events[cat_sitting_event.event_id] = cat_sitting_event
        self.calendar_event_id = cat_sitting_event.event_id

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

        cat_photo_path = "/dana_cat_feeding.jpg"
        user_name = messaging_app.current_user_name

        with EventRegisterer.capture_mode():
            # --- Non-oracle ENV event: Marco posts the shift-swap request in the
            # "Pet Sitters Co-op" group chat with the feeding-station photo
            # attached. All actionable asks (take Thursday 6pm shift, update the
            # existing "Cat-sitting for Dana" calendar event to swap Marco out
            # and the user in, remove Marco from this chat, reply in chat to
            # confirm) live in this env message text.
            marco_handoff_event = messaging_app.create_and_add_message(
                conversation_id=self.group_conversation_id,
                sender_id=self.marco_id,
                content=(
                    "Hey team - a family emergency came up and I'm flying out "
                    "tonight, so I can't cover Dana's cat-sitting shift this "
                    "Thursday at 6pm that I'd signed up for. Could you take the "
                    "shift for me? Please also: update the existing "
                    "\"Cat-sitting for Dana\" calendar event to swap me out and "
                    "you in, remove me from this chat since I'll be offline, and "
                    "reply in the chat to confirm so Dana knows it's covered. "
                    "Attaching a photo of Dana's cat's feeding station so you "
                    "can see the setup."
                ),
                attachment_path=cat_photo_path,
            ).delayed(5)

            # Oracle: agent reads the "Pet Sitters Co-op" group chat after
            # Marco's incoming message (env cue: "Hey team - a family emergency
            # came up ... Could you take the shift for me?").
            read_chat_event = messaging_app.read_conversation(
                conversation_id=self.group_conversation_id,
                offset=0,
                limit=10,
            ).oracle().depends_on(marco_handoff_event, delay_seconds=2)

            # Oracle: agent inspects the attached feeding-station photo to
            # identify the cat (orange tabby with a white front paw) and the
            # color-coded food bins (blue = dry, red = wet) before proposing.
            # These facts are only visible in the image, never in Marco's text.
            view_photo_event = (
                files.display(path=cat_photo_path)
                .oracle()
                .depends_on(read_chat_event, delay_seconds=1)
            )

            # Oracle: agent searches the calendar for the existing
            # "Cat-sitting for Dana" event Marco asked to update (env cue:
            # "update the existing 'Cat-sitting for Dana' calendar event to
            # swap me out and you in"). The returned CalendarEvent exposes the
            # event_id used by the downstream edit.
            search_calendar_event = (
                calendar_app.search_events(query="Cat-sitting for Dana")
                .oracle()
                .depends_on(view_photo_event, delay_seconds=2)
            )

            # Oracle: agent proposes the shift-swap handoff to the user, citing
            # Marco's incoming message (env cue: "take the shift", "update the
            # existing 'Cat-sitting for Dana' calendar event to swap me out and
            # you in", "remove me from this chat", "reply in the chat to
            # confirm") and the visual evidence from view_photo_event (orange
            # tabby with a white front paw; blue bin = dry food, red bin = wet
            # food).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Marco just posted in the Pet Sitters Co-op chat: a "
                        "family emergency came up, he's flying out tonight, and "
                        "he can't cover Dana's cat-sitting shift this Thursday "
                        "at 6pm. He asked you to take the shift, update the "
                        "existing \"Cat-sitting for Dana\" calendar event to "
                        "swap him out and you in, remove him from the chat, and "
                        "reply in the chat to confirm. I inspected his photo of "
                        "Dana's feeding station: an orange tabby with a white "
                        "front paw, a blue bin of dry food, and a red bin of wet "
                        "food. Want me to edit the calendar event's attendees, "
                        "remove Marco from the chat, and post a confirmation "
                        "reply for you?"
                    )
                )
                .oracle()
                .depends_on(search_calendar_event, delay_seconds=2)
            )

            # User accepts the agent's proposal to perform the swap handoff.
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes - please edit the Cat-sitting for Dana calendar "
                        "event to swap Marco out and me in, remove Marco from "
                        "the chat, and reply in the chat to confirm."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Oracle WRITE: edit the existing calendar event's attendees to
            # remove Marco and add the user. Grounded in acceptance_event, the
            # search_calendar_event observation (event_id), and the
            # marco_handoff_event env cue ("swap me out and you in").
            edit_calendar_event = (
                calendar_app.edit_calendar_event(
                    event_id=self.calendar_event_id,
                    attendees=[user_name, self.dana_name],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # Oracle WRITE: post the confirmation reply in the group chat so
            # Dana knows the shift is covered. Sent BEFORE removing Marco so
            # the conversation still has 3 participants (group-conversation
            # guard). Grounded in acceptance_event and the marco_handoff_event
            # env cue ("reply in the chat to confirm so Dana knows it's
            # covered"). Visual facts from view_photo_event are acknowledged.
            send_confirmation_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=self.group_conversation_id,
                    content=(
                        "Covered - I'm taking over Dana's cat-sitting shift "
                        "this Thursday at 6pm. From Marco's feeding-station "
                        "photo: orange tabby with a white front paw, blue bin "
                        "is dry food, red bin is wet food. I've updated the "
                        "calendar event to swap Marco out and me in, and I'll "
                        "remove Marco from this chat now. See you Thursday, Dana!"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Oracle WRITE: remove Marco from the Pet Sitters Co-op group chat.
            # Grounded in acceptance_event and the marco_handoff_event env cue
            # ("remove me from this chat since I'll be offline"). marco_id was
            # revealed as the sender_id of Marco's incoming message via
            # read_chat_event, and is also a seeded participant of the group
            # conversation.
            remove_marco_event = (
                messaging_app.remove_participant_from_conversation(
                    conversation_id=self.group_conversation_id,
                    user_id=self.marco_id,
                )
                .oracle()
                .depends_on(send_confirmation_event, delay_seconds=1)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            marco_handoff_event,
            read_chat_event,
            view_photo_event,
            search_calendar_event,
            proposal_event,
            acceptance_event,
            edit_calendar_event,
            send_confirmation_event,
            remove_marco_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        # Local imports keep this section self-contained; the are.simulation.types
        # Action/EventType enums are required to inspect the event log.
        from are.simulation.types import Action, EventType

        try:
            log_entries = env.event_log.list_view()
            user_name = self.messaging.current_user_name

            # --- Check 1: Proposal ---
            # Prove the proactive agent offered help via
            # PAREAgentUserInterface.send_message_to_user(...). No keyword
            # matching on proposal text; acceptance is not validated.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2: Task ---
            # The promised handoff requires three coordinated writes, all
            # grounded in the seeded ids and the narrative's expected side
            # effects:
            #   (a) edit the existing "Cat-sitting for Dana" calendar event to
            #       swap Marco out and the user in (attendees must include the
            #       user and Dana and must NOT include Marco).
            #   (b) post a confirmation reply in the "Pet Sitters Co-op" group
            #       chat (conversation_id matches the seeded group).
            #   (c) remove Marco from that group conversation (conversation_id
            #       and user_id match the seeded group and Marco's id).
            # All three must pass for task_completed.
            calendar_edited = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "edit_calendar_event"
                and e.action.args.get("event_id") == self.calendar_event_id
                and set(e.action.args.get("attendees", []))
                == {user_name, self.dana_name}
                and self.marco_name not in e.action.args.get("attendees", [])
                for e in log_entries
            )

            confirmation_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == self.group_conversation_id
                for e in log_entries
            )

            marco_removed = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name
                == "remove_participant_from_conversation"
                and e.action.args.get("conversation_id") == self.group_conversation_id
                and e.action.args.get("user_id") == self.marco_id
                for e in log_entries
            )

            task_completed = calendar_edited and confirmation_sent and marco_removed

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            failed: list[str] = []
            if not proposal_found:
                failed.append("no proactive proposal found")
            if not calendar_edited:
                failed.append(
                    "task not completed: calendar event not edited to swap Marco out and user in"
                )
            if not confirmation_sent:
                failed.append(
                    "task not completed: no confirmation reply posted to the group chat"
                )
            if not marco_removed:
                failed.append(
                    "task not completed: Marco not removed from the group chat"
                )
            return ScenarioValidationResult(
                success=False, rationale="; ".join(failed)
            )

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
