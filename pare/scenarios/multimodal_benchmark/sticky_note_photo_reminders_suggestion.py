"""Scenario: Agent converts a sticky-note attachment on a Note into three reminders."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface
from pare.apps.note import StatefulNotesApp
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "sticky_note_photo_reminders_suggestion"
_STICKY_ASSET_IMAGE = _ASSETS / "planning_sticky_note.jpg"
_STICKY_PATH = "/planning_sticky_note.jpg"


@register_scenario("sticky_note_photo_reminders_suggestion")
class StickyNotePhotoRemindersSuggestion(PAREScenario):
    """Agent turns a sticky-note attachment into three dated reminders.

    The user attaches a sprint-plan sticky-note photo to an Inbox note. Task titles,
    owners, and due dates appear only on the image. The assistant must:
    1. Read the note and inspect the attachment (vision).
    2. Ask one proactive accept/reject question before any reminder writes.
    3. If accepted, create reminders for each extracted due date.

    Constraints:
    - Proactive permission before reminder writes.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Reminder text does not carry the task/date details (those are read from the image).
    """

    start_time = datetime(2025, 11, 24, 16, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed note shell, sticky attachment file, reminder trigger, and oracle due datetimes."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.note = StatefulNotesApp(name="Notes")
        self.note.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        if not _STICKY_ASSET_IMAGE.exists():
            raise FileNotFoundError(
                f"Sticky note image not found: {_STICKY_ASSET_IMAGE}. Place planning_sticky_note.jpg under {_ASSETS}."
            )
        with self.files.open(_STICKY_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(_STICKY_ASSET_IMAGE.read_bytes()))

        self.trigger_note_id = self.note.create_note_with_time(
            folder="Inbox",
            title="Sprint plan sticky — set reminders",
            content="Photo of our sprint sticky note.",
            created_at="2025-11-24 15:50:00",
            updated_at="2025-11-24 15:50:00",
        )

        self.reminder.add_reminder(
            title="Process sprint sticky note in Inbox",
            due_datetime="2025-11-24 16:00:10",
            description="Open 'Sprint plan sticky — set reminders' and follow the attachment.",
        )

        self.oracle_tasks: list[dict[str, str]] = [
            {"title": "API contract draft", "owner": "Alex", "due_datetime": "2025-11-27 10:00:00"},
            {"title": "QA regression checklist", "owner": "Priya", "due_datetime": "2025-11-28 10:00:00"},
            {
                "title": "Release candidate demo prep",
                "owner": "Marco",
                "due_datetime": "2025-12-01 10:00:00",
            },
        ]

        self.apps = [self.agent_ui, self.system_app, self.files, self.note, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: read note → view sticky attachment → propose → three reminders."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        note_app = self.get_typed_app(StatefulNotesApp, "Notes")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        # Seed attachment after state init but before capture to avoid bytes in initial-state JSON.
        note_app.add_attachment_to_note(note_id=self.trigger_note_id, attachment_path=_STICKY_PATH)

        with EventRegisterer.capture_mode():
            read_note_event = note_app.get_note_by_id(note_id=self.trigger_note_id).oracle().delayed(8)

            view_sticky_event = (
                files_app.display(path=_STICKY_PATH).oracle().depends_on(read_note_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I inspected the sprint sticky-note attachment and extracted three tasks with due dates "
                        "(API contract draft — Thu Nov 27; QA regression checklist — Fri Nov 28; "
                        "Release candidate demo prep — Mon Dec 1). "
                        "Would you like me to create reminders for each due date?"
                    )
                )
                .oracle()
                .depends_on(view_sticky_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, create the three reminders.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            reminder_events = []
            prev = acceptance_event
            for task in self.oracle_tasks:
                evt = (
                    reminder_app.add_reminder(
                        title=f"Follow up: {task['title']}",
                        due_datetime=task["due_datetime"],
                        description=f"Owner: {task['owner']}. From sprint sticky attachment.",
                    )
                    .oracle()
                    .depends_on(prev, delay_seconds=1)
                )
                reminder_events.append(evt)
                prev = evt

        self.events = [
            read_note_event,
            view_sticky_event,
            proposal_event,
            acceptance_event,
            *reminder_events,
        ]

    @staticmethod
    def _reminder_due_matches_oracle(due_str: str, oracle_dues: set[str]) -> bool:
        raw = str(due_str).strip()
        if raw in oracle_dues:
            return True
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return False
        for o in oracle_dues:
            try:
                odt = datetime.strptime(o, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            except ValueError:
                continue
            if abs((dt - odt).total_seconds()) <= 86_400:
                return True
        return False

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:  # noqa: C901
        """Validate note read, sticky attachment viewing, proposal, and three oracle reminders."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))
            oracle_dues = {t["due_datetime"] for t in self.oracle_tasks}

            sticky_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_path=_STICKY_PATH,
            )

            note_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulNotesApp"
                and e.action.function_name in ("get_note_by_id", "search_notes", "search_notes_in_folder")
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            matched_reminder_dues: set[str] = set()
            for e in log_entries:
                if (
                    e.event_type == EventType.AGENT
                    and isinstance(e.action, Action)
                    and e.action.class_name == "StatefulReminderApp"
                    and e.action.function_name == "add_reminder"
                    and self._reminder_due_matches_oracle(
                        str(e.action.args.get("due_datetime", "")),
                        oracle_dues,
                    )
                ):
                    due = str(e.action.args.get("due_datetime", ""))
                    for o in oracle_dues:
                        if self._reminder_due_matches_oracle(due, {o}):
                            matched_reminder_dues.add(o)
                            break

            reminders_ok = len(matched_reminder_dues) >= 3

            success = sticky_viewed and note_read and proposal_found and reminders_ok

            if not success:
                failed: list[str] = []
                if not note_read:
                    failed.append("agent did not read the sprint sticky trigger note")
                if not sticky_viewed:
                    failed.append(f"agent did not view {_STICKY_PATH}")
                if not proposal_found:
                    failed.append("agent did not proactively propose creating reminders")
                if not reminders_ok:
                    failed.append(
                        "agent did not create three reminders with due datetimes matching the sticky-note dates "
                        f"(oracle: {sorted(oracle_dues)}; matched: {sorted(matched_reminder_dues)})"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
