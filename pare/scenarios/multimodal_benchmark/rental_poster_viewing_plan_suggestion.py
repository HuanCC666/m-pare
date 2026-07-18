"""Scenario: Agent maps rental poster image into apartment search + viewing logistics."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulCalendarApp, StatefulEmailApp
from pare.apps.apartment import StatefulApartmentApp
from pare.apps.cab import StatefulCabApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario


@register_scenario("rental_poster_viewing_plan_suggestion")
class RentalPosterViewingPlanSuggestion(PAREScenario):
    """Agent extracts rental-poster constraints and builds a viewing logistics plan.

    A rental poster screenshot arrives via email attachment. The assistant must:
    1. Read the message and inspect the image first.
    2. Ask one proactive accept/reject permission question before apartment/calendar/cab actions.
    3. After acceptance, map poster constraints into apartment search and combine schedule + cab estimate.
    4. Save the selected listing and plan.

    Constraints:
    - Image reading/browsing is allowed before permission.
    - Do not request additional user details.
    - User responses are limited to accept/reject style.
    """

    start_time = datetime(2025, 11, 22, 14, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_POSTER_PATH = (
        Path(__file__).parent / "assets" / "rental_poster_viewing_plan_suggestion" / "rental_poster_photo.jpg"
    )

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed the rental poster, listings, and calendar fixtures."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.apartment = StatefulApartmentApp(name="Apartment")
        self.calendar = StatefulCalendarApp(name="Calendar")
        self.cab = StatefulCabApp(name="Cab")

        local_poster_path = Path(os.getenv("PARE_RENTAL_POSTER_LOCAL_PATH", str(self.DEFAULT_LOCAL_POSTER_PATH)))
        if not local_poster_path.exists():
            raise FileNotFoundError(
                f"Rental poster image not found: {local_poster_path}. "
                f"Place rental_poster_photo.jpg under {self.DEFAULT_LOCAL_POSTER_PATH.parent}."
            )
        with self.files.open("/rental_poster_photo.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_poster_path.read_bytes()))

        self.rental_email_id = "rental_poster_email"
        self.target_apartment_id = self.apartment.add_new_apartment(
            name="Northside Garden Homes",
            location="Northside District, 88 Cedar Ave",
            zip_code="94108",
            price=2150.0,
            number_of_bedrooms=2,
            number_of_bathrooms=1,
            square_footage=860,
            property_type="Apartment",
            furnished_status="Unfurnished",
            floor_level="Mid-level",
            pet_policy="Cats allowed",
            lease_term="1 year",
            amenities=["Laundry", "Gym", "Near metro"],
        )
        self.target_listing_location = "Northside District, 88 Cedar Ave"
        self.target_listing_price = 2150.0
        self.target_listing_bedrooms = 2
        self.apartment.add_new_apartment(
            name="Riverside View Tower",
            location="Riverside, 15 Harbor St",
            zip_code="94110",
            price=2800.0,
            number_of_bedrooms=2,
            number_of_bathrooms=2,
            square_footage=1020,
            property_type="Apartment",
            furnished_status="Furnished",
            floor_level="Upper floors",
            pet_policy="No pets",
            lease_term="1 year",
            amenities=["Parking", "Pool"],
        )
        self.apartment.add_new_apartment(
            name="Northside Micro Studio",
            location="Northside District, 10 Pine St",
            zip_code="94108",
            price=1650.0,
            number_of_bedrooms=1,
            number_of_bathrooms=1,
            square_footage=420,
            property_type="Studio",
            furnished_status="Unfurnished",
            floor_level="Low-level",
            pet_policy="No pets",
            lease_term="1 year",
            amenities=["Laundry"],
        )

        # User availability: busy in morning, free around afternoon.
        self.calendar.add_calendar_event(
            title="Team standup",
            start_datetime="2025-11-23 10:00:00",
            end_datetime="2025-11-23 11:00:00",
            location="Office",
            description="Weekly standup",
        )
        self.calendar.add_calendar_event(
            title="Doctor appointment",
            start_datetime="2025-11-23 13:00:00",
            end_datetime="2025-11-23 14:00:00",
            location="Clinic",
            description="Routine checkup",
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.apartment, self.calendar, self.cab]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for rental planning."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        apartment_app = self.get_typed_app(StatefulApartmentApp, "Apartment")
        cab_app = self.get_typed_app(StatefulCabApp, "Cab")

        with EventRegisterer.capture_mode():
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.rental_email_id,
                sender="user.mobile@local",
                subject="Rental poster snapshot",
                content=(
                    "Saw this taped up on my walk home, snapped a pic. "
                    "No idea if it's legit or already gone but figured I'd send it your way in case anything close is listed. "
                    "If it looks real, maybe see whether anything similar is actually renting nearby? "
                    "If I toured this week I'd want an afternoon that fits my calendar and a rough sense what a ride from downtown would cost."
                ),
                attachment_paths=["/rental_poster_photo.jpg"],
            ).delayed(8)

            read_email_event = (
                email_app.get_email_by_id(email_id=self.rental_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_poster_event = (
                files_app.display(path="/rental_poster_photo.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content="I can screen rentals and generate viewing logistics from this poster. Proceed?"
                )
                .oracle()
                .depends_on(view_poster_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please proceed with the screening and logistics.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            apartment_action_event = (
                apartment_app.search_apartments(location="Northside", number_of_bedrooms=2, max_price=2300.0)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            logistics_action_event = (
                cab_app.get_quotation(
                    start_location="123 Main Street, Downtown",
                    end_location=self.target_listing_location,
                    service_type="Default",
                    ride_time="2025-11-23 15:30:00",
                )
                .oracle()
                .depends_on(apartment_action_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_poster_event,
            proposal_event,
            acceptance_event,
            apartment_action_event,
            logistics_action_event,
        ]

    def validate(
        self,
        env: AbstractEnvironment,
    ) -> ScenarioValidationResult:
        """Validate multimodal proactive shopping behavior."""
        try:
            log_entries = env.event_log.list_view()

            allow_any_event_type = bool(getattr(env, "oracle_mode", False))

            photo_visual_input_found = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any_event_type,
                image_path="/rental_poster_photo.jpg",
                email_id=self.rental_email_id,
            )

            listing_search_grounded_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulApartmentApp"
                and e.action.function_name in ("search_apartments", "save_apartment", "get_apartment_details")
                and (
                    self.target_apartment_id in str(e.action.args)
                    or "northside" in str(e.action.args).lower()
                    or str(self.target_listing_price) in str(e.action.args)
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

            viewing_schedule_or_cab_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and (
                    (
                        e.action.class_name == "StatefulCalendarApp"
                        and e.action.function_name == "get_calendar_events_from_to"
                    )
                    or (e.action.class_name == "StatefulCabApp" and e.action.function_name == "get_quotation")
                )
                for e in log_entries
            )

            success = proposal_found and viewing_schedule_or_cab_found

            advisory_failures: list[str] = []
            if not photo_visual_input_found:
                advisory_failures.append(
                    "agent never accessed the rental poster image (no Files read of /rental_poster_photo.jpg "
                    "and no Email read/download for the inbox message with the attachment)"
                )
            if photo_visual_input_found and not listing_search_grounded_found:
                advisory_failures.append(
                    "agent viewed the poster but failed to ground a matching apartment search in Apartment"
                )

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append(
                        "agent grounded listings but failed to proactively propose viewing or logistics assistance"
                    )
                if not viewing_schedule_or_cab_found:
                    failed_checks.append(
                        "agent failed to combine schedule and transport (calendar availability query or cab quotation)"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(
                success=False,
                exception=e,
            )
