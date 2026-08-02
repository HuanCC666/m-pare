"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import (
    AbstractEnvironment,
    Action,
    Event,
    EventRegisterer,
    EventType,
)

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
DEFAULT_LOCAL_ARMCHAIR_PHOTO_PATH = (
    Path(__file__).resolve().parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "estate_armchair_photo_confirm"
    / "armchair.jpg"
)


@register_scenario("estate_armchair_photo_confirm")
class EstateArmchairPhotoConfirm(PAREScenario):
    """Agent confirms a friend's estate-sale armchair photo matches the user's described wish and proposes replying yes plus adding a drop-off calendar event.

A friend (Priya) messages in their existing Messages thread: she found a "vintage green velvet armchair with turned wooden legs" at an estate sale, matching what the user said they wanted for their reading nook. She attaches a photo of the chair and says the sale will hold it until 5pm today for $120; if it looks right, the user should reply yes and she will grab it and drop it at the user's place Sunday afternoon around 2pm, and asks the user to add the drop-off to their calendar so they are home. The chair's actual appearance (green velvet upholstery, turned wooden legs) is only visible in the photo — the message just names the style. The assistant must:
1. Read the incoming message in Messages and download/inspect the attached chair photo via the sandbox file system.
2. Visually confirm the chair matches the described style (green velvet, turned wooden legs) so the reply is grounded in what was actually found.
3. Proactively propose replying yes to Priya and adding a Sunday 2pm "Armchair drop-off from Priya" calendar event.
4. After user acceptance, send the reply and create the calendar event.

The chair photo is seeded in SandboxLocalFileSystem (e.g., `/armchair.jpg`) and attached to the incoming Messages thread; the agent accesses it by reading the conversation, downloading the attachment, and viewing the image. Actionable specifics — the 5pm hold deadline, $120 price, Sunday 2pm drop-off, and the explicit asks to "reply yes" and "add the drop-off to your calendar" — come from the incoming message, while the image supplies the visual content (green velvet armchair with turned legs) that the reply's confirmation depends on and that cannot be known from the filename alone.

This scenario exercises messaging conversation read + attachment download + outbound reply, multimodal confirmation of a real-world object's appearance against a text description, and calendar event creation for a coordinated drop-off — a visual-match-then-confirm coordination flow rather than a calendar-location edit or an outbound photo share.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging: pre-existing 1:1 thread with Priya Sharma -------------------
        # The user and Priya already have a lived-in Messages thread. Priya's runtime
        # message (Step 3) delivering the armchair photo + drop-off ask is injected into
        # this same conversation, so the agent reads an existing thread rather than a new
        # one. Priya is seeded as a contact so the agent can resolve her user id later.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.messaging.add_users(["Priya Sharma"])
        priya_id = self.messaging.get_user_id("Priya Sharma")
        if priya_id is None:
            raise RuntimeError("Failed to resolve seeded messaging user id for Priya Sharma")
        self.priya_user_id = priya_id

        priya_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, priya_id],
            title="Priya Sharma",
        )
        # Baseline prior chat history (before start_time) so the thread feels lived-in
        # and establishes the user's reading-nook armchair wish that Priya references.
        prior_ts = self.start_time - 4 * 86_400  # four days before start_time
        priya_conversation.messages.append(
            MessageV2(
                sender_id=priya_id,
                content=(
                    "Still on the hunt for that reading-nook chair? What style were you "
                    "thinking again?"
                ),
                timestamp=prior_ts,
            )
        )
        priya_conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content=(
                    "Yes! I'd love a vintage green velvet armchair with turned wooden "
                    "legs — something comfy for the corner by the window."
                ),
                timestamp=prior_ts + 600,
            )
        )
        priya_conversation.messages.append(
            MessageV2(
                sender_id=priya_id,
                content="Got it — green velvet, turned legs, vintage vibe. I'll keep an eye out.",
                timestamp=prior_ts + 1200,
            )
        )
        priya_conversation.update_last_updated(prior_ts + 1200)
        self.messaging.add_conversation(priya_conversation)
        self.priya_conversation_id = priya_conversation.conversation_id

        # --- Calendar: baseline lived-in agenda (drop-off event is created in Step 3) -
        self.calendar = StatefulCalendarApp(name="Calendar")
        # One pre-existing event earlier on start_time day so the agenda is non-empty.
        self.calendar.add_calendar_event(
            title="Morning yoga",
            start_datetime="2025-11-18 07:30:00",
            end_datetime="2025-11-18 08:15:00",
            tag="personal",
            description="Living room flow",
        )

        # --- Visual asset: load armchair.jpg into the sandbox Files system ------------
        # The chair photo is attached to Priya's runtime Messages thread (Step 3) and
        # inspected by the agent via Files.display(...). The image supplies the visual
        # content (green velvet upholstery, turned wooden legs) that the reply's
        # confirmation depends on; the filename alone is not sufficient.
        local_photo_path = Path(
            os.getenv(
                "PARE_ESTATE_ARMCHAIR_PHOTO_LOCAL_PATH",
                str(DEFAULT_LOCAL_ARMCHAIR_PHOTO_PATH),
            )
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Armchair photo not found: {local_photo_path}. "
                f"Place armchair.jpg under {DEFAULT_LOCAL_ARMCHAIR_PHOTO_PATH.parent}."
            )
        with self.files.open("/armchair.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))
        self.armchair_photo_sandbox_path = "/armchair.jpg"

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

        # Precompute plain string ids (event objects must not be passed where str ids are expected).
        priya_conversation_id = self.priya_conversation_id
        priya_user_id = self.priya_user_id
        armchair_photo_sandbox_path = self.armchair_photo_sandbox_path

        with EventRegisterer.capture_mode():
            # --- Environment: Priya's incoming message with the estate-sale armchair photo ----
            # Exogenous trigger: Priya messages in the existing thread with the chair photo and
            # the 5pm-hold / $120 / Sunday-2pm-drop-off ask + "reply yes" + "add to your calendar".
            incoming_armchair_message_event = messaging_app.create_and_add_message(
                conversation_id=priya_conversation_id,
                sender_id=priya_user_id,
                content=(
                    "Found it! There's a vintage green velvet armchair with turned wooden legs at "
                    "the estate sale on Elm — looks just like the one you described for your reading "
                    "nook. Photo attached. They'll hold it until 5pm today for $120. If it looks right "
                    "to you, reply yes and I'll grab it and drop it at your place Sunday afternoon around "
                    "2pm. Please add the drop-off to your calendar so you're home!"
                ),
                attachment_path=armchair_photo_sandbox_path,
            ).delayed(5)

            # --- Oracle: read the Priya thread to see the new message + attachment metadata.
            # Motivation: the env message above ("Photo attached", "reply yes", "add the drop-off to
            # your calendar") is what the agent must read to recover the actionable asks.
            read_priya_thread_event = (
                messaging_app.read_conversation(
                    conversation_id=priya_conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(incoming_armchair_message_event, delay_seconds=2)
            )

            # --- Oracle: visually inspect the attached chair photo before confirming a match.
            # Motivation: the env message names the style ("green velvet armchair with turned wooden
            # legs") but the actual appearance is only visible in the attached image at
            # /armchair.jpg, so the agent must view it to ground the yes/no reply.
            view_armchair_photo_event = (
                files.display(path=armchair_photo_sandbox_path)
                .oracle()
                .depends_on(read_priya_thread_event, delay_seconds=1)
            )

            # --- Oracle: proactive proposal to the user citing the env cue and visual confirmation.
            # Motivation: grounded in incoming_armchair_message_event ("hold it until 5pm today for
            # $120", "drop it at your place Sunday afternoon around 2pm", "reply yes", "add the
            # drop-off to your calendar") and the visual confirmation from view_armchair_photo_event
            # that the chair is a green velvet armchair with turned wooden legs matching the wish.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Priya messaged in your thread: she found a vintage green velvet armchair with "
                        "turned wooden legs at the Elm estate sale, photo attached, held until 5pm today "
                        "for $120. I viewed the photo and it matches your reading-nook wish — green "
                        "velvet upholstery, padded back and armrests, turned wooden legs. She asked you "
                        "to reply yes and said she'll drop it at your place Sunday around 2pm. Want me "
                        "to reply yes to Priya and add a Sunday 2pm \"Armchair drop-off from Priya\" "
                        "calendar event so you're home?"
                    )
                )
                .oracle()
                .depends_on(view_armchair_photo_event, delay_seconds=2)
            )

            # --- Oracle: user accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please reply yes to Priya and add the Sunday 2pm drop-off event."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- Oracle (write, user-gated): reply yes in the Priya thread.
            # Motivation: the user accepted the proposal; the env cue explicitly asked to "reply yes".
            # Target conversation_id is the same Priya thread the agent just read (read_priya_thread_event).
            reply_yes_to_priya_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=priya_conversation_id,
                    content=(
                        "Yes! The chair in the photo is exactly the green velvet armchair with turned "
                        "legs I wanted. Grab it and drop it Sunday around 2pm — I'll add it to my "
                        "calendar. Thanks Priya!"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- Oracle (write, user-gated): create the Sunday 2pm drop-off calendar event.
            # Motivation: the user accepted the proposal; the env cue asked to "add the drop-off to
            # your calendar" for "Sunday afternoon around 2pm" at "your place". Sunday after the
            # 2025-11-18 start_time is 2025-11-23.
            create_dropoff_calendar_event = (
                calendar_app.add_calendar_event(
                    title="Armchair drop-off from Priya",
                    start_datetime="2025-11-23 14:00:00",
                    end_datetime="2025-11-23 15:00:00",
                    tag="personal",
                    description=(
                        "Priya dropping off the estate-sale green velvet armchair with turned wooden "
                        "legs she picked up for the reading nook."
                    ),
                    location="Home",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events: list[Event] = [
            incoming_armchair_message_event,
            read_priya_thread_event,
            view_armchair_photo_event,
            proposal_event,
            acceptance_event,
            reply_yes_to_priya_event,
            create_dropoff_calendar_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            priya_conversation_id = self.priya_conversation_id

            # Check 1 — Proposal: agent proactively offered help via the agent-user interface.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # Check 2 — Task: agent completed both promised writes (reply yes to Priya
            # AND create the Sunday 2pm drop-off calendar event). Folded into a single
            # task check — both writes must pass for task_completed to be True.
            reply_yes_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == priya_conversation_id
                for e in log_entries
            )

            dropoff_calendar_event_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "add_calendar_event"
                and e.action.args.get("title") == "Armchair drop-off from Priya"
                and e.action.args.get("start_datetime") == "2025-11-23 14:00:00"
                for e in log_entries
            )

            task_completed = reply_yes_found and dropoff_calendar_event_found

            success = proposal_found and task_completed

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append("no proactive proposal found")
                if not reply_yes_found:
                    failed_checks.append(
                        "task not completed: reply 'yes' to Priya's thread was not sent"
                    )
                if not dropoff_calendar_event_found:
                    failed_checks.append(
                        "task not completed: Sunday 2025-11-23 2pm 'Armchair drop-off from Priya' "
                        "calendar event was not created"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
