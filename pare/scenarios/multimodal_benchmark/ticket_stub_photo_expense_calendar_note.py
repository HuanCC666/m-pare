"""Scenario: Agent replies to HR with a ticket-stub photo from Album after a reimbursement reminder."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulAlbumApp,
    StatefulEmailApp,
)
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "ticket_stub_photo_expense_calendar_note_suggestion"
_STUB_ASSET_IMAGE = _ASSETS / "movie_ticket_stub.jpg"
_STUB_INTERNAL_PATH = "/photos/movie_ticket_stub.jpg"
_HR_SENDER = "hr@company.com"
_ORACLE_TITLE = "The Odyssey"


@register_scenario("ticket_stub_photo_expense_calendar_note_suggestion")
class TicketStubPhotoExpenseCalendarNoteSuggestion(PAREScenario):
    """Agent reimburses a company movie ticket by replying to HR with a stub photo from Album.

    HR already emailed about the company movie outing: keep your ticket stub after the
    screening and reply to that email for reimbursement. A reminder nudges the user to
    submit reimbursement. The assistant must:
    1. Read the HR reimbursement email and the due reminder.
    2. Find and visually inspect the ticket stub photo in Camera Roll.
    3. Propose replying to HR with the stub photo attached.
    4. Send the reply only after accept/reject acceptance.

    Constraints:
    - Proactive permission before sending email.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Reminder text must not carry the movie title from the stub.
    """

    start_time = datetime(2025, 11, 19, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed Files, Album ticket stub photo, HR email, and reimbursement reminder."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        if not _STUB_ASSET_IMAGE.exists():
            raise FileNotFoundError(
                f"Ticket stub image not found: {_STUB_ASSET_IMAGE}. Place movie_ticket_stub.jpg under {_ASSETS}."
            )

        self.files.mkdir("/photos", create_parents=True)
        with self.files.open(_STUB_INTERNAL_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(_STUB_ASSET_IMAGE.read_bytes()))

        # The stub photo date is Nov 18; reminder triggers on Nov 19.
        self.stub_day = "2025-11-18"
        self.stub_photo_id = self.album.add_photo_with_time(
            file_path=_STUB_INTERNAL_PATH,
            file_name="movie_ticket_stub.jpg",
            caption="",
            description="",
            tags=[],
            location=None,
            taken_at=f"{self.stub_day} 19:30:00",
        )

        self.movie_email_id = self.email.create_and_add_email_with_time(
            sender=_HR_SENDER,
            recipients=[self.email.user_email],
            subject="Company movie night — ticket stub reimbursement",
            content=(
                "Hi team,\n\n"
                "Thank you for joining the company movie outing. After the screening, "
                "please keep your physical ticket stub.\n\n"
                "To request reimbursement, reply to this email and attach a clear photo "
                "of your ticket stub.\n\n"
                "Thanks,\nPeople Operations"
            ),
            email_time="2025-11-17 14:00:00",
            folder_name="INBOX",
        )

        self.reminder.add_reminder(
            title="Submit movie ticket reimbursement",
            due_datetime="2025-11-19 09:00:10",
            description="Reply to HR with your ticket stub photo",
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.email, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: reminder → HR email → Album stub → propose → reply with attachment."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        with EventRegisterer.capture_mode():
            list_reminders_event = reminder_app.current_state.list_all_reminders().oracle().delayed(20)

            read_hr_email_event = (
                email_app.get_email_by_id(email_id=self.movie_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(list_reminders_event, delay_seconds=1)
            )

            list_stub_day_event = (
                album_app.list_photos(
                    folder="Camera Roll",
                    offset=0,
                    limit=10,
                    taken_on=self.stub_day,
                )
                .oracle()
                .depends_on(read_hr_email_event, delay_seconds=1)
            )

            view_stub_event = (
                album_app.view_photo(photo_id=self.stub_photo_id)
                .oracle()
                .depends_on(list_stub_day_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "HR asked everyone to reply with a ticket stub photo for company movie "
                        f"reimbursement, and your reminder is due. I inspected your stub photo "
                        f"from movie night ({_ORACLE_TITLE}). "
                        f"Would you like me to reply to {_HR_SENDER} with the stub attached?"
                    )
                )
                .oracle()
                .depends_on(view_stub_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, reply to HR with the ticket stub photo.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            reimbursement_reply_event = (
                email_app.reply_to_email(
                    email_id=self.movie_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi,\n\n"
                        "Please find my ticket stub photo attached for reimbursement after "
                        f"the company movie screening ({_ORACLE_TITLE}).\n\n"
                        "Thank you."
                    ),
                    attachment_paths=[_STUB_INTERNAL_PATH],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            list_reminders_event,
            read_hr_email_event,
            list_stub_day_event,
            view_stub_event,
            proposal_event,
            acceptance_event,
            reimbursement_reply_event,
        ]

    @staticmethod
    def _reply_targets_hr(args: dict[str, object], movie_email_id: str) -> bool:
        if args.get("email_id") == movie_email_id:
            return True
        return _HR_SENDER in str(args).lower()

    @staticmethod
    def _reply_body_mentions_stub(args: dict[str, object]) -> bool:
        blob = str(args.get("content", "")).lower()
        return "stub" in blob or "ticket" in blob or _ORACLE_TITLE.lower() in blob

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:  # noqa: C901
        """Validate HR email read, reminder check, stub view, proposal, and reply with photo."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            hr_email_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name in ("get_email_by_id", "download_attachments")
                and str((e.action.args or {}).get("email_id", "")) == self.movie_email_id
                for e in log_entries
            )

            reminder_checked = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name
                # Proactive agent reads reminders via Reminders app_tools (not the UI state user_tools).
                in ("get_all_reminders", "get_due_reminders", "get_reminder_with_id")
                for e in log_entries
            )

            stub_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                photo_ids={self.stub_photo_id},
                image_paths=[_STUB_INTERNAL_PATH],
                min_views=1,
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            reply_found = False
            attachment_ok = False
            body_ok = False
            for e in log_entries:
                if e.event_type != EventType.AGENT or not isinstance(e.action, Action):
                    continue
                if e.action.class_name != "StatefulEmailApp":
                    continue
                if e.action.function_name not in ("reply_to_email", "send_composed_email", "send_email"):
                    continue
                args = e.action.args or {}
                if e.action.function_name == "reply_to_email" and args.get("email_id") != self.movie_email_id:
                    continue
                if not self._reply_targets_hr(args, self.movie_email_id):
                    continue
                reply_found = True
                attachment_paths = args.get("attachment_paths", [])
                attachment_ok = any(
                    _STUB_INTERNAL_PATH in str(p) or "movie_ticket_stub" in str(p) for p in attachment_paths
                )
                body_ok = self._reply_body_mentions_stub(args)
                break

            success = proposal_found and reply_found and attachment_ok and body_ok

            advisory_failures: list[str] = []
            if not hr_email_read:
                advisory_failures.append("agent did not read the company movie reimbursement email from HR")
            if not reminder_checked:
                advisory_failures.append("agent did not check the reimbursement reminder")
            if not stub_viewed:
                advisory_failures.append("agent did not visually inspect the ticket stub photo in Album")

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("agent did not proactively propose replying to HR with the stub photo")
                if not reply_found:
                    failed.append(f"agent did not reply to {_HR_SENDER} about ticket stub reimbursement")
                elif not attachment_ok:
                    failed.append("agent reply to HR did not attach the ticket stub photo from Album")
                elif not body_ok:
                    failed.append("agent reply did not mention the ticket stub or screening in the message body")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
