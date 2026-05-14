"""Scenario: Agent handles damaged product photo with proactive after-sales workflow."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulEmailApp
from pare.apps.reminder import StatefulReminderApp
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario


@register_scenario("damaged_product_photo_after_sales_workflow")
class DamagedProductPhotoAfterSalesWorkflow(PAREScenario):
    """Agent handles damaged-product after-sales workflow from an image report.

    A damaged product photo and short complaint text arrive via email attachment. The assistant must:
    1. Read the message and inspect the image first.
    2. Locate the related order.
    3. Ask one proactive accept/reject permission question before execution.
    4. After acceptance, complete claim email action and create follow-up reminder.

    Constraints:
    - Image reading/browsing is allowed before permission.
    - Do not ask for extra information.
    - User responses should remain accept/reject style.
    """

    start_time = datetime(2025, 11, 21, 18, 30, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LOCAL_DAMAGE_PHOTO_PATH = Path(__file__).parent / "assets" / "damaged_blender_photo.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Initialize apps and seed the damaged-photo email plus shopping order history."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        self.shopping = StatefulShoppingApp(name="Shopping")
        self.reminder = StatefulReminderApp(name="Reminders")

        local_damage_path = Path(
            os.getenv("PARE_DAMAGED_PRODUCT_PHOTO_LOCAL_PATH", str(self.DEFAULT_LOCAL_DAMAGE_PHOTO_PATH))
        )
        if not local_damage_path.exists():
            raise FileNotFoundError(
                f"Damaged product photo not found: {local_damage_path}. "
                "Set PARE_DAMAGED_PRODUCT_PHOTO_LOCAL_PATH or place the image at "
                f"{self.DEFAULT_LOCAL_DAMAGE_PHOTO_PATH}."
            )
        with self.files.open("/damaged_product_photo.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_damage_path.read_bytes()))

        base_dt = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.oracle_order_id = f"order_mixer_{base_dt.strftime('%Y%m%d')}"
        self.oracle_follow_up_due = (
            (base_dt + timedelta(days=3)).replace(hour=10, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
        )
        self.support_email_id = "damaged_item_report_email"
        # Seed shopping products and order history.
        mixer_pid = self.shopping.add_product(name="KitchenPro Portable Blender")
        self.mixer_product_id = mixer_pid

        self.target_item_id = self.shopping.add_item_to_product(
            product_id=mixer_pid,
            price=59.99,
            options={"color": "white", "capacity_ml": 600},
            available=True,
        )
        self.shopping.add_order(
            order_id=self.oracle_order_id,
            order_status="delivered",
            order_date=datetime(2025, 11, 20, 15, 45, 0, tzinfo=UTC).timestamp(),
            order_total=59.99,
            item_id=self.target_item_id,
            quantity=1,
        )

        # Distractor order
        lamp_pid = self.shopping.add_product(name="Desk Lamp Classic")
        lamp_item_id = self.shopping.add_item_to_product(
            product_id=lamp_pid,
            price=24.90,
            options={"color": "black"},
            available=True,
        )
        self.shopping.add_order(
            order_id="order_lamp_20251118",
            order_status="delivered",
            order_date=datetime(2025, 11, 18, 11, 0, 0, tzinfo=UTC).timestamp(),
            order_total=24.90,
            item_id=lamp_item_id,
            quantity=1,
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.shopping, self.reminder]

    def build_events_flow(self) -> None:
        """Build minimal executable oracle flow for after-sales handling."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            inject_email_event = email_app.send_email_to_user_with_id(
                email_id=self.support_email_id,
                sender="user.mobile@local",
                subject="Just arrived and already broken",
                content=(
                    "So this just showed up today and it literally won't turn on. "
                    "Box looks pretty roughed up on one side too — sending a photo. "
                    "It's the KitchenPro portable blender from yesterday's delivery. "
                    "Can you check the order for the details and help me get a replacement or refund going?"
                ),
                attachment_paths=["/damaged_product_photo.jpg"],
            ).delayed(10)

            read_email_event = (
                email_app.get_email_by_id(email_id=self.support_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(inject_email_event, delay_seconds=2)
            )

            view_photo_event = (
                files_app.display(path="/damaged_product_photo.jpg")
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            list_orders_event = shopping_app.list_orders().oracle().depends_on(view_photo_event, delay_seconds=1)

            proposal_event = (
                aui.send_message_to_user(
                    content="I found the related order and can draft/send the after-sales claim now. Proceed?"
                )
                .oracle()
                .depends_on(list_orders_event, delay_seconds=1)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please proceed.").oracle().depends_on(proposal_event, delay_seconds=1)
            )

            send_claim_event = (
                email_app.reply_to_email(
                    email_id=self.support_email_id,
                    folder_name="INBOX",
                    content="Hello support, item arrived damaged. Please initiate replacement/refund.",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            inject_email_event,
            read_email_event,
            view_photo_event,
            list_orders_event,
            proposal_event,
            acceptance_event,
            send_claim_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate that the agent inspected the photo and performed after-sales actions."""
        try:
            log_entries = env.event_log.list_view()

            allow_any_event_type = bool(getattr(env, "oracle_mode", False))

            photo_visual_input_found = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any_event_type,
                image_path="/damaged_product_photo.jpg",
                email_id=self.support_email_id,
            )

            related_order_grounded_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name in ("list_orders", "get_order_details")
                and (
                    e.action.function_name == "list_orders"
                    or self.oracle_order_id in str(e.action.args)
                    or self.target_item_id in str(e.action.args)
                    or self.mixer_product_id in str(e.action.args)
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

            claim_email_action_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name
                in (
                    "reply_to_email",
                    "send_email_to_user",
                    "send_email_to_user_with_id",
                )
                for e in log_entries
            )

            success = (
                photo_visual_input_found
                and related_order_grounded_found
                and proposal_found
                and claim_email_action_found
            )

            if not success:
                failed_checks: list[str] = []

                #
                # Failure analysis
                #

                if not photo_visual_input_found:
                    failed_checks.append(
                        "agent never accessed the damaged-product photo (no Files read of /damaged_product_photo.jpg "
                        "and no Email read/download for the inbox message with the attachment)"
                    )

                if photo_visual_input_found and not related_order_grounded_found:
                    failed_checks.append("agent viewed the photo but failed to inspect the related order in Shopping")

                if related_order_grounded_found and not proposal_found:
                    failed_checks.append(
                        "agent grounded the order but failed to proactively propose after-sales assistance"
                    )

                if not claim_email_action_found:
                    failed_checks.append(
                        "agent did not complete an outbound email action for the after-sales claim "
                        "(reply_to_email, send_email_to_user, or send_email_to_user_with_id)"
                    )

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
