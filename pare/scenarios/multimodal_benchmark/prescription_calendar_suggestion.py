"""Scenario: Agent blocks meal-time medication slots from a prescription label photo."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulCalendarApp
from pare.apps.note import StatefulNotesApp
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "prescription_calendar_suggestion"
_LABEL_PATH = "/prescription_label.jpg"
_ORACLE_MED = "metformin"
_LUNCH_TIME = "12:30:00"
_DINNER_TIME = "19:30:00"


@register_scenario("prescription_calendar_suggestion")
class PrescriptionCalendarSuggestion(PAREScenario):
    """Agent schedules post-meal medication blocks from a prescription label photo.

    The user attaches a pharmacy label image to an Inbox note (environment event).
    Dosing schedule (twice daily, 30 minutes after breakfast and dinner) is only on
    the label. The assistant must:
    1. Read the note and inspect the attachment.
    2. Infer lunch/dinner medication times from the image.
    3. Propose creating calendar blocks for the next several days.
    4. Create events only after accept/reject acceptance.

    Constraints:
    - Proactive permission before calendar writes.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Note body must not include specific dose times or drug name.
    """

    start_time = datetime(2025, 11, 26, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LABEL_IMAGE = _ASSETS / "prescription_label.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed note shell, calendar baseline, reminder, and label JPEG."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.note = StatefulNotesApp(name="Notes")
        self.note.internal_fs = self.files
        self.calendar = StatefulCalendarApp(name="Calendar")
        self.reminder = StatefulReminderApp(name="Reminders")

        local_path = Path(os.getenv("PARE_PRESCRIPTION_LABEL_LOCAL_PATH", str(self.DEFAULT_LABEL_IMAGE)))
        if not local_path.exists():
            raise FileNotFoundError(
                f"Prescription label image not found: {local_path}. Place prescription_label.jpg under {_ASSETS}."
            )
        with self.files.open(_LABEL_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_path.read_bytes()))

        self.trigger_note_id = self.note.create_note_with_time(
            folder="Inbox",
            title="New Rx — calendar blocks",
            content=("New precription for the next week. "),
            created_at="2025-11-26 08:45:00",
            updated_at="2025-11-26 08:45:00",
        )
        self.reminder.add_reminder(
            title="Process Rx calendar note in Inbox",
            due_datetime="2025-11-26 09:00:10",
            description="Open 'New Rx — calendar blocks' and follow the label attachment.",
        )

        base = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.oracle_days = [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

        self.apps = [self.agent_ui, self.system_app, self.files, self.note, self.calendar, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: read note → view label → propose → create medication calendar blocks."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        note_app = self.get_typed_app(StatefulNotesApp, "Notes")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        day0 = self.oracle_days[0]

        # Seed attachment after state init but before capture to avoid bytes in initial-state JSON.
        note_app.add_attachment_to_note(note_id=self.trigger_note_id, attachment_path=_LABEL_PATH)

        with EventRegisterer.capture_mode():
            read_note_event = note_app.get_note_by_id(note_id=self.trigger_note_id).oracle().delayed(8)

            view_label_event = (
                note_app.view_attachment(note_id=self.trigger_note_id, attachment=Path(_LABEL_PATH).name)
                .oracle()
                .depends_on(read_note_event, delay_seconds=1)
            )

            check_calendar_event = (
                calendar_app.get_calendar_events_from_to(
                    start_datetime=f"{day0} 00:00:00",
                    end_datetime=f"{self.oracle_days[-1]} 23:59:59",
                )
                .oracle()
                .depends_on(view_label_event, delay_seconds=2)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "The label is for Metformin 500 mg: one tablet twice daily, "
                        "30 minutes after breakfast and dinner (about 12:30 PM and 7:30 PM). "
                        "Would you like me to add 15-minute medication blocks at those times for the next week?"
                    )
                )
                .oracle()
                .depends_on(check_calendar_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, add those medication blocks for the week.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            lunch_block_event = (
                calendar_app.add_calendar_event(
                    title="Take Metformin (after breakfast)",
                    start_datetime=f"{day0} {_LUNCH_TIME}",
                    end_datetime=f"{day0} 12:45:00",
                    description="From prescription label — 30 min after breakfast.",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            dinner_block_event = (
                calendar_app.add_calendar_event(
                    title="Take Metformin (after dinner)",
                    start_datetime=f"{day0} {_DINNER_TIME}",
                    end_datetime=f"{day0} 19:45:00",
                    description="From prescription label — 30 min after dinner.",
                )
                .oracle()
                .depends_on(lunch_block_event, delay_seconds=1)
            )

        self.events = [
            read_note_event,
            view_label_event,
            check_calendar_event,
            proposal_event,
            acceptance_event,
            lunch_block_event,
            dinner_block_event,
        ]

    @staticmethod
    def _calendar_block_matches_oracle(args: dict[str, object]) -> bool:
        blob = str(args).lower()
        if _ORACLE_MED not in blob:
            return False
        start = str(args.get("start_datetime", ""))
        return _LUNCH_TIME[:5] in start or _DINNER_TIME[:5] in start

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate label vision, calendar grounding, proposal, and medication blocks."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            label_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_path=_LABEL_PATH,
            )

            note_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulNotesApp"
                and e.action.function_name in ("get_note_by_id", "search_notes", "search_notes_in_folder")
                for e in log_entries
            )

            calendar_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name
                in ("get_calendar_events_from_to", "read_today_calendar_events", "get_calendar_event")
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            med_blocks = [
                e
                for e in log_entries
                if e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulCalendarApp"
                and e.action.function_name == "add_calendar_event"
                and self._calendar_block_matches_oracle(e.action.args or {})
            ]

            lunch_block = any(_LUNCH_TIME[:5] in str(e.action.args.get("start_datetime", "")) for e in med_blocks)
            dinner_block = any(_DINNER_TIME[:5] in str(e.action.args.get("start_datetime", "")) for e in med_blocks)

            success = proposal_found and lunch_block and dinner_block

            advisory_failures: list[str] = []
            if not note_read:
                advisory_failures.append("agent did not read the Rx trigger note")
            if not label_viewed:
                advisory_failures.append(f"agent did not view {_LABEL_PATH}")
            if not calendar_read:
                advisory_failures.append("agent did not check Calendar availability")

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("agent did not proactively propose medication calendar blocks")
                if not lunch_block:
                    failed.append(f"agent did not create a ~{_LUNCH_TIME[:5]} Metformin calendar block")
                if not dinner_block:
                    failed.append(f"agent did not create a ~{_DINNER_TIME[:5]} Metformin calendar block")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
