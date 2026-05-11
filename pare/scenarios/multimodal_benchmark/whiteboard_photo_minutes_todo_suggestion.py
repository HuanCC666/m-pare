"""Scenario: Agent structures whiteboard photo into meeting notes and proactive reminders."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulEmailApp
from pare.apps.note import StatefulNotesApp
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario


@register_scenario("whiteboard_photo_minutes_todo_suggestion")
class WhiteboardPhotoMinutesTodoSuggestion(PAREScenario):
    """Agent converts whiteboard action items into a note and reminder workflow.

    A meeting whiteboard photo arrives as an email attachment. The assistant must:
    1. Read the message and inspect the image first.
    2. Extract action items, owners, and due dates from the whiteboard content.
    3. Before any write/action operation, ask one proactive accept/reject permission question.
    4. If accepted, create a structured Work note and the follow-up reminders directly.
    """

    start_time = datetime(2025, 11, 24, 16, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_WHITEBOARD_PHOTO_PATH = Path(__file__).parent / "assets" / "meeting_whiteboard_photo.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed the whiteboard email, note, and reminder fixtures."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.note = StatefulNotesApp(name="Notes")
        self.reminder = StatefulReminderApp(name="Reminders")

        local_whiteboard_path = Path(
            os.getenv("PARE_WHITEBOARD_PHOTO_LOCAL_PATH", str(self.DEFAULT_LOCAL_WHITEBOARD_PHOTO_PATH))
        )
        if not local_whiteboard_path.exists():
            raise FileNotFoundError(
                f"Whiteboard photo not found: {local_whiteboard_path}. "
                "Set PARE_WHITEBOARD_PHOTO_LOCAL_PATH or place the image at "
                f"{self.DEFAULT_LOCAL_WHITEBOARD_PHOTO_PATH}."
            )
        with self.files.open("/meeting_whiteboard_photo.jpg", "wb") as f:
            f.write(local_whiteboard_path.read_bytes())

        self.whiteboard_email_id = "meeting_whiteboard_email"
        base_day = datetime.fromtimestamp(self.start_time, tz=UTC)
        task_templates = [
            ("API contract draft", "Alex", 3),
            ("QA regression checklist", "Priya", 4),
            ("Release candidate demo prep", "Marco", 7),
        ]
        self.oracle_tasks: list[dict[str, str]] = []
        for task_title, owner, day_offset in task_templates:
            due_dt = (base_day + timedelta(days=day_offset)).replace(hour=10, minute=0, second=0)
            self.oracle_tasks.append({
                "title": task_title,
                "owner": owner,
                "due_human": due_dt.strftime("%b %d").replace(" 0", " "),
                "due_datetime": due_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })
        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.note, self.reminder]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for whiteboard follow-up."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        note_app = self.get_typed_app(StatefulNotesApp, "Notes")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        with EventRegisterer.capture_mode():
            inject_email_event = (
                email_app.send_email_to_user_with_id(
                    email_id=self.whiteboard_email_id,
                    sender="teammate@company.com",
                    subject="Whiteboard snapshot from today's planning",
                    content="Sharing the whiteboard photo from the meeting for your reference.",
                    attachment_paths=["/meeting_whiteboard_photo.jpg"],
                )
                .oracle()
                .delayed(7)
            )

            read_email_event = (
                email_app.get_email_by_id(email_id=self.whiteboard_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_photo_event = (
                files_app.display(path="/meeting_whiteboard_photo.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content="I extracted action items from the whiteboard and can create notes/reminders now. Proceed?"
                )
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please create those reminders.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            create_note_event = (
                note_app.create_note(
                    folder="Work",
                    title="Planning Meeting Action Items",
                    content="Summarized action items from whiteboard photo.",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            create_reminder_event = (
                reminder_app.add_reminder(
                    title="Follow up whiteboard items",
                    due_datetime=self.oracle_tasks[0]["due_datetime"],
                    description="Track execution of extracted action items.",
                )
                .oracle()
                .depends_on(create_note_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_photo_event,
            proposal_event,
            acceptance_event,
            create_note_event,
            create_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate simple flow correctness for whiteboard follow-up."""
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

            structured_note_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulNotesApp"
                and e.action.function_name == "create_note"
                for e in log_entries
            )

            proactive_proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            reminders_created_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                for e in log_entries
            )

            success = viewed_image_found and structured_note_found and proactive_proposal_found
            if not success:
                failed_checks = []
                if not viewed_image_found:
                    failed_checks.append("agent did not inspect whiteboard image")
                if not structured_note_found:
                    failed_checks.append("agent did not create structured notes from action items")
                if not proactive_proposal_found:
                    failed_checks.append("agent did not proactively suggest reminder creation")
                if not reminders_created_found:
                    failed_checks.append("(info) agent did not create reminders")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
