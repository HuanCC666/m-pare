"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from are.simulation.apps.email_client import Email, EmailFolderName
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, Event, EventRegisterer, EventType

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
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"
# Resolved visual asset for this scenario (local asset provider, manifest-backed).
APPOINTMENT_CARD_LOCAL_PATH = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "plumber_reschedule_card_update"
    / "IMG_2420.jpg"
)
APPOINTMENT_CARD_SANDBOX_PATH = "/IMG_2420.jpg"


@register_scenario("plumber_reschedule_card_update")
class PlumberRescheduleCardUpdate(PAREScenario):
    """Agent updates an existing repair-appointment reminder after a reschedule email that includes an appointment-card photo.

A plumbing company emails the user: their previously scheduled kitchen faucet repair needs to move to a new slot. The message carries an attached photo of the updated appointment card (a local image asset seeded in Files and attached to the email) and explicitly asks the user to update their existing reminder to the new date and time shown on the card, then reply to confirm. A prior reminder titled "Kitchen faucet repair appointment" is already present in the Reminders app from the original booking, so the change is an update rather than a new reminder.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the appointment-card photo to read the new date and time window from the card, (3) list existing reminders to locate the current "Kitchen faucet repair appointment" reminder, (4) proactively propose updating that reminder to the new slot and replying to the plumber, and (5) after user acceptance, update the reminder's due datetime to the slot read from the card and reply to the email confirming the new appointment.

This scenario exercises multimodal grounding on a photo-like appointment card, cross-app coordination across Email + Files + Reminders, updating an existing reminder (rather than creating a new one) after listing reminders, and a reply-style outward action to a remote party — all cued explicitly by the trigger email.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email + Reminders apps. Email's internal_fs is wired to the shared sandbox
        # so runtime attachment_paths in build_events_flow can resolve to seeded bytes.
        self.email = StatefulEmailApp(name="Emails")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the appointment-card image asset into the sandbox so the Step 3 trigger
        # email can attach it and the agent can later display/inspect it.
        local_card_path = APPOINTMENT_CARD_LOCAL_PATH
        if not local_card_path.exists():
            raise FileNotFoundError(
                f"Appointment card image not found: {local_card_path}. "
                f"Place IMG_2420.jpg under {local_card_path.parent}."
            )
        with self.files.open(APPOINTMENT_CARD_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_card_path.read_bytes()))
        self.appointment_card_sandbox_path = APPOINTMENT_CARD_SANDBOX_PATH

        # Baseline reminder: the ORIGINAL kitchen faucet repair appointment from the prior
        # booking. The Step 3 trigger email will ask the agent to UPDATE this reminder to
        # the new slot shown on the appointment-card photo (Thursday July 23, 2026,
        # 9:00 AM - 11:00 AM). The original slot is a different, earlier window so the
        # update is observable at runtime.
        original_appointment_dt = datetime(2026, 7, 21, 14, 0, 0, tzinfo=UTC)
        self.existing_reminder_id = self.reminder.add_reminder(
            title="Kitchen faucet repair appointment",
            due_datetime=original_appointment_dt.strftime("%Y-%m-%d %H:%M:%S"),
            description=(
                "Kitchen faucet repair with A-1 Plumbing. Original slot: Tuesday, "
                "July 21, 2026, 2:00 PM - 4:00 PM. Contact: service@a1plumbing.com."
            ),
        )

        # Baseline email history: the plumber's ORIGINAL confirmation email, sent before
        # start_time. This establishes the prior appointment and the existing reminder's
        # provenance. The reschedule trigger email arrives in Step 3, not here.
        self.original_email_id = "plumber_original_appointment_email"
        self.reschedule_email_id = "plumber_reschedule_email"
        prior_confirmation = Email(
            email_id=self.original_email_id,
            sender="service@a1plumbing.com",
            recipients=[self.email.user_email],
            subject="Confirmation: Kitchen faucet repair appointment",
            content=(
                "Hi John,\n\n"
                "This confirms your kitchen faucet repair appointment with A-1 Plumbing.\n"
                "Original slot: Tuesday, July 21, 2026, 2:00 PM - 4:00 PM.\n\n"
                "Please make sure someone is home during the service window. "
                "If you ever need to reschedule, just reply to this email.\n\n"
                "Thanks,\nA-1 Plumbing"
            ),
            timestamp=datetime(2025, 11, 17, 15, 0, 0, tzinfo=UTC).timestamp(),
            is_read=True,
        )
        self.email.folders[EmailFolderName.INBOX].add_email(prior_confirmation)

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
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        email_app = self.get_typed_app(StatefulEmailApp, "Emails")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        # Pre-computed constants grounded in the seeded appointment-card asset
        # (must match the card image text the agent inspects at runtime).
        new_appointment_due_datetime = "2026-07-23 09:00:00"  # Thursday July 23, 2026, 9:00 AM UTC
        new_appointment_description = (
            "Kitchen faucet repair with A-1 Plumbing. Updated slot: Thursday, "
            "July 23, 2026, 9:00 AM - 11:00 AM. Contact: service@a1plumbing.com."
        )

        with EventRegisterer.capture_mode():
            # ENV: A-1 Plumbing sends the reschedule email carrying the appointment-card photo.
            # Notification template "send_email_to_user_with_id" exists for both user/agent streams.
            reschedule_email_event = email_app.send_email_to_user_with_id(
                email_id=self.reschedule_email_id,
                sender="service@a1plumbing.com",
                subject="Reschedule: Kitchen faucet repair appointment",
                content=(
                    "Hi John,\n\n"
                    "We need to move your kitchen faucet repair appointment. The new slot is "
                    "shown on the attached appointment card photo. Please update your existing "
                    "reminder to the new date and time shown on the card, and reply to this "
                    "email to confirm the new appointment.\n\n"
                    "Thanks,\nA-1 Plumbing"
                ),
                attachment_paths=[self.appointment_card_sandbox_path],
            ).delayed(5)

            # ORACLE: read the incoming reschedule email to surface its attachment and body
            # (motivated by the env email event `reschedule_email_event` subject "Reschedule: Kitchen faucet repair appointment").
            read_email_event = (
                email_app.get_email_by_id(email_id=self.reschedule_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(reschedule_email_event, delay_seconds=2)
            )

            # ORACLE: inspect the attached appointment-card photo to read the new date/time window
            # (motivated by the attachment exposed by `read_email_event`; email body says "new slot is shown on the attached appointment card photo").
            view_card_event = (
                files.display(path=self.appointment_card_sandbox_path)
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            # ORACLE: list existing reminders to locate the current "Kitchen faucet repair appointment" reminder
            # (motivated by `reschedule_email_event` text: "update your existing reminder to the new date and time shown on the card").
            list_reminders_event = (
                reminder_app.get_all_reminders()
                .oracle()
                .depends_on(reschedule_email_event, delay_seconds=1)
            )

            # ORACLE proposal: cite the card's new slot (read via `view_card_event`) and the existing reminder (found via `list_reminders_event`),
            # then ask permission to update that reminder and reply to the plumber.
            # Grounded in `reschedule_email_event` ("reply to this email to confirm") and card visual evidence.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "A-1 Plumbing emailed to reschedule your kitchen faucet repair. "
                        "I read the attached appointment card: the new slot is Thursday, July 23, 2026, "
                        "9:00 AM - 11:00 AM. I also found your existing \"Kitchen faucet repair appointment\" "
                        "reminder in Reminders. Want me to update that reminder to the new slot and reply to "
                        "service@a1plumbing.com confirming the new appointment?"
                    )
                )
                .oracle()
                .depends_on([view_card_event, list_reminders_event], delay_seconds=2)
            )

            # USER accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please update the reminder to the new slot and reply to confirm."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # ORACLE write: update the existing reminder to the new slot read from the card.
            # `self.existing_reminder_id` is the ID revealed by `list_reminders_event` for the matching reminder title.
            # User-gated via `acceptance_event`.
            update_reminder_event = (
                reminder_app.update_reminder(
                    reminder_id=self.existing_reminder_id,
                    title="Kitchen faucet repair appointment",
                    description=new_appointment_description,
                    due_datetime=new_appointment_due_datetime,
                    repetition_unit=None,
                    repetition_value=None,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # ORACLE write: reply to the plumber confirming the new appointment.
            # Grounded in `reschedule_email_event` ("reply to this email to confirm"); user-gated via `acceptance_event`.
            reply_email_event = (
                email_app.reply_to_email(
                    email_id=self.reschedule_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi,\n\n"
                        "Thanks for the reschedule. I can confirm the new appointment on "
                        "Thursday, July 23, 2026, 9:00 AM - 11:00 AM. I've updated my reminder accordingly.\n\n"
                        "Best,\nJohn"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

        self.events: list[Event] = [
            reschedule_email_event,
            read_email_event,
            view_card_event,
            list_reminders_event,
            proposal_event,
            acceptance_event,
            update_reminder_event,
            reply_email_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            agent_entries = [
                e for e in env.event_log.list_view() if e.event_type == EventType.AGENT
            ]

            # Check 1 — Proposal: agent proactively offered help to the user.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: update existing reminder to the card's new slot AND reply
            # to the plumber's reschedule email. Free-form reply text is not constrained.
            update_reminder_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "update_reminder"
                and bool(e.action.args.get("reminder_id"))
                and "2026-07-23" in str(e.action.args.get("due_datetime", ""))
                for e in agent_entries
            )
            reply_email_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "reply_to_email"
                and str(e.action.args.get("email_id", "")) == self.reschedule_email_id
                for e in agent_entries
            )
            task_completed = update_reminder_found and reply_email_found

            if proposal_found and task_completed:
                return ScenarioValidationResult(success=True)

            failed_checks: list[str] = []
            if not proposal_found:
                failed_checks.append("no proactive proposal found")
            if not task_completed:
                missing_task_parts: list[str] = []
                if not update_reminder_found:
                    missing_task_parts.append(
                        "reminder not updated to 2026-07-23 via StatefulReminderApp.update_reminder"
                    )
                if not reply_email_found:
                    missing_task_parts.append(
                        "no StatefulEmailApp.reply_to_email to the reschedule email id"
                    )
                failed_checks.append("task not completed: " + "; ".join(missing_task_parts))
            return ScenarioValidationResult(
                success=False,
                rationale="; ".join(failed_checks),
            )

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
