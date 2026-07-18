"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
import os

from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulMessagingApp,
)
from pare.apps.shopping import StatefulShoppingApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

# TODO: replace these with paths from the resolved VisualAssetSpec / asset manifest.
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"
DEFAULT_LAMP_BASE_PHOTO_PATH = (
    "/Users/jasonz/Projects/m-pare/pare/scenarios/multimodal_benchmark/assets/"
    "image_assets/lamp_shade_finish_cart_prune/lamp_base_closeup.jpg"
)


@register_scenario("lamp_shade_finish_cart_prune")
class LampShadeFinishCartPrune(PAREScenario):
    """The user is out of town and a friend housesitting sends a Messages photo of the user's living-room table lamp with a cracked shade, asking the user to replace it. The user had already been comparison-shopping before the trip, so two candidate replacement shades (a brass-finish variant and a nickel-finish variant) are already sitting in the Shopping cart; the friend cannot tell which finish matches the lamp base from the room lighting and sends a close-up photo of just the base. The photo is delivered as a Messages attachment in the existing one-to-one conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it.

The friend's message reads: "Bad news — I knocked the table lamp in the living room and the shade cracked. You've got two replacement shades sitting in your cart (brass and nickel) but I can't tell which one matches your lamp base from here. I'm attaching a close-up of the base — pick the matching finish, drop the other one, and check out. Code LIGHT10 should work on one of them." The assistant must: (1) read the incoming message in Messages, (2) view the lamp-base photo and infer from visual evidence that the base is brass-finish (so the brass shade is the match, not the nickel one), (3) list the current Shopping cart via list_cart to surface both candidate shade line items and their item ids, (4) call get_discount_code_info on LIGHT10 to confirm which shade it applies to, (5) proactively propose removing the nickel shade and checking out with the brass shade plus the LIGHT10 discount, and (6) after the user accepts, remove the nickel shade from the cart and check out with the LIGHT10 code. The photo is required because the friend's message names both candidate finishes but cannot tell which matches the lamp base; vision identifies the brass base, which determines which cart line item to keep, and the discount-code verification depends on first grounding the surviving variant.

This scenario exercises multimodal finish/material identification from a close-up product photo, cart inspection via list_cart, the rarely-used remove_from_cart side effect combined with discount-code verification (get_discount_code_info) and a discounted checkout, and a user-gated cart prune plus checkout in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging app (friend housesitter conversation, pre-existing) ---
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # --- Shopping app (pre-trip comparison shopping cart state) ---
        self.shopping = StatefulShoppingApp(name="Shopping")

        # --- Visual asset: close-up of the lamp base, written into Files sandbox ---
        local_lamp_photo_path = Path(
            os.getenv("PARE_LAMP_BASE_PHOTO_LOCAL_PATH", str(DEFAULT_LAMP_BASE_PHOTO_PATH))
        )
        if not local_lamp_photo_path.exists():
            raise FileNotFoundError(
                f"Lamp base close-up photo not found: {local_lamp_photo_path}. "
                f"Place lamp_base_closeup.jpg under {DEFAULT_LAMP_BASE_PHOTO_PATH}."
            )
        with self.files.open("/lamp_base_closeup.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_lamp_photo_path.read_bytes()))
        self.lamp_base_photo_sandbox_path = "/lamp_base_closeup.jpg"

        # --- Shopping catalog: one lamp shade product with two finish variants ---
        lamp_shade_product_id = self.shopping.add_product(name="Lumina Drum Lamp Shade 12in")
        self.lamp_shade_product_id = lamp_shade_product_id

        self.brass_shade_item_id = self.shopping.add_item_to_product(
            product_id=lamp_shade_product_id,
            price=42.99,
            options={
                "finish": "brass",
                "color": "warm golden yellowish-gold metallic",
                "diameter_in": 12,
                "material": "metal",
            },
            available=True,
        )

        self.nickel_shade_item_id = self.shopping.add_item_to_product(
            product_id=lamp_shade_product_id,
            price=42.99,
            options={
                "finish": "nickel",
                "color": "cool silver metallic",
                "diameter_in": 12,
                "material": "metal",
            },
            available=True,
        )

        # Pre-trip cart: both candidate shades already added by the user before the trip.
        self.shopping.add_to_cart(item_id=self.brass_shade_item_id, quantity=1)
        self.shopping.add_to_cart(item_id=self.nickel_shade_item_id, quantity=1)

        # LIGHT10 discount code applies only to the brass shade variant (10% off).
        self.shopping.add_discount_code(
            item_id=self.brass_shade_item_id,
            discount_code={"LIGHT10": 10.0},
        )
        self.discount_code = "LIGHT10"

        # --- Messaging: existing one-to-one conversation with housesitter friend ---
        friend_name = "Maya Chen"
        friend_phone = "+1-555-0148"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")

        self.friend_name = friend_name
        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        # Pre-existing conversation history from before start_time.
        day_before_ts = self.start_time - 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.friend_id,
                content="Hey, made it to your place no problem. Watering the plants and keeping an eye on things.",
                timestamp=day_before_ts,
            )
        )
        conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="Awesome, thanks Maya! Make yourself at home.",
                timestamp=day_before_ts + 60,
            )
        )
        conversation.update_last_updated(day_before_ts + 60)
        self.messaging.add_conversation(conversation)
        self.friend_conversation_id = conversation.conversation_id

        # TODO: Register all apps here in self.apps
        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.shopping]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment event: friend (Maya) sends a Messages message
            #     with a close-up photo of the cracked lamp's base attached. This is
            #     the exogenous trigger that contains the user's instruction and the
            #     visual evidence (brass base) the agent must ground on.
            inject_message_event = messaging_app.create_and_add_message(
                conversation_id=self.friend_conversation_id,
                sender_id=self.friend_id,
                content=(
                    "Bad news — I knocked the table lamp in the living room and the shade cracked. "
                    "You've got two replacement shades sitting in your cart (brass and nickel) but I "
                    "can't tell which one matches your lamp base from here. I'm attaching a close-up "
                    "of the base — pick the matching finish, drop the other one, and check out. "
                    "Code LIGHT10 should work on one of them."
                ),
                attachment_path=self.lamp_base_photo_sandbox_path,
            ).delayed(5)

            # --- Oracle: agent reads the Messages conversation to see Maya's incoming
            #     message and access the attached lamp-base photo. Motivated by
            #     inject_message_event's notification ("New message from ... in
            #     conversation ...: Bad news — I knocked the table lamp...").
            read_conversation_event = (
                messaging_app.read_conversation(conversation_id=self.friend_conversation_id)
                .oracle()
                .depends_on(inject_message_event, delay_seconds=2)
            )

            # --- Oracle: agent displays the lamp-base photo at the sandbox path via
            #     Files to inspect the finish visually. Motivated by the
            #     FileMessageV2 attachment exposed by read_conversation_event and
            #     Maya's instruction "I'm attaching a close-up of the base — pick the
            #     matching finish".
            view_photo_event = (
                files.display(path=self.lamp_base_photo_sandbox_path)
                .oracle()
                .depends_on(read_conversation_event, delay_seconds=1)
            )

            # --- Oracle: agent lists the Shopping cart to surface both candidate
            #     shade line items and their item ids. Motivated by Maya's env cue
            #     "You've got two replacement shades sitting in your cart (brass and
            #     nickel)" — the agent needs the concrete item ids to act on them.
            list_cart_event = (
                shopping_app.list_cart()
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            # --- Oracle: agent verifies which shade LIGHT10 applies to. Motivated by
            #     Maya's env cue "Code LIGHT10 should work on one of them." — the
            #     agent must confirm the code applies to the surviving brass variant
            #     before proposing it.
            get_discount_code_info_event = (
                shopping_app.get_discount_code_info(discount_code=self.discount_code)
                .oracle()
                .depends_on(list_cart_event, delay_seconds=1)
            )

            # --- Oracle (proposal): agent proposes removing the nickel shade and
            #     checking out with the brass shade + LIGHT10. Grounded in
            #     inject_message_event's instruction ("pick the matching finish, drop
            #     the other one, and check out. Code LIGHT10 should work on one of
            #     them."), the visual evidence from view_photo_event (brass base), and
            #     get_discount_code_info_event (LIGHT10 applies to the brass item).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read Maya's message about the cracked lamp shade and inspected the close-up "
                        "photo of your lamp base — the base has a warm brass finish, so the brass shade "
                        "in your cart is the match, not the nickel one. I also confirmed the LIGHT10 "
                        "code applies to the brass shade. Want me to remove the nickel shade from the "
                        "cart and check out with just the brass shade using LIGHT10?"
                    )
                )
                .oracle()
                .depends_on(get_discount_code_info_event, delay_seconds=1)
            )

            # --- User accepts the proposal (Maya's instruction authorizes the prune
            #     + checkout; user confirms via the agent-user channel).
            acceptance_event = (
                aui.accept_proposal(content="Yes, please drop the nickel shade and check out with LIGHT10.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # --- Oracle (write): remove the nickel shade from the cart. Motivated by
            #     the user's acceptance of the proposal to "drop the nickel shade";
            #     the nickel item id was surfaced by list_cart_event.
            remove_nickel_event = (
                shopping_app.remove_from_cart(item_id=self.nickel_shade_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- Oracle (write): check out with LIGHT10 applied to the brass shade.
            #     Motivated by the user's acceptance and get_discount_code_info_event
            #     confirming LIGHT10 is valid for the brass item; runs after the
            #     nickel line item has been pruned so the all-or-nothing discount
            #     policy passes.
            checkout_event = (
                shopping_app.checkout(discount_code=self.discount_code)
                .oracle()
                .depends_on(remove_nickel_event, delay_seconds=1)
            )

        self.events: list[Event] = [
            inject_message_event,
            read_conversation_event,
            view_photo_event,
            list_cart_event,
            get_discount_code_info_event,
            proposal_event,
            acceptance_event,
            remove_nickel_event,
            checkout_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import Action, EventType

            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Check 1 — Proposal: agent proactively offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: agent completed the promised user-gated cart prune +
            # discounted checkout. Both coordinated writes must appear in the agent
            # event log:
            #   (a) remove_from_cart on the nickel shade line item (the non-matching
            #       variant that must be dropped), and
            #   (b) checkout with the LIGHT10 discount code applied to the surviving
            #       brass shade.
            nickel_shade_item_id = self.nickel_shade_item_id
            discount_code = self.discount_code

            def _args(action: Action) -> dict:
                return action.resolved_args if action.resolved_args else action.args

            removed_nickel = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "remove_from_cart"
                and _args(e.action).get("item_id") == nickel_shade_item_id
                for e in agent_entries
            )

            checked_out_with_discount = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "checkout"
                and _args(e.action).get("discount_code") == discount_code
                for e in agent_entries
            )

            task_completed = removed_nickel and checked_out_with_discount

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not removed_nickel:
                rationale = "task not completed: nickel shade not removed from cart"
            else:
                rationale = (
                    f"task not completed: checkout not completed with discount code "
                    f"{discount_code}"
                )
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
