"""Scenario: Agent proposes move-car reminder from recent Album parking sign after a meeting email."""

from __future__ import annotations

import os
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

_ASSETS = Path(__file__).parent / "assets" / "meeting_recent_parking_sign_suggestion"
_PHOTO_INTERNAL_PATH = "/photos/parking_sign_recent.jpg"
_ORACLE_LATEST_MOVE_TIME = "17:40"
_ORACLE_LATEST_MOVE_TIME_12H = "5:40"
_ORACLE_REMINDER_TIME = "17:20"


@register_scenario("meeting_recent_parking_sign_suggestion")
class MeetingRecentParkingSignSuggestion(PAREScenario):
    """Agent uses a meeting email + recent parking sign photo to propose a move-car reminder.

    A meeting-update email arrives with an end time. The user also has a recent parking
    sign photo in Camera Roll where the parking restriction is only visible in the image.
    The assistant must:
    1. Read the meeting email.
    2. Check recent Camera Roll entries and inspect the parking sign photo.
    3. Proactively propose a move-car reminder using inferred latest move time.
    4. Create the reminder only after accept/reject acceptance.

    Constraints:
    - No location APIs are available.
    - "Parking start" should be inferred from the photo taken_at timestamp.
    - User responses are accept/reject only.
    """

    start_time = datetime(2025, 11, 28, 16, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_SIGN_IMAGE = _ASSETS / "parking_sign_recent.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed Files/Album parking-sign photo, meeting email fixture, and reminders app."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        local_photo_path = Path(os.getenv("PARE_PARKING_SIGN_LOCAL_PATH", str(self.DEFAULT_SIGN_IMAGE)))
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Parking sign image not found: {local_photo_path}. Place parking_sign_recent.jpg under {_ASSETS}."
            )
        self.files.mkdir("/photos", create_parents=True)
        with self.files.open(_PHOTO_INTERNAL_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))

        scenario_day = datetime.fromtimestamp(self.start_time, tz=UTC).strftime("%Y-%m-%d")
        self.sign_photo_id = self.album.add_photo_with_time(
            file_path=_PHOTO_INTERNAL_PATH,
            file_name="parking_sign_recent.jpg",
            caption="",
            description="",
            tags=[],
            location=None,
            taken_at=f"{scenario_day} 15:40:00",
        )
        self.scenario_day = scenario_day
        self.meeting_email_id = "meeting_update_parking_check"

        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.email, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: email trigger -> list/view recent photo -> proposal -> reminder write after acceptance."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        with EventRegisterer.capture_mode():
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.meeting_email_id,
                sender="calendar-bot@company.com",
                subject="Meeting updated: Client debrief today 4:30-5:30 PM",
                content=(
                    "Quick update: the client debrief is now 4:30 PM to 5:30 PM today. "
                    "Plan around the updated end time."
                ),
            ).delayed(8)

            read_email_event = (
                email_app.get_email_by_id(email_id=self.meeting_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            list_recent_roll_event = (
                album_app.list_photos(
                    folder="Camera Roll",
                    offset=0,
                    limit=12,
                    taken_on=self.scenario_day,
                )
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            view_sign_photo_event = (
                album_app.view_photo(photo_id=self.sign_photo_id)
                .oracle()
                .depends_on(list_recent_roll_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I checked your recent parking-sign photo and the meeting update email. "
                        "Assuming you parked when the photo was taken, your latest safe move time is about 5:40 PM. "
                        "Would you like me to add a 5:20 PM move-car reminder?"
                    )
                )
                .oracle()
                .depends_on(view_sign_photo_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, add the move-car reminder.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            reminder_event = (
                reminder_app.add_reminder(
                    title="Move car before parking limit",
                    due_datetime="2025-11-28 17:20:00",
                    description="Based on your recent parking-sign photo and meeting end time.",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            list_recent_roll_event,
            view_sign_photo_event,
            proposal_event,
            acceptance_event,
            reminder_event,
        ]

    @staticmethod
    def _time_in_blob(blob: str, hhmm: str) -> bool:
        return hhmm in blob or hhmm.replace(":", "") in blob.replace(":", "")

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate email trigger handling, recent Album photo inspection, proposal, and reminder write."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            email_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name in ("get_email_by_id", "list_emails", "search_emails")
                for e in log_entries
            )

            recent_album_checked = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulAlbumApp"
                and e.action.function_name in ("list_photos", "search_photos")
                for e in log_entries
            )

            sign_photo_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                photo_ids={self.sign_photo_id},
                email_id=self.meeting_email_id,
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                and (
                    self._time_in_blob(str(e.action.args), _ORACLE_LATEST_MOVE_TIME)
                    or self._time_in_blob(str(e.action.args), _ORACLE_LATEST_MOVE_TIME_12H)
                )
                for e in log_entries
            )

            reminder_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and self._time_in_blob(str(e.action.args.get("due_datetime", "")), _ORACLE_REMINDER_TIME)
                for e in log_entries
            )

            success = proposal_found and reminder_found

            advisory_failures: list[str] = []
            if not email_read:
                advisory_failures.append("agent did not read the meeting update email")
            if not recent_album_checked:
                advisory_failures.append("agent did not check recent Camera Roll photos")
            if not sign_photo_viewed:
                advisory_failures.append("agent did not visually inspect the recent parking sign photo")

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append(
                        f"agent did not proactively propose a move-car plan with latest time around {_ORACLE_LATEST_MOVE_TIME}"
                    )
                if not reminder_found:
                    failed.append(f"agent did not create a move-car reminder around {_ORACLE_REMINDER_TIME}")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
