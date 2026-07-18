"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import (
    AbstractEnvironment,
    Action,
    Event,
    EventRegisterer,
    EventType,
)

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulMessagingApp,
    StatefulShoppingApp,
)
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

# TODO: replace these with paths from the resolved VisualAssetSpec / asset manifest.
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "duplicate_plant_order_cancel"
)


@register_scenario("duplicate_plant_order_cancel")
class DuplicatePlantOrderCancel(PAREScenario):
    """A friend offers a free houseplant via a Messages photo attachment, and the agent cancels the user's duplicate pending Shopping order after visually confirming the species match.

A friend sends a message with a photo of a healthy houseplant in their apartment: they are moving next week, cannot take their plants, and offer this one free if the user picks it up this weekend, adding it "could save you from having to buy one." The photo is delivered as a Messages attachment; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it. The assistant must: (1) read the incoming message, (2) view the photo and infer from the split-leaf visual evidence that the plant is a Monstera deliciosa, (3) list the user's Shopping orders and view the single pending (not-yet-delivered) order to confirm its line item is a Monstera deliciosa of the same species, (4) proactively propose canceling the pending paid order and replying to accept the friend's free plant, and (5) after user acceptance, cancel the order and send a reply to the friend. The photo is required because the message only says "this plant" without naming the species; vision identifies the Monstera so the agent can safely match it against the user's pending order line item and avoid canceling an order for a different plant.

This scenario exercises multimodal plant-species identification from a single photo, cross-app order lookup and matching via Shopping's list_orders / view_order, the rarely-used cancel_order side effect, and a user-gated cancellation plus an outbound reply in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # TODO: Initialize scenario specific apps here.
        # Visual assets should be loaded from the asset manifest / resolved asset directory,
        # written into self.files with jpeg_bytes_for_sandbox(...), and then attached
        # through Email, Album, Notes, or Files according to the VisualAssetSpec.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Load the friend's houseplant photo into the sandbox filesystem so Step 3
        # can deliver it as a Messages attachment via create_and_add_message(...).
        self.houseplant_sandbox_path = "/houseplant_monstera.jpg"
        local_image_path = SCENARIO_ASSET_DIR / "IMG_2411.jpg"
        if not local_image_path.exists():
            raise FileNotFoundError(
                f"Houseplant photo not found: {local_image_path}. "
                f"Place IMG_2411.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(self.houseplant_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_image_path.read_bytes()))

        # TODO: Populate apps with scenario specific data here.
        # Messaging: seed the friend contact and a pre-existing 1:1 conversation.
        # The photo + plant-offer message itself is delivered as an environment
        # event in Step 3 (the trigger), NOT seeded silently here.
        friend_name = "Maya Rivera"
        friend_phone = "+1-555-0148"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")
        self.user_id = self.messaging.current_user_id

        plant_chat = ConversationV2(
            participant_ids=[self.user_id, self.friend_id],
            title="Maya Rivera",
            messages=[
                MessageV2(
                    sender_id=self.friend_id,
                    content=(
                        "Hey! It's been a while — how have you been? "
                        "We should catch up soon."
                    ),
                    timestamp=self.start_time - 5 * 86400,
                ),
                MessageV2(
                    sender_id=self.user_id,
                    content="Hey Maya! All good on my end. Would love to catch up :)",
                    timestamp=self.start_time - 5 * 86400 + 3600,
                ),
            ],
        )
        plant_chat.update_last_updated(self.start_time - 5 * 86400 + 3600)
        self.messaging.add_conversation(plant_chat)
        self.conversation_id = plant_chat.conversation_id

        # Shopping: seed a product catalog and the user's order history.
        # One pending (not-yet-delivered) Monstera order that the agent should
        # cancel after visually confirming the species match, plus one already
        # delivered order for a different plant so the agent must distinguish.
        monstera_pid = self.shopping.add_product(name="Monstera Deliciosa Houseplant")
        self.monstera_item_id = self.shopping.add_item_to_product(
            product_id=monstera_pid,
            price=32.0,
            options={
                "pot_size": "6 inch",
                "pot_material": "terracotta",
                "maturity": "established",
            },
            available=True,
        )

        snake_pid = self.shopping.add_product(name="Snake Plant Sansevieria")
        self.snake_item_id = self.shopping.add_item_to_product(
            product_id=snake_pid,
            price=24.0,
            options={
                "pot_size": "4 inch",
                "pot_material": "ceramic",
            },
            available=True,
        )

        self.pending_order_id = "order-monstera-pending-001"
        self.shopping.add_order(
            order_id=self.pending_order_id,
            order_status="processed",
            order_date=self.start_time - 2 * 86400,
            order_total=32.0,
            item_id=self.monstera_item_id,
            quantity=1,
        )

        self.delivered_order_id = "order-snake-plant-delivered-002"
        self.shopping.add_order(
            order_id=self.delivered_order_id,
            order_status="delivered",
            order_date=self.start_time - 12 * 86400,
            order_total=24.0,
            item_id=self.snake_item_id,
            quantity=1,
        )

        # TODO: Register all apps here in self.apps
        self.apps = [
            self.agent_ui,
            self.system_app,
            self.files,
            self.messaging,
            self.shopping,
        ]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment trigger: friend sends a photo + free-plant offer.
            # The photo attachment carries the species evidence; the text says "this plant"
            # without naming it, forcing the agent to inspect the image to identify it.
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=self.conversation_id,
                sender_id=self.friend_id,
                content=(
                    "Hey! I'm moving next week and can't take my plants with me. "
                    "Want this one for free if you can pick it up this weekend? "
                    "It's been on my windowsill for ages — could save you from having to buy one."
                ),
                attachment_path=self.houseplant_sandbox_path,
            ).delayed(5)

            # Oracle: read the friend's message to see the offer text and the photo attachment
            # (motivated by the incoming_message_event notification "could save you from having to buy one").
            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=self.conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            # Oracle: explicitly display the attached houseplant photo from the sandbox FS so the
            # agent can visually inspect the leaves and infer the species (split-leaf fenestrations
            # => Monstera deliciosa). Motivated by the photo attachment surfaced by read_message_event.
            view_photo_event = (
                files.display(path=self.houseplant_sandbox_path)
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            # Oracle: list the user's Shopping orders to find any pending (not-yet-delivered) order
            # that might be a duplicate of the free plant. Motivated by the friend's "could save you
            # from having to buy one" line in the incoming message.
            list_orders_event = (
                shopping_app.list_orders().oracle().depends_on(view_photo_event, delay_seconds=2)
            )

            # Oracle: inspect the pending Monstera order's line item to confirm the species matches
            # the visually identified Monstera. The order_id is revealed by list_orders_event's output.
            view_pending_order_event = (
                shopping_app.get_order_details(order_id=self.pending_order_id)
                .oracle()
                .depends_on(list_orders_event, delay_seconds=2)
            )

            # Oracle proposal: cite the friend's free-plant offer (incoming_message_event) and the
            # matching pending Monstera order (view_pending_order_event) — propose canceling the
            # pending order and replying to accept the friend's plant.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya just offered you a free houseplant in Messages (she's moving and "
                        "can't take it). I opened the photo — the split-leaf fenestrations look "
                        "like a Monstera deliciosa, which matches the pending Monstera in your "
                        "Shopping orders. Want me to cancel that pending order and reply to Maya "
                        "to accept the free plant?"
                    )
                )
                .oracle()
                .depends_on(view_pending_order_event, delay_seconds=2)
            )

            # User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please cancel the pending Monstera order and tell Maya I'll take it."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Oracle write: cancel the pending Monstera order (user-gated via acceptance_event).
            cancel_order_event = (
                shopping_app.cancel_order(order_id=self.pending_order_id)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Oracle write: reply to the friend in the existing 1:1 conversation to accept the plant
            # (user-gated via acceptance_event). The friend_id was revealed by read_message_event
            # (sender of the incoming offer).
            reply_to_friend_event = (
                messaging_app.send_message(
                    user_id=self.friend_id,
                    content=(
                        "Maya — yes please, I'd love to take it! I'll swing by this weekend to "
                        "pick it up. Thanks so much :)"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=3)
            )

        # TODO: Register ALL events here in self.events
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            view_photo_event,
            list_orders_event,
            view_pending_order_event,
            proposal_event,
            acceptance_event,
            cancel_order_event,
            reply_to_friend_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            agent_entries = [
                e for e in log_entries if e.event_type == EventType.AGENT
            ]

            # Check 1 — Proposal: agent offered proactive help to the user via
            # PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: agent completed BOTH promised user-gated writes
            # correctly: (a) canceled the pending Monstera Shopping order, and
            # (b) replied to the friend in Messages to accept the free plant.
            # Both writes must appear with the correct structural identifiers
            # (the seeded pending order_id and the friend's messaging user_id).
            order_cancelled = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "cancel_order"
                and e.action.args.get("order_id") == self.pending_order_id
                for e in agent_entries
            )

            replied_to_friend = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message"
                and e.action.args.get("user_id") == self.friend_id
                for e in agent_entries
            )

            task_completed = order_cancelled and replied_to_friend

            success = proposal_found and task_completed

            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not order_cancelled and not replied_to_friend:
                    rationale = (
                        "task not completed: pending Monstera order not cancelled "
                        "and no reply sent to the friend"
                    )
                elif not order_cancelled:
                    rationale = (
                        "task not completed: pending Monstera order not cancelled"
                    )
                else:
                    rationale = "task not completed: no reply sent to the friend"
                return ScenarioValidationResult(success=False, rationale=rationale)

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
