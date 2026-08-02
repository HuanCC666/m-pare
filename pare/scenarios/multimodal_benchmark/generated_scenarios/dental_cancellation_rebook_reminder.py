"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventType, EventRegisterer

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulEmailApp,
    StatefulReminderApp,
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
    / "dental_cancellation_rebook_reminder"
)
DENTAL_CANCELLATION_CARD_FILENAME = "IMG_2417.jpg"
DENTAL_CANCELLATION_CARD_SANDBOX_PATH = "/IMG_2417.jpg"
TRIGGER_EMAIL_ID = "email-brightsmile-dental-cancellation-card"


@register_scenario("dental_cancellation_rebook_reminder")
class DentalCancellationRebookReminder(PAREScenario):
    """A dental clinic emails the user a photo of a printed appointment-cancellation card and asks them to remove the now-canceled appointment from their reminders and set a new reminder to call the clinic to rebook, using the next availability date printed on the card.

The cancellation email from "Brightsmile Dental" arrives with an attached photo of a printed cancellation card (a local image asset seeded in Files and attached to the email). The card shows two printed dates that can only be read by viewing the image: the original appointment slot ("July 24, 2025 at 14:00") and the clinic's next availability ("August 18, 2025"). The email body explicitly asks the owner to delete the canceled appointment from their reminders and create a new reminder to call the clinic to rebook on the next availability date shown on the card. A pre-existing reminder titled "Dental cleaning — July 24" is already present in the Reminders app from the original booking, so the workflow is a delete-then-create rather than an update or a fresh create.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the cancellation-card photo via Files to read both the original appointment date and the next availability date from the image, (3) list existing reminders to locate the "Dental cleaning — July 24" reminder, (4) proactively propose deleting that reminder and creating a new "Call Brightsmile Dental to rebook" reminder due on August 18, 2025, and (5) after user acceptance, delete the old reminder and create the new rebook reminder using the date read from the card.

This scenario exercises multimodal grounding on a photo-like cancellation card where the image is the sole source for both the delete-target identifier (the original appointment date, used to match against an existing reminder) and the new reminder's due date, cross-app coordination across Email + Files + Reminders, and the novel combination of `delete_reminder` (removing a now-invalid commitment) with `add_reminder` (tracking the rebook call) — all cued explicitly by the trigger email's request to both remove the old appointment and track the rebook.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email app: connect its internal_fs to the sandbox so attachments can be
        # read from Files. The trigger cancellation-card email itself is delivered
        # as an early environment event in build_events_flow() (Step 3), not here.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        # Reminders app: seed only pre-existing baseline state here. The original
        # "Dental cleaning — July 24" reminder predates the cancellation email and
        # is the delete-target the agent must locate via list_reminders.
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the cancellation-card photo into the sandbox Files so that Step 3
        # can attach it to the trigger email and the agent can display() it.
        local_card_path = SCENARIO_ASSET_DIR / DENTAL_CANCELLATION_CARD_FILENAME
        if not local_card_path.exists():
            raise FileNotFoundError(
                f"Dental cancellation card photo not found: {local_card_path}. "
                f"Place {DENTAL_CANCELLATION_CARD_FILENAME} under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(DENTAL_CANCELLATION_CARD_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_card_path.read_bytes()))

        # Pre-existing reminder from the original booking (before start_time).
        # The cancellation card's printed "Original: July 24, 2025 at 14:00" slot
        # is what the agent must match against this reminder's title/date to
        # identify the delete target. The new "Call Brightsmile Dental to rebook"
        # reminder is created at runtime by the agent after user acceptance.
        self.dental_cleaning_reminder_id = self.reminder.add_reminder(
            title="Dental cleaning — July 24",
            due_datetime="2025-07-24 14:00:00",
            description=(
                "Routine dental cleaning at Brightsmile Dental. "
                "Original appointment slot July 24, 2025 at 14:00."
            ),
            repetition_unit=None,
            repetition_value=1,
        )

        # Register all apps here in self.apps
        self.apps = [
            self.agent_ui,
            self.system_app,
            self.files,
            self.email,
            self.reminder,
        ]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Brightsmile Dental sends the cancellation-card email with the printed
            # card photo attached. The body explicitly asks the owner to remove the
            # canceled July 24 appointment from reminders and create a new rebook
            # reminder for the next availability date printed on the card.
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=TRIGGER_EMAIL_ID,
                sender="front.desk@brightsmile-dental.com",
                subject="Your July 24 appointment was cancelled — please rebook",
                content=(
                    "Hi,\n\n"
                    "We had to cancel your dental cleaning originally booked for July 24, 2025 at 14:00. "
                    "A scan of the printed cancellation card is attached — it shows both your original "
                    "slot and our next available date.\n\n"
                    "Could you please remove the now-canceled July 24 appointment from your reminders, "
                    "and set a new reminder to call us to rebook on the next availability date printed "
                    "on the card?\n\n"
                    "Thanks,\nBrightsmile Dental Front Desk"
                ),
                attachment_paths=[DENTAL_CANCELLATION_CARD_SANDBOX_PATH],
            ).delayed(5)

            # --- ORACLE EVENTS ---
            # Motivation: the cancellation email (inject_email_event) is an unread
            # inbox message the agent must open to see its request and attachment.
            read_email_event = (
                email_app.get_email_by_id(email_id=TRIGGER_EMAIL_ID, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            # Motivation: the email attachment is a printed cancellation card whose
            # dates (original appointment + next availability) are only readable by
            # viewing the image; the email body refers to "the next availability
            # date printed on the card".
            view_card_event = (
                files.display(path=DENTAL_CANCELLATION_CARD_SANDBOX_PATH)
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            # Motivation: the email asks the owner to "remove the now-canceled
            # July 24 appointment from your reminders"; the agent must list existing
            # reminders to locate the matching "Dental cleaning — July 24" entry and
            # retrieve its reminder_id for the delete step.
            list_reminders_event = (
                reminder_app.get_all_reminders()
                .oracle()
                .depends_on(view_card_event, delay_seconds=1)
            )

            # Motivation: grounded in inject_email_event ("remove the now-canceled
            # July 24 appointment from your reminders, and set a new reminder to
            # call us to rebook on the next availability date printed on the card")
            # and the card image (view_card_event) which shows the next availability
            # date, plus list_reminders_event which reveals the matching existing
            # reminder. Agent asks permission before performing the delete+create writes.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read Brightsmile Dental's cancellation email and inspected the attached "
                        "cancellation card. The card shows your original slot (July 24, 2025 at 14:00) "
                        "and the clinic's next availability (August 18, 2025). I also found your existing "
                        "\"Dental cleaning — July 24\" reminder. Would you like me to delete that reminder "
                        "and create a new \"Call Brightsmile Dental to rebook\" reminder due on August 18, 2025?"
                    )
                )
                .oracle()
                .depends_on([view_card_event, list_reminders_event], delay_seconds=1)
            )

            # User accepts the proposal (gates the write actions).
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please delete the old reminder and add the rebook one."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # Motivation: acceptance_event gated this write; reminder_id was revealed
            # by list_reminders_event (the "Dental cleaning — July 24" entry), and the
            # email (inject_email_event) explicitly asked to remove the canceled July
            # 24 appointment from reminders.
            delete_reminder_event = (
                reminder_app.delete_reminder(reminder_id=self.dental_cleaning_reminder_id)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # Motivation: acceptance_event gated this write; the email
            # (inject_email_event) asked to "set a new reminder to call us to rebook
            # on the next availability date printed on the card" and view_card_event
            # revealed that next availability date (August 18, 2025).
            create_rebook_reminder_event = (
                reminder_app.add_reminder(
                    title="Call Brightsmile Dental to rebook",
                    due_datetime="2025-08-18 09:00:00",
                    description=(
                        "Call Brightsmile Dental to rebook the cancelled July 24, 2025 14:00 cleaning. "
                        "Next availability shown on cancellation card: August 18, 2025."
                    ),
                    repetition_unit=None,
                    repetition_value=1,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            inject_email_event,
            read_email_event,
            view_card_event,
            list_reminders_event,
            proposal_event,
            acceptance_event,
            delete_reminder_event,
            create_rebook_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Check 1 — Proposal: agent proactively offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                e.action is not None
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: agent completed the promised delete + create rebook
            # reminder workflow. Both required writes must appear among AGENT events:
            #   (a) delete_reminder targeting the seeded "Dental cleaning — July 24"
            #       reminder (self.dental_cleaning_reminder_id), and
            #   (b) add_reminder for the rebook call due on the next availability
            #       date read from the card (August 18, 2025 => "2025-08-18 09:00:00").
            delete_done = False
            create_done = False
            for e in agent_entries:
                action = e.action
                if action is None:
                    continue
                if (
                    action.class_name == "StatefulReminderApp"
                    and action.function_name == "delete_reminder"
                ):
                    args = action.resolved_args or action.args
                    if args.get("reminder_id") == self.dental_cleaning_reminder_id:
                        delete_done = True
                elif (
                    action.class_name == "StatefulReminderApp"
                    and action.function_name == "add_reminder"
                ):
                    args = action.resolved_args or action.args
                    title = args.get("title")
                    due = args.get("due_datetime")
                    if (
                        title is not None
                        and "rebook" in str(title).lower()
                        and "brightsmile" in str(title).lower()
                        and due == "2025-08-18 09:00:00"
                    ):
                        create_done = True
            task_completed = delete_done and create_done

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)
            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not delete_done:
                rationale = (
                    "task not completed: original 'Dental cleaning — July 24' "
                    "reminder was not deleted"
                )
            else:
                rationale = (
                    "task not completed: 'Call Brightsmile Dental to rebook' "
                    "reminder due 2025-08-18 09:00:00 was not created"
                )
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
