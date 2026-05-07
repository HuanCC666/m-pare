"""Scenario: Agent suggests adding a photographed rice cooker to cart."""

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
    StatefulEmailApp,
)
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario


@register_scenario("rice_cooker_photo_cart_suggestion")
class RiceCookerPhotoCartSuggestion(PAREScenario):
    """Agent infers shopping intent from a rice cooker photo and proposes cart action.

    The user is outside and sends a photo of a rice cooker they want. The assistant must:
    1. Read the incoming message that includes the image attachment.
    2. Ground intent from photo context ("I want this rice cooker").
    3. Search the shopping app for the matching product.
    4. Proactively suggest adding the product to cart.
    5. After user acceptance, add the selected rice cooker to cart.

    This scenario focuses on multimodal intent grounding (image + short text),
    shopping retrieval, and proactive cart assistance.

    Constraints:
    - The assistant can read the message and inspect the image without permission.
    - Before shopping/cart actions, it should ask one proactive accept/reject permission question.
    - The user should not need to issue operational instructions and should only respond in accept/reject style.
    """

    start_time = datetime(2025, 11, 19, 12, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    # Preferred: set env var PARE_RICE_COOKER_PHOTO_LOCAL_PATH to a local file path.
    # Fallback: in-repo default under this scenario directory.
    DEFAULT_LOCAL_RICE_COOKER_PHOTO_PATH = Path(__file__).parent / "assets" / "rice_cooker_photo.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed rice cooker catalog + image attachment."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.shopping = StatefulShoppingApp(name="Shopping")

        # Load a real rice cooker photo from local filesystem into sandbox FS for attachment.
        local_photo_path = Path(
            os.getenv("PARE_RICE_COOKER_PHOTO_LOCAL_PATH", str(self.DEFAULT_LOCAL_RICE_COOKER_PHOTO_PATH))
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Rice cooker photo not found: {local_photo_path}. "
                "Set PARE_RICE_COOKER_PHOTO_LOCAL_PATH or place the image at "
                f"{self.DEFAULT_LOCAL_RICE_COOKER_PHOTO_PATH}."
            )
        with self.files.open("/rice_cooker_photo.jpg", "wb") as f:
            f.write(local_photo_path.read_bytes())

        self.user_photo_email_id = "email-user-rice-cooker-photo"
        # Seed shopping catalog with a clear target product and distractors.
        target_pid = self.shopping.add_product(name="HeatMaster Rice Cooker HM-RC30 3L")
        self.target_item_id = self.shopping.add_item_to_product(
            product_id=target_pid,
            price=79.99,
            options={
                "color": "stainless steel",
                "capacity_l": 3.0,
                "model": "HM-RC30",
                "warranty": "2 years",
            },
            available=True,
        )

        other_pid = self.shopping.add_product(name="HeatMaster Rice Cooker HM-RC50 5L")
        self.secondary_item_id = self.shopping.add_item_to_product(
            product_id=other_pid,
            price=99.99,
            options={"color": "black", "capacity_l": 5.0, "model": "HM-RC50"},
            available=True,
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.shopping]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for photo-driven cart assistance."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            inject_email_event = (
                email_app.send_email_to_user_with_id(
                    email_id=self.user_photo_email_id,
                    sender="user.mobile@local",
                    subject="Item photo from store",
                    content=(
                        "I just saw this in a store and I want it! "
                        "Check the attached photo and find it in Shopping for me - "
                        "can you add it to my cart?"
                    ),
                    attachment_paths=["/rice_cooker_photo.jpg"],
                )
                .oracle()
                .delayed(10)
            )

            read_email_event = (
                email_app.get_email_by_id(email_id=self.user_photo_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_photo_event = (
                files_app.display(path="/rice_cooker_photo.jpg").oracle().depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content="I found the matching rice cooker from the photo and can add it to cart. Proceed?"
                )
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please go ahead.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=self.target_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_photo_event,
            proposal_event,
            acceptance_event,
            add_to_cart_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate simple flow correctness for photo-driven cart assistance."""
        try:
            log_entries = env.event_log.list_view()

            viewed_image_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "SandboxLocalFileSystem"
                and e.action.function_name in ("display", "cat", "read_document")
                and "rice_cooker" in str(e.action.args.get("path", ""))
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            add_to_cart_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and int(e.action.args.get("quantity", 0) or 0) >= 1
                for e in log_entries
            )

            success = viewed_image_found and proposal_found and add_to_cart_found
            if not success:
                failed_checks = []
                if not viewed_image_found:
                    failed_checks.append("agent did not view the photo via Files tools")
                if not proposal_found:
                    failed_checks.append("(info) agent did not send a user-facing proposal")
                if not add_to_cart_found:
                    failed_checks.append("agent did not add an item to cart")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed_checks))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
