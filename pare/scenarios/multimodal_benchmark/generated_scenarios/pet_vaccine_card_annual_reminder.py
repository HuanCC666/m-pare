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
from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulEmailApp,
)
from pare.apps.reminder import StatefulReminderApp
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
    / "pet_vaccine_card_annual_reminder"
)


@register_scenario("pet_vaccine_card_annual_reminder")
class PetVaccineCardAnnualReminder(PAREScenario):
    """Agent reads a pet vaccination record card from an email attachment and sets up a recurring annual booster reminder.

A veterinary clinic emails the user a photo of their dog's vaccination record card (a local image asset seeded in Files and attached to the email). The card image shows the next booster vaccine name and due date as printed/handwritten text that can only be read by viewing the image; the email explicitly asks the owner to set a recurring annual reminder so future boosters are not missed, and points out that the upcoming due date is printed on the card.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the vaccination record card via Files to read the booster vaccine name and due date from the image, (3) proactively propose creating an annually repeating reminder for the booster using the date read from the card, and (4) after user acceptance, create the reminder with `due_datetime` set to the booster date and yearly repetition (`repetition_unit="year"`, `repetition_value=1`).

This scenario exercises multimodal grounding on a photo-like vaccination record card, cross-app coordination across Email + Files + Reminders, and creation of a recurring (annually repeating) reminder — all cued explicitly by the trigger email's request for ongoing follow-up tracking.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        # Files + Email share the same sandbox filesystem so that email
        # attachments can be resolved from seeded image bytes during Step 3.
        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the pet vaccination record card image (resolved local asset) into
        # the sandbox filesystem at /IMG_2416.jpg. The trigger email (sent in
        # Step 3) will attach this path; the agent inspects it via
        # files.display("/IMG_2416.jpg") to read the booster vaccine name and
        # due date printed on the card.
        local_vaccine_card_path = SCENARIO_ASSET_DIR / "IMG_2416.jpg"
        if not local_vaccine_card_path.exists():
            raise FileNotFoundError(
                f"Pet vaccination record card image not found: {local_vaccine_card_path}. "
                f"Place IMG_2416.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open("/IMG_2416.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_vaccine_card_path.read_bytes()))

        # Email id reused by Step 3 to send the trigger email and to let the
        # agent read it back by id.
        self.vaccine_email_id = "pet_vaccine_record_email"

        # Ground-truth booster facts read from the card image; kept on the
        # scenario for later validation reference (Step 4). The card shows the
        # next booster (Rabies) due on 2027-07-17 and the clinic explicitly
        # requests an annually repeating reminder.
        self.booster_vaccine_name = "Rabies"
        self.booster_due_date_str = "2027-07-17"
        self.booster_due_datetime_str = "2027-07-17 09:00:00"

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

        # Plain values used inside environment events (must not be Event objects).
        vaccine_email_id = self.vaccine_email_id
        booster_due_datetime_str = self.booster_due_datetime_str
        booster_vaccine_name = self.booster_vaccine_name
        # NOTE: The ReminderApp API (are/simulation/apps/reminder.py) only accepts
        # repetition_unit in {"second","minute","hour","day","week","month"}; "year"
        # is not supported. We model the requested annually repeating booster by
        # using repetition_unit="month" with repetition_value=12 (12 months == 1 year).
        annual_repetition_unit = "month"
        annual_repetition_value = 12

        with EventRegisterer.capture_mode():
            # ENV event: veterinary clinic emails the user a photo of the pet
            # vaccination record card, explicitly asking the owner to set up a
            # recurring annual reminder so future boosters are not missed, and
            # pointing out that the next booster due date is printed on the card.
            vaccine_email_event = email_app.send_email_to_user_with_id(
                email_id=vaccine_email_id,
                sender="care@greenfieldvetclinic.com",
                subject="Buddy's vaccination record card — please set an annual booster reminder",
                content=(
                    "Hi,\n\n"
                    "Buddy's checkup at Greenfield Veterinary Clinic is complete. We've attached a "
                    "photo of his vaccination record card. The card shows the next booster vaccine "
                    "name and the next booster due date.\n\n"
                    "Could you set up a recurring annual reminder so future boosters are not missed? "
                    "Please use the booster vaccine name and due date printed on the attached card "
                    "to schedule it.\n\n"
                    "Thanks,\nGreenfield Veterinary Clinic"
                ),
                attachment_paths=["/IMG_2416.jpg"],
            ).delayed(5)

            # ORACLE read: motivated by the incoming clinic email — the agent reads
            # the trigger email (with attachment) by id to access the vaccination
            # record card photo.
            read_vaccine_email_event = (
                email_app.get_email_by_id(email_id=vaccine_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(vaccine_email_event, delay_seconds=2)
            )

            # ORACLE visual inspection: the booster vaccine name and due date are
            # only readable from the card image — agent displays /IMG_2416.jpg via
            # Files to read "Rabies" and "2027-07-17" from the photo.
            view_vaccine_card_event = (
                files.display(path="/IMG_2416.jpg")
                .oracle()
                .depends_on(read_vaccine_email_event, delay_seconds=1)
            )

            # ORACLE proposal: grounded in the clinic email ("set up a recurring
            # annual reminder so future boosters are not missed" / "booster vaccine
            # name and due date printed on the attached card") and the visual
            # inspection of vaccine_email_event's attachment (card shows Rabies,
            # 2027-07-17). Agent proposes creating an annually repeating reminder.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read Greenfield Veterinary Clinic's email about Buddy's vaccination record "
                        "card and inspected the attached photo. The card shows the next booster "
                        "(Rabies) is due on 2027-07-17, and the clinic asked you to set up a "
                        "recurring annual reminder so future boosters are not missed. "
                        "Would you like me to create an annually repeating reminder for Buddy's "
                        "Rabies booster due on 2027-07-17?"
                    )
                )
                .oracle()
                .depends_on(view_vaccine_card_event, delay_seconds=2)
            )

            # USER acceptance of the proactive proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please create the recurring annual Rabies booster reminder for Buddy."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # ORACLE write (user-gated): create the annually repeating booster
            # reminder using the booster vaccine name and due date read from the
            # card image. Per the ReminderApp API, annual recurrence is modeled as
            # repetition_unit="month", repetition_value=12.
            create_reminder_event = (
                reminder_app.add_reminder(
                    title=f"Buddy's {booster_vaccine_name} booster",
                    due_datetime=booster_due_datetime_str,
                    description=(
                        f"Annual {booster_vaccine_name} booster for Buddy (Golden Retriever), per "
                        "Greenfield Veterinary Clinic vaccination record card. Recurs annually."
                    ),
                    repetition_unit=annual_repetition_unit,
                    repetition_value=annual_repetition_value,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            vaccine_email_event,
            read_vaccine_email_event,
            view_vaccine_card_event,
            proposal_event,
            acceptance_event,
            create_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            # Only consider agent-side actions for validation.
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Check 1 — Proposal: the proactive agent offered help to the user
            # via PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: after the proposal, the agent created the annually
            # recurring booster reminder using the booster due date read from the
            # vaccination card image. Annual recurrence is modeled via the
            # ReminderApp API as repetition_unit="month", repetition_value=12.
            # We assert on structural identifiers only (due_datetime matching
            # the seeded booster date, plus the annual-recurrence repetition
            # fields), never on free-form reminder text.
            expected_due = self.booster_due_datetime_str
            task_completed = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and str(e.action.args.get("due_datetime", "")).strip() == expected_due
                and str(e.action.args.get("repetition_unit", "")).strip() == "month"
                and e.action.args.get("repetition_value") == 12
                for e in agent_entries
            )

            success = proposal_found and task_completed

            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                else:
                    rationale = (
                        "task not completed: annually repeating booster reminder "
                        f"with due_datetime={expected_due}, repetition_unit=month, "
                        "repetition_value=12 not created"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
