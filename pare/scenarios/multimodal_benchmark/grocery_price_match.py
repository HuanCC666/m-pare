"""Scenario: Agent checks if a purchased item is worth it from a message image."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import HomeScreenSystemApp, PAREAgentUserInterface, StatefulMessagingApp
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "grocery_price_match"
_RECEIPT_ASSET_IMAGE = _ASSETS / "grocery.jpg"
_RECEIPT_INTERNAL_PATH = "/photos/grocery.jpg"

_ORACLE_PRODUCT = "FarmFresh Organic Milk 1L"


@register_scenario("grocery_price_match")
class ReceiptNotePriceMatchCartSuggestion(PAREScenario):
    """Agent price-checks a purchased milk item from a receipt photo in Messages.

    A friend sends a message with a receipt image and asks if the milk purchase is worth it.
    The assistant must:
    1. Read the incoming message with receipt attachment.
    2. View the receipt photo (vision) to ground the price-check.
    3. Search Shopping and identify a cheaper matching milk item.
    4. Propose adding it to cart; add only after accept/reject acceptance.

    Constraints:
    - Proactive: one permission proposal before cart writes.
    - User responses are accept/reject only.
    - Message text does not carry the paid price or the cheaper SKU name.
    """

    start_time = datetime(2025, 11, 25, 12, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed Files, Shopping catalog, and a message trigger."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")

        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Seed receipt image into Files for message attachment viewing.
        local_path = _RECEIPT_ASSET_IMAGE
        if not local_path.exists():
            raise FileNotFoundError(f"Receipt image not found: {local_path}. Place grocery.jpg under {_ASSETS}.")
        self.files.mkdir("/photos", create_parents=True)
        with self.files.open(_RECEIPT_INTERNAL_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_path.read_bytes()))

        friend_name = "Maya Park"
        friend_phone = "+1-555-0146"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        self.messaging.add_conversation(conversation)

        # Shopping catalog setup
        target_pid = self.shopping.add_product(name=_ORACLE_PRODUCT)
        self.cheaper_item_id = self.shopping.add_item_to_product(
            product_id=target_pid,
            price=3.49,
            options={"size": "1L", "organic": True},
            available=True,
        )

        decoy_pid = self.shopping.add_product(name="Valley Crest Whole Milk 1L")
        self.decoy_item_id = self.shopping.add_item_to_product(
            product_id=decoy_pid,
            price=4.79,
            options={"size": "1L"},
            available=True,
        )

        premium_pid = self.shopping.add_product(name="Organic Valley Whole Milk 1L")
        self.premium_item_id = self.shopping.add_item_to_product(
            product_id=premium_pid,
            price=5.29,
            options={"size": "1L", "organic": True},
            available=True,
        )

        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.shopping]

    def build_events_flow(self) -> None:
        """Oracle: incoming message → view attachment → search → propose → add to cart."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        files_app = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")
        conversation_ids = messaging_app.get_existing_conversation_ids([self.friend_id])
        conversation_id = conversation_ids[0]

        with EventRegisterer.capture_mode():
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=conversation_id,
                sender_id=self.friend_id,
                content=(
                    "Our milk is running low. I need to buy some more."
                    "I'm in the market now. Do you think this is a good price? Should I buy it?"
                ),
                attachment_path=_RECEIPT_INTERNAL_PATH,
            ).delayed(3)

            read_message_event = (
                messaging_app.read_conversation(conversation_id=conversation_id, offset=0, limit=10)
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            view_receipt_event = (
                files_app.display(path=_RECEIPT_INTERNAL_PATH).oracle().depends_on(read_message_event, delay_seconds=1)
            )

            search_event = (
                shopping_app.search_product(product_name="organic milk")
                .oracle()
                .depends_on(view_receipt_event, delay_seconds=2)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I inspected the receipt photo and found a cheaper matching milk on Shopping. "
                        "Would you like me to add the cheaper item to your cart?"
                    )
                )
                .oracle()
                .depends_on(search_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, add it to my cart.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=self.cheaper_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            incoming_message_event,
            read_message_event,
            view_receipt_event,
            search_event,
            proposal_event,
            acceptance_event,
            add_to_cart_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate attachment viewing, shopping grounding, proposal, and correct cart line."""
        try:
            log_entries = env.event_log.list_view()
            allow_any_event_type = bool(getattr(env, "oracle_mode", False))

            message_read_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.function_name == "read_conversation"
                for e in log_entries
            )

            receipt_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any_event_type,
                image_paths={_RECEIPT_INTERNAL_PATH},
            )

            shopping_grounded = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.function_name
                in ("search_product", "list_all_products", "get_product_details", "add_to_cart")
                and (
                    self.cheaper_item_id in str(e.action.args)
                    or "organic milk" in str(e.action.args).lower()
                    or "farmfresh" in str(e.action.args).lower()
                )
                for e in log_entries
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.function_name == "send_message_to_user"
                and any(token in str(e.action.args).lower() for token in ("milk", "cheaper", "shopping", "add", "cart"))
                for e in log_entries
            )

            correct_cart = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.function_name == "add_to_cart"
                and str(e.action.args.get("item_id", "")) == self.cheaper_item_id
                and int(e.action.args.get("quantity", 0) or 0) >= 1
                for e in log_entries
            )

            wrong_cart = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.function_name == "add_to_cart"
                and str(e.action.args.get("item_id", "")) in {self.decoy_item_id, self.premium_item_id}
                for e in log_entries
            )

            success = (
                message_read_found
                and receipt_viewed
                and shopping_grounded
                and proposal_found
                and correct_cart
                and not wrong_cart
            )

            if not success:
                failed: list[str] = []
                if not message_read_found:
                    failed.append("agent did not read the incoming message in Messages")
                if not receipt_viewed:
                    failed.append("agent did not visually inspect the receipt image attachment")
                if not shopping_grounded:
                    failed.append("agent did not search or browse Shopping after viewing the receipt")
                if not proposal_found:
                    failed.append("agent did not proactively propose adding the cheaper item")
                if not correct_cart:
                    failed.append(f"agent did not add the cheaper item (item {self.cheaper_item_id}) to cart")
                if wrong_cart:
                    failed.append("agent added a decoy or premium milk SKU instead of the cheaper match")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
