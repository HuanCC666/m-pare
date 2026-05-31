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
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
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

    DEFAULT_LOCAL_WHITEBOARD_PHOTO_PATH = (
        Path(__file__).parent / "assets" / "whiteboard_photo_minutes_todo_suggestion" / "meeting_whiteboard_photo.jpg"
    )

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
                f"Place meeting_whiteboard_photo.jpg under {self.DEFAULT_LOCAL_WHITEBOARD_PHOTO_PATH.parent}."
            )
        with self.files.open("/meeting_whiteboard_photo.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_whiteboard_path.read_bytes()))

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
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.whiteboard_email_id,
                sender="teammate@company.com",
                subject="Whiteboard snapshot from today's planning",
                content=(
                    "Hey — grabbed a photo of the board after we wrapped earlier. "
                    "Can you turn the scribbles into something we can actually follow ? "
                    "A short write-up and a few reminders for the dates would help a lot. "
                    "Pic attached, sorry if my handwriting is awful."
                ),
                attachment_paths=["/meeting_whiteboard_photo.jpg"],
            ).delayed(7)

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

    def validate(
        self,
        env: AbstractEnvironment,
    ) -> ScenarioValidationResult:
        """Validate whiteboard photo viewing, note/reminder grounding, proposal, and follow-up writes."""
        try:
            log_entries = env.event_log.list_view()

            allow_any_event_type = bool(getattr(env, "oracle_mode", False))

            photo_visual_input_found = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any_event_type,
                image_path="/meeting_whiteboard_photo.jpg",
                email_id=self.whiteboard_email_id,
            )

            minutes_or_followups_grounded_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and (
                    (
                        e.action.class_name == "StatefulNotesApp"
                        and e.action.function_name == "create_note"
                        and (
                            "work" in str(e.action.args).lower()
                            or "whiteboard" in str(e.action.args).lower()
                            or "meeting" in str(e.action.args).lower()
                            or "planning" in str(e.action.args).lower()
                        )
                    )
                    or (
                        e.action.class_name == "StatefulReminderApp"
                        and e.action.function_name == "add_reminder"
                        and bool(str(e.action.args.get("due_datetime", "")).strip())
                    )
                )
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            structured_note_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulNotesApp"
                and e.action.function_name == "create_note"
                for e in log_entries
            )

            reminders_created_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                for e in log_entries
            )

            note_and_reminder_writes_found = structured_note_found and reminders_created_found

            success = (
                photo_visual_input_found
                and minutes_or_followups_grounded_found
                and proposal_found
                and note_and_reminder_writes_found
            )

            if not success:
                failed_checks: list[str] = []

                #
                # Failure analysis
                #

                if not photo_visual_input_found:
                    failed_checks.append(
                        "agent never accessed the meeting whiteboard photo (no Files read of "
                        "/meeting_whiteboard_photo.jpg and no Email read/download for the inbox message "
                        "with the attachment)"
                    )

                if photo_visual_input_found and not minutes_or_followups_grounded_found:
                    failed_checks.append(
                        "agent viewed the whiteboard but failed to ground meeting notes or dated follow-up reminders"
                    )

                if minutes_or_followups_grounded_found and not proposal_found:
                    failed_checks.append(
                        "agent drafted notes or reminders but failed to proactively propose creating them for the user"
                    )

                if not note_and_reminder_writes_found:
                    failed_checks.append(
                        "agent did not complete both required writes: structured meeting note and at least one reminder"
                    )

                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
