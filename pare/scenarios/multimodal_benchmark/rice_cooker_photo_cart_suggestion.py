"""Scenario: Agent suggests adding a photographed rice cooker to cart."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import (
    ScenarioStatus,
    ScenarioValidationResult,
)
from are.simulation.types import (
    AbstractEnvironment,
    Action,
    EventRegisterer,
    EventType,
)

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulEmailApp,
)
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario


@register_scenario("rice_cooker_photo_cart_suggestion")
class RiceCookerPhotoCartSuggestion(PAREScenario):
    """Agent infers shopping intent from a rice cooker photo.

    The user sends a real product photo attachment and implicitly expresses
    purchase intent. The assistant should:

    1. Read the incoming email with image attachment.
    2. Open / inspect the image attachment.
    3. Infer from visual evidence that the object is a rice cooker.
    4. Search the Shopping app for a matching product.
    5. Proactively ask for permission before cart modification.
    6. Add the rice cooker to cart after user approval.
    """

    start_time = datetime(2025, 11, 19, 12, 0, 0, tzinfo=UTC).timestamp()

    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_RICE_COOKER_PHOTO_PATH = (
        Path(__file__).parent / "assets" / "rice_cooker_photo_cart_suggestion" / "photo.jpg"
    )

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")

        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.shopping = StatefulShoppingApp(name="Shopping")

        local_photo_path = Path(
            os.getenv("PARE_RICE_COOKER_PHOTO_LOCAL_PATH", str(self.DEFAULT_LOCAL_RICE_COOKER_PHOTO_PATH))
        )

        if not local_photo_path.exists():
            raise FileNotFoundError(f"Rice cooker photo not found: {local_photo_path}")

        with self.files.open("/photo.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))

        self.user_photo_email_id = "email-user-rice-cooker-photo"

        rice_cooker_pid = self.shopping.add_product(name="HomeEase Rice Cooker 3L")
        self.rice_cooker_product_id = rice_cooker_pid

        self.rice_cooker_item_id = self.shopping.add_item_to_product(
            product_id=rice_cooker_pid,
            price=79.99,
            options={
                "color": "white",
                "capacity_l": 3.0,
                "material": "nonstick inner pot",
                "warranty": "2 years",
            },
            available=True,
        )

        kettle_pid = self.shopping.add_product(name="QuickBoil Electric Kettle 1.8L")

        self.kettle_item_id = self.shopping.add_item_to_product(
            product_id=kettle_pid,
            price=39.99,
            options={
                "color": "silver",
                "capacity_l": 1.8,
                "power_w": 1500,
            },
            available=True,
        )

        blender_pid = self.shopping.add_product(name="FreshBlend Blender 700W")

        self.blender_item_id = self.shopping.add_item_to_product(
            product_id=blender_pid,
            price=59.99,
            options={
                "color": "black",
                "power_w": 700,
                "jar_l": 1.5,
            },
            available=True,
        )

        self.apps = [
            self.agent_ui,
            self.system_app,
            self.files,
            self.email,
            self.shopping,
        ]

    def build_events_flow(self) -> None:
        """Build oracle flow."""
        aui = self.get_typed_app(PAREAgentUserInterface)

        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.user_photo_email_id,
                sender="user.mobile@local",
                subject="Item photo from store",
                content=(
                    "Took this at the store earlier — it's that white rice cooker on the end cap. "
                    "Didn't grab it in person but kinda want it if you can find the same one online and drop it in my cart? "
                    "No rush, just so I don't forget the model."
                ),
                attachment_paths=["/photo.jpg"],
            ).delayed(5)

            read_email_event = (
                email_app.get_email_by_id(email_id=self.user_photo_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_photo_event = (
                files_app.display(path="/photo.jpg").oracle().depends_on(read_email_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content="I found a matching rice cooker product from the attached image. Would you like me to add it to cart?"
                )
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please add it.").oracle().depends_on(proposal_event, delay_seconds=1)
            )

            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=self.rice_cooker_item_id, quantity=1)
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
                image_path="/photo.jpg",
                email_id=self.user_photo_email_id,
            )

            rice_cooker_grounded_found = any(
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
                    self.rice_cooker_item_id in str(e.action.args)
                    or self.rice_cooker_product_id in str(e.action.args)
                    or "rice cooker" in str(e.action.args).lower()
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

            rice_cooker_added_to_cart_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and str(e.action.args.get("item_id", "")) == self.rice_cooker_item_id
                and int(e.action.args.get("quantity", 0) or 0) >= 1
                for e in log_entries
            )

            success = (
                photo_visual_input_found
                and rice_cooker_grounded_found
                and proposal_found
                and rice_cooker_added_to_cart_found
            )

            if not success:
                failed_checks: list[str] = []

                if not photo_visual_input_found:
                    failed_checks.append(
                        "agent never accessed the product photo (no Files read of /photo.jpg and no "
                        "Email read/download for the inbox message with the attachment)"
                    )

                if photo_visual_input_found and not rice_cooker_grounded_found:
                    failed_checks.append(
                        "agent viewed the image but failed to ground the matching rice cooker in Shopping"
                    )

                if rice_cooker_grounded_found and not proposal_found:
                    failed_checks.append(
                        "agent found the rice cooker in Shopping but did not proactively propose adding it to cart"
                    )

                if not rice_cooker_added_to_cart_found:
                    failed_checks.append("agent failed to add the correct rice cooker line item to the cart")

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
