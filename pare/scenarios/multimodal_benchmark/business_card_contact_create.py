"""Scenario: Agent creates a contact from a business card photo on a user Note."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulContactsApp
from pare.apps.note import StatefulNotesApp
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "business_card_contact_create"
_CARD_PATH = "/business_card.jpg"
_ORACLE_EMAIL = "chen.wei@novalabs.com"
_ORACLE_PHONE = "5552048817"


@register_scenario("business_card_contact_create")
class BusinessCardNoteContactCreateSuggestion(PAREScenario):
    """Agent saves a conference contact from a business card photo on a user Note.

    The user attaches a business card image to an Inbox note. Name, company, email,
    and phone appear only on the card. The assistant must:
    1. Read the note and inspect the attachment.
    2. Extract contact fields from the image.
    3. Propose creating a Contacts entry with the parsed details.
    4. Create the contact only after accept/reject acceptance.

    Constraints:
    - Proactive permission before Contacts writes.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Note body must not include the person's email or phone.
    """

    start_time = datetime(2025, 11, 28, 15, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_CARD_IMAGE = _ASSETS / "business_card.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed note shell, reminder, and business card JPEG."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.note = StatefulNotesApp(name="Notes")
        self.note.internal_fs = self.files
        self.contacts = StatefulContactsApp(name="Contacts")
        self.reminder = StatefulReminderApp(name="Reminders")

        local_path = Path(os.getenv("PARE_BUSINESS_CARD_LOCAL_PATH", str(self.DEFAULT_CARD_IMAGE)))
        if not local_path.exists():
            raise FileNotFoundError(
                f"Business card image not found: {local_path}. Place business_card.jpg under {_ASSETS}."
            )
        with self.files.open(_CARD_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_path.read_bytes()))

        self.trigger_note_id = self.note.create_note_with_time(
            folder="Inbox",
            title="Conference card — add to contacts",
            content=("Met someone today. Need to add him to my Contacts"),
            created_at="2025-11-28 14:50:00",
            updated_at="2025-11-28 14:50:00",
        )
        self.reminder.add_reminder(
            title="Save conference contact from Inbox note",
            due_datetime="2025-11-28 15:00:10",
            description="Open 'Conference card — add to contacts'",
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.note, self.contacts, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: read note → view card → propose → add_new_contact."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        note_app = self.get_typed_app(StatefulNotesApp, "Notes")
        contacts_app = self.get_typed_app(StatefulContactsApp, "Contacts")

        # Seed attachment after state init but before capture to avoid bytes in initial-state JSON.
        note_app.add_attachment_to_note(note_id=self.trigger_note_id, attachment_path=_CARD_PATH)

        with EventRegisterer.capture_mode():
            read_note_event = note_app.get_note_by_id(note_id=self.trigger_note_id).oracle().delayed(8)

            view_card_event = (
                note_app.view_attachment(note_id=self.trigger_note_id, attachment=Path(_CARD_PATH).name)
                .oracle()
                .depends_on(read_note_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "The card shows Chen Wei, Product Manager at Nova Labs — "
                        "chen.wei@novalabs.com, +1-555-204-8817. "
                        "Would you like me to add this person to Contacts?"
                    )
                )
                .oracle()
                .depends_on(view_card_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please save Chen Wei to Contacts.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            create_contact_event = (
                contacts_app.add_new_contact(
                    first_name="Chen",
                    last_name="Wei",
                    job="Product Manager",
                    email=_ORACLE_EMAIL,
                    phone="+1-555-204-8817",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            read_note_event,
            view_card_event,
            proposal_event,
            acceptance_event,
            create_contact_event,
        ]

    @staticmethod
    def _normalize_phone(value: str) -> str:
        return "".join(ch for ch in value if ch.isdigit())

    @staticmethod
    def _contact_args_match_oracle(args: dict[str, object]) -> bool:
        blob = str(args).lower()
        email_ok = _ORACLE_EMAIL in blob
        phone_raw = str(args.get("phone", ""))
        phone_ok = _ORACLE_PHONE in BusinessCardNoteContactCreateSuggestion._normalize_phone(phone_raw)
        name_ok = "chen" in blob and "wei" in blob
        return email_ok and phone_ok and name_ok

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate card vision, proposal, and contact creation with oracle fields."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            card_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_path=_CARD_PATH,
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

            contact_created = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulContactsApp"
                and e.action.function_name in ("add_new_contact", "create_contact")
                and self._contact_args_match_oracle(e.action.args or {})
                for e in log_entries
            )

            success = proposal_found and contact_created

            advisory_failures: list[str] = []
            if not note_read:
                advisory_failures.append("agent did not read the business-card trigger note")
            if not card_viewed:
                advisory_failures.append(f"agent did not view {_CARD_PATH}")

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("agent did not proactively propose saving the contact")
                if not contact_created:
                    failed.append(
                        f"agent did not create a contact with {_ORACLE_EMAIL} and phone matching {_ORACLE_PHONE}"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
