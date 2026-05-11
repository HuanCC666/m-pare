"""Scenario: Agent proposes movie ticket booking from image poster + availability checks."""

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
    StatefulCalendarApp,
    StatefulEmailApp,
)
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario


@register_scenario("movie_poster_showtime_booking_suggestion")
class MoviePosterShowtimeBookingSuggestion(PAREScenario):
    """Agent infers movie intent from an image poster and suggests booking nearby tickets.

    A friend sends an image attachment containing a movie poster. The assistant must:
    1. Read the incoming friend message and use the image attachment as the trigger.
    2. Retrieve nearby showtimes for that movie from the shopping/ticket catalog.
    3. Check friend availability from the friend message content and user availability from Calendar.
    4. Proactively suggest booking a matching showtime.
    5. After user acceptance, add two tickets to cart and complete checkout.

    This scenario exercises multimodal trigger grounding (image attachment + text), cross-app reasoning
    (Email/Messages + Shopping + Calendar), temporal alignment across people, and proactive planning.

    Constraints:
    - Reading email and inspecting the poster can happen before permission.
    - Before calendar/ticketing actions, ask one proactive accept/reject permission question.
    - Do not require user instructions beyond accept/reject style responses.
    """

    start_time = datetime(2025, 11, 18, 17, 30, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    # Preferred: set env var PARE_MOVIE_POSTER_LOCAL_PATH to an absolute/relative local file path.
    # Fallback: keep an in-repo default under this scenario directory.
    DEFAULT_LOCAL_POSTER_PATH = Path(__file__).parent / "assets" / "starlight_movie_poster.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed movie-ticket + schedule state."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.calendar = StatefulCalendarApp(name="Calendar")
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Load a real poster image from local filesystem, then place it in sandbox FS for attachment.
        local_poster_path = Path(os.getenv("PARE_MOVIE_POSTER_LOCAL_PATH", str(self.DEFAULT_LOCAL_POSTER_PATH)))
        if not local_poster_path.exists():
            raise FileNotFoundError(
                f"Movie poster image not found: {local_poster_path}. "
                "Set PARE_MOVIE_POSTER_LOCAL_PATH or place the image at "
                f"{self.DEFAULT_LOCAL_POSTER_PATH}."
            )
        with self.files.open("/starlight_movie_poster.jpg", "wb") as f:
            f.write(local_poster_path.read_bytes())

        self.friend_poster_email_id = "alex_movie_poster_email"
        # Seed user calendar: user is busy around 7:30 PM, but free for 9:00 PM.
        self.calendar.add_calendar_event(
            title="Dinner with lab team",
            start_datetime="2025-11-18 19:00:00",
            end_datetime="2025-11-18 20:15:00",
            location="Campus Cafe",
            description="Team dinner before project deadline.",
        )

        # Seed movie ticket catalog in Shopping app (used as ticketing backend).
        movie_pid = self.shopping.add_product(name="Starlight (2026) - Downtown Cinema")
        self.ticket_item_730 = self.shopping.add_item_to_product(
            product_id=movie_pid,
            price=14.50,
            options={
                "showtime": "2025-11-18 19:30:00",
                "theater": "Downtown Cinema",
                "distance_km": 2.0,
                "seat_type": "Standard",
            },
            available=True,
        )
        self.ticket_item_900 = self.shopping.add_item_to_product(
            product_id=movie_pid,
            price=14.50,
            options={
                "showtime": "2025-11-18 21:00:00",
                "theater": "Downtown Cinema",
                "distance_km": 2.0,
                "seat_type": "Standard",
            },
            available=True,
        )
        self.movie_product_id = movie_pid
        self.showtime_item_map = {
            "2025-11-18 19:30:00": self.ticket_item_730,
            "2025-11-18 21:00:00": self.ticket_item_900,
        }

        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.calendar, self.shopping]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for movie booking."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            inject_email_event = (
                email_app.send_email_to_user_with_id(
                    email_id=self.friend_poster_email_id,
                    sender="alex.park@email.com",
                    subject="Movie tonight?",
                    content="Hey! Saw this movie poster and thought of you. I'm free after 8:15 PM tonight if you're down.",
                    attachment_paths=["/starlight_movie_poster.jpg"],
                )
                .oracle()
                .delayed(12)
            )

            read_email_event = (
                email_app.get_email_by_id(email_id=self.friend_poster_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_poster_event = (
                files_app.display(path="/starlight_movie_poster.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(content="I checked the poster and can proceed to ticket booking now. Proceed?")
                .oracle()
                .depends_on(view_poster_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please proceed with the best matching showtime.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            booking_action_event = (
                shopping_app.add_to_cart(item_id=self.ticket_item_900, quantity=2)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_poster_event,
            proposal_event,
            acceptance_event,
            booking_action_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate simple flow correctness for multimodal booking."""
        try:
            log_entries = env.event_log.list_view()

            allow_any_event_type = bool(getattr(env, "oracle_mode", False))
            # Core check 1: agent actually inspected the poster image.
            viewed_image_found = any(
                (allow_any_event_type or e.event_type == EventType.AGENT)
                and isinstance(e.action, Action)
                and e.action.class_name == "SandboxLocalFileSystem"
                and e.action.function_name in {"display", "cat", "read_document"}
                for e in log_entries
            )

            # Core check 2: agent proactively communicated a decision/proposal.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # Core check 3: agent took booking execution action.
            booking_action_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and (
                    (e.action.class_name == "StatefulShoppingApp" and e.action.function_name == "add_to_cart")
                    or (e.action.class_name == "StatefulShoppingApp" and e.action.function_name == "checkout")
                )
                for e in log_entries
            )

            success = viewed_image_found and proposal_found and booking_action_found
            if not success:
                failed_checks = []
                if not viewed_image_found:
                    failed_checks.append("agent did not inspect movie poster image")
                if not proposal_found:
                    failed_checks.append("agent did not send a proactive booking proposal")
                if not booking_action_found:
                    failed_checks.append("agent did not take any ticket-booking action")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
