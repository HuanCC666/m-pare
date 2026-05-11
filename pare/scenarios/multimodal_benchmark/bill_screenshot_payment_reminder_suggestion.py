"""Scenario: Agent extracts bill details from screenshot and proposes reminder creation."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulEmailApp
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario


@register_scenario("bill_screenshot_payment_reminder_suggestion")
class BillScreenshotPaymentReminderSuggestion(PAREScenario):
    """Agent extracts bill details from screenshot and handles reminder creation.

    A utility/credit-card bill screenshot arrives via email attachment. The assistant must:
    1. Read the message and inspect the image first.
    2. Extract amount and due date from the screenshot.
    3. Ask one proactive accept/reject permission question before creating a reminder.
    4. If accepted, create the reminder directly.

    Constraints:
    - Image reading/browsing can happen before permission.
    - Do not ask for extra details.
    - User responses should stay in accept/reject style.
    """

    start_time = datetime(2025, 11, 20, 19, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_BILL_IMAGE_PATH = Path(__file__).parent / "assets" / "utility_bill_screenshot.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed the bill screenshot + email/reminder fixtures."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.reminder = StatefulReminderApp(name="Reminders")

        local_bill_path = Path(os.getenv("PARE_BILL_SCREENSHOT_LOCAL_PATH", str(self.DEFAULT_LOCAL_BILL_IMAGE_PATH)))
        if not local_bill_path.exists():
            raise FileNotFoundError(
                f"Bill screenshot not found: {local_bill_path}. "
                "Set PARE_BILL_SCREENSHOT_LOCAL_PATH or place the image at "
                f"{self.DEFAULT_LOCAL_BILL_IMAGE_PATH}."
            )
        with self.files.open("/utility_bill_screenshot.jpg", "wb") as f:
            f.write(local_bill_path.read_bytes())

        due_base = datetime.fromtimestamp(self.start_time, tz=UTC) + timedelta(days=8)
        self.reminder_due_datetime = due_base.replace(hour=9, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
        self.bill_email_id = "monthly_bill_screenshot_email"
        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.reminder]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for multimodal reminder assistance."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        with EventRegisterer.capture_mode():
            inject_email_event = (
                email_app.send_email_to_user_with_id(
                    email_id=self.bill_email_id,
                    sender="billing@city-utilities.com",
                    subject="Your latest bill statement",
                    content="Please see the attached bill screenshot from today. I am in transit now.",
                    attachment_paths=["/utility_bill_screenshot.jpg"],
                )
                .oracle()
                .delayed(8)
            )

            read_email_event = (
                email_app.get_email_by_id(email_id=self.bill_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_bill_event = (
                files_app.display(path="/utility_bill_screenshot.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content="I reviewed the bill screenshot and can create a payment reminder now. Proceed?"
                )
                .oracle()
                .depends_on(view_bill_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please create the reminder.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            create_reminder_event = (
                reminder_app.add_reminder(
                    title="Pay bill reminder",
                    due_datetime=self.reminder_due_datetime,
                    description="Pay the latest bill before due date.",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_bill_event,
            proposal_event,
            acceptance_event,
            create_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate that the agent viewed the bill image, proposed, and saved a reminder."""
        try:
            log_entries = env.event_log.list_view()

            allow_any_event_type = bool(getattr(env, "oracle_mode", False))
            viewed_image_found = any(
                (allow_any_event_type or e.event_type == EventType.AGENT)
                and isinstance(e.action, Action)
                and e.action.class_name == "SandboxLocalFileSystem"
                and e.action.function_name in {"display", "cat", "read_document"}
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            reminder_saved_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and bool(str(e.action.args.get("due_datetime", "")).strip())
                for e in log_entries
            )

            success = viewed_image_found and proposal_found and reminder_saved_found
            if not success:
                failed_checks = []
                if not viewed_image_found:
                    failed_checks.append("agent did not open the bill image")
                if not proposal_found:
                    failed_checks.append("agent did not send a proactive reminder proposal")
                if not reminder_saved_found:
                    failed_checks.append("agent did not save a valid payment reminder")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
