"""Scenario: Agent updates a flight calendar event from a boarding-pass screenshot in Messages."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulCalendarApp, StatefulMessagingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "boarding_pass_chat_calendar_update_suggestion"
_BOARDING_PASS_PATH = "/photos/boarding_pass_screenshot.jpg"  # noqa: S105
_ORACLE_FLIGHT = "UA 482"
_ORACLE_GATE = "B12"
_ORACLE_SEAT = "14C"
_ORACLE_BOARDING = "10:35"
_ORACLE_DEPARTURE = "11:20"
_FLIGHT_DAY = "2025-12-05"
_ORACLE_BOARDING_START = f"{_FLIGHT_DAY} 10:35:00"
_ORACLE_ARRIVAL_END = f"{_FLIGHT_DAY} 14:15:00"


@register_scenario("boarding_pass_chat_calendar_update_suggestion")
class BoardingPassChatCalendarUpdateSuggestion(PAREScenario):
    """Agent fills in gate, seat, and boarding time on Calendar from a friend's chat attachment.

    A friend messages in the Chicago trip thread: asks whether the user has bought tickets
    yet, says they already booked, and attaches their mobile boarding-pass screenshot (env
    inject only — the simulated user cannot send messages or attachments). Calendar already
    has a placeholder flight block for UA 482 on the travel day, but gate, seat, and
    boarding time are only visible on the friend's screenshot. The assistant must:
    1. Read the trip chat and inspect the friend's boarding-pass attachment.
    2. Find the existing flight event on Calendar.
    3. Propose updating that event with gate, seat, and boarding/departure times from the image.
    4. Apply the calendar edit only after accept/reject acceptance.

    Constraints:
    - Proactive permission before calendar writes.
    - User responses are accept/reject only.
    - No AUI user-input trigger; no user-authored chat or attachment env events.
    - Incoming message text must not state gate, seat, or boarding time.
    """

    start_time = datetime(2025, 12, 3, 18, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_BOARDING_PASS_IMAGE = _ASSETS / "boarding_pass_screenshot.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed messaging thread, boarding-pass JPEG, and placeholder flight calendar event."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.calendar = StatefulCalendarApp(name="Calendar")

        local_path = Path(os.getenv("PARE_BOARDING_PASS_SCREENSHOT_LOCAL_PATH", str(self.DEFAULT_BOARDING_PASS_IMAGE)))
        if not local_path.exists():
            raise FileNotFoundError(
                f"Boarding pass screenshot not found: {local_path}. Place boarding_pass_screenshot.jpg under {_ASSETS}."
            )
        self.files.mkdir("/photos", create_parents=True)
        with self.files.open(_BOARDING_PASS_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_path.read_bytes()))

        friend_name = "Alex Kim"
        friend_phone = "+1-555-0192"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")
        self.user_id = self.messaging.current_user_id

        trip_chat = ConversationV2(
            participant_ids=[self.user_id, self.friend_id],
            title="Chicago Trip",
            messages=[
                MessageV2(
                    sender_id=self.friend_id,
                    content="Still good for Chicago the first week of December? I'm aiming to fly out on the 5th.",
                    timestamp=self.start_time - 86400,
                ),
            ],
        )
        trip_chat.update_last_updated(self.start_time - 86400)
        self.messaging.add_conversation(trip_chat)
        self.conversation_id = trip_chat.conversation_id

        self.flight_event_id = self.calendar.add_calendar_event(
            title=f"Flight to Chicago ({_ORACLE_FLIGHT})",
            start_datetime=f"{_FLIGHT_DAY} 08:00:00",
            end_datetime=f"{_FLIGHT_DAY} 18:00:00",
            description="Trip flight booked — gate, seat, and boarding time not added yet.",
            tag="travel",
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.calendar]

    def build_events_flow(self) -> None:
        """Oracle: friend shares pass → read/view → calendar read → propose → edit."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        calendar_app = self.get_typed_app(StatefulCalendarApp, "Calendar")

        with EventRegisterer.capture_mode():
            friend_pass_event = messaging_app.create_and_add_message(
                conversation_id=self.conversation_id,
                sender_id=self.friend_id,
                content=(
                    "Did you buy your tickets for the Chicago trip yet? I already booked mine. Here's my boarding pass."
                ),
                attachment_path=_BOARDING_PASS_PATH,
            ).delayed(5)

            read_chat_event = (
                messaging_app.read_conversation(
                    conversation_id=self.conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(friend_pass_event, delay_seconds=2)
            )

            view_pass_event = (
                files_app.display(path=_BOARDING_PASS_PATH).oracle().depends_on(read_chat_event, delay_seconds=1)
            )

            read_calendar_event = (
                calendar_app.get_calendar_events_from_to(
                    start_datetime=f"{_FLIGHT_DAY} 00:00:00",
                    end_datetime=f"{_FLIGHT_DAY} 23:59:59",
                )
                .oracle()
                .depends_on(view_pass_event, delay_seconds=2)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        f"Alex asked whether you've booked Chicago tickets and shared their boarding pass. "
                        f"From the screenshot I read gate {_ORACLE_GATE}, seat {_ORACLE_SEAT}, "
                        f"boarding at {_ORACLE_BOARDING} AM, and departure at {_ORACLE_DEPARTURE} AM "
                        f"on {_ORACLE_FLIGHT}. "
                        f"Would you like me to update your existing "
                        f'"Flight to Chicago ({_ORACLE_FLIGHT})" calendar event with those details?'
                    )
                )
                .oracle()
                .depends_on(read_calendar_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, update my flight calendar event with the boarding pass details.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            update_calendar_event = (
                calendar_app.edit_calendar_event(
                    event_id=self.flight_event_id,
                    start_datetime=_ORACLE_BOARDING_START,
                    end_datetime=_ORACLE_ARRIVAL_END,
                    location=f"Gate {_ORACLE_GATE}",
                    description=(
                        f"{_ORACLE_FLIGHT} — seat {_ORACLE_SEAT}, "
                        f"boarding {_ORACLE_BOARDING} AM, departs {_ORACLE_DEPARTURE} AM."
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            friend_pass_event,
            read_chat_event,
            view_pass_event,
            read_calendar_event,
            proposal_event,
            acceptance_event,
            update_calendar_event,
        ]

    @staticmethod
    def _blob_has_gate_seat_boarding(blob: str) -> bool:
        lower = blob.lower()
        gate_ok = _ORACLE_GATE.lower() in lower or f"gate {_ORACLE_GATE.lower()}" in lower
        seat_ok = _ORACLE_SEAT.lower() in lower
        boarding_ok = _ORACLE_BOARDING in blob or "10:35" in blob
        return gate_ok and seat_ok and boarding_ok

    @staticmethod
    def _edit_matches_oracle(args: dict[str, object]) -> bool:
        blob = str(args).lower()
        if not BoardingPassChatCalendarUpdateSuggestion._blob_has_gate_seat_boarding(blob):
            return False
        start = str(args.get("start_datetime", ""))
        if _ORACLE_BOARDING[:5] not in start and "10:35" not in start:
            return False
        location = str(args.get("location", "")).lower()
        return _ORACLE_GATE.lower() in location or "gate" in location

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:  # noqa: C901
        """Validate chat read, pass vision, calendar grounding, proposal, and flight event update."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            chat_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "read_conversation"
                and str((e.action.args or {}).get("conversation_id", "")) == self.conversation_id
                for e in log_entries
            )

            pass_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_paths=[_BOARDING_PASS_PATH],
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
                and self._blob_has_gate_seat_boarding(str(e.action.args))
                for e in log_entries
            )

            calendar_updated = False
            for e in log_entries:
                if e.event_type != EventType.AGENT or not isinstance(e.action, Action):
                    continue
                if e.action.class_name != "StatefulCalendarApp":
                    continue
                if e.action.function_name != "edit_calendar_event":
                    continue
                args = e.action.args or {}
                if str(args.get("event_id", "")) != self.flight_event_id:
                    continue
                if self._edit_matches_oracle(args):
                    calendar_updated = True
                    break

            success = chat_read and pass_viewed and calendar_read and proposal_found and calendar_updated

            if not success:
                failed: list[str] = []
                if not chat_read:
                    failed.append(
                        "agent did not read the Chicago trip chat after Alex shared the boarding-pass attachment"
                    )
                if not pass_viewed:
                    failed.append(
                        "agent did not visually inspect Alex's boarding-pass screenshot attachment in Messages"
                    )
                if not calendar_read:
                    failed.append("agent did not read Calendar around the flight day before updating")
                if not proposal_found:
                    failed.append(
                        f"agent did not proactively propose updating the flight event with gate {_ORACLE_GATE}, "
                        f"seat {_ORACLE_SEAT}, and boarding {_ORACLE_BOARDING}"
                    )
                if not calendar_updated:
                    failed.append(
                        f"agent did not edit the flight calendar event ({self.flight_event_id}) with "
                        f"gate, seat, and boarding time from the boarding pass"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
