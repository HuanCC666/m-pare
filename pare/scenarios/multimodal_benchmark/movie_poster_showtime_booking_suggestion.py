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
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario


@register_scenario("movie_poster_showtime_booking_suggestion")
class MoviePosterShowtimeBookingSuggestion(PAREScenario):
    """Agent infers movie intent from an image poster and suggests booking nearby tickets.

    A friend sends an image attachment (teaser poster).
    The assistant must:
    1. Read the incoming friend message and use the image attachment as the trigger.
    2. Retrieve nearby showtimes for that movie from the shopping/ticket catalog.
    3. Check friend availability from the friend message content and user availability from Calendar.
    4. Proactively suggest booking a matching showtime.
    5. After user acceptance, add two tickets to cart and complete checkout.

    Constraints:
    - Reading email and inspecting the poster can happen before permission.
    - Before calendar/ticketing actions, ask one proactive accept/reject permission question.
    - Do not require user instructions beyond accept/reject style responses.
    """

    start_time = datetime(2025, 11, 18, 17, 30, 0, tzinfo=UTC).timestamp()

    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_POSTER_PATH = (
        Path(__file__).parent / "assets" / "movie_poster_showtime_booking_suggestion" / "movie_teaser_poster.jpg"
    )

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed movie-ticket + schedule state."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.calendar = StatefulCalendarApp(name="Calendar")
        self.shopping = StatefulShoppingApp(name="Shopping")

        local_poster_path = Path(os.getenv("PARE_MOVIE_POSTER_LOCAL_PATH", str(self.DEFAULT_LOCAL_POSTER_PATH)))
        if not local_poster_path.exists():
            raise FileNotFoundError(
                f"Movie poster image not found: {local_poster_path}. "
                f"Place movie_teaser_poster.jpg under {self.DEFAULT_LOCAL_POSTER_PATH.parent}."
            )
        poster_bytes = jpeg_bytes_for_sandbox(local_poster_path.read_bytes())
        with self.files.open("/movie_teaser_poster.jpg", "wb") as f:
            f.write(poster_bytes)

        self.friend_poster_email_id = "alex_movie_poster_email"
        self.calendar.add_calendar_event(
            title="Dinner with lab team",
            start_datetime="2025-11-18 19:00:00",
            end_datetime="2025-11-18 20:15:00",
            location="Campus Cafe",
            description="Team dinner before project deadline.",
        )

        movie_pid = self.shopping.add_product(name="The Odyssey (2026) - Downtown Cinema")
        self.movie_product_id = movie_pid

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
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.friend_poster_email_id,
                sender="alex.park@email.com",
                subject="Movie tonight?",
                content=(
                    "Hey — random idea for tonight. I'm free after like 8:15ish (once I'm done with stuff).\n\n"
                    "The Summer 2026 teaser art is in the attached poster — I don't have the listing link handy. "
                    "If you can spot this movie at Downtown Cinema with a late-ish show after dinner, that would work. "
                    "Grab us 2 tickets? I'll Venmo you."
                ),
                attachment_paths=["/movie_teaser_poster.jpg"],
            ).delayed(10)

            read_email_event = (
                email_app.get_email_by_id(email_id=self.friend_poster_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_poster_event = (
                files_app.display(path="/movie_teaser_poster.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I checked Alex's email and the attached teaser poster. "
                        "Downtown Cinema has post-8:15 showtimes in Shopping — proceed to book 2 tickets? Proceed?"
                    )
                )
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

            photo_visual_input_found = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any_event_type,
                image_path="/movie_teaser_poster.jpg",
                email_id=self.friend_poster_email_id,
            )

            ticket_catalog_grounded_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name
                in (
                    "list_products",
                    "view_product",
                    "add_to_cart",
                )
                and (
                    e.action.function_name == "list_products"
                    or self.ticket_item_730 in str(e.action.args)
                    or self.ticket_item_900 in str(e.action.args)
                    or self.movie_product_id in str(e.action.args)
                    or "odyssey" in str(e.action.args).lower()
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

            tickets_in_cart_or_checkout_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name in ("add_to_cart", "checkout")
                for e in log_entries
            )

            success = (
                photo_visual_input_found
                and ticket_catalog_grounded_found
                and proposal_found
                and tickets_in_cart_or_checkout_found
            )

            if not success:
                failed_checks: list[str] = []

                if not photo_visual_input_found:
                    failed_checks.append(
                        "agent never accessed the movie poster image (no Files read of /movie_teaser_poster.jpg "
                        "and no Email read/download for the inbox message with the attachment)"
                    )

                if photo_visual_input_found and not ticket_catalog_grounded_found:
                    failed_checks.append(
                        "agent viewed the poster but failed to ground the correct movie or showtime in Shopping"
                    )

                if ticket_catalog_grounded_found and not proposal_found:
                    failed_checks.append(
                        "agent grounded showtimes but failed to proactively propose ticket booking assistance"
                    )

                if not tickets_in_cart_or_checkout_found:
                    failed_checks.append("agent failed to add movie tickets to cart or proceed to checkout")

                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(
                success=False,
                exception=e,
            )
