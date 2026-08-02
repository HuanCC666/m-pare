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
DEFAULT_LOCAL_STICK_BLENDER_PHOTO_PATH = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "cart_swap_stick_blender_sale"
    / "IMG_2411.jpg"
)
STICK_BLENDER_PHOTO_SANDBOX_PATH = "/purple_stick_blender.jpg"


@register_scenario("cart_swap_stick_blender_sale")
class CartSwapStickBlenderSale(PAREScenario):
    """A friend spots a stick blender on sale at a kitchen shop and messages the user a photo, suggesting they swap out the pricier one currently sitting in their cart. The photo (a real product shot of a purple-handled immersion blender with a whisk attachment) is delivered as a Messages attachment in an existing one-to-one conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it. The message text reads: "Saw this at the shop — purple stick blender on sale for $32. You mentioned you were about to buy one — if you haven't checked out yet, swap it in? Way cheaper than yours." The assistant must: (1) read the incoming message, (2) view the photo and infer from visual evidence that the product is a purple-handled immersion/stick blender (so the correct variant can be matched in the catalog, not just any blender), (3) list the user's current Shopping cart to confirm it already contains an immersion blender line item, (4) search Shopping for a purple stick blender matching the photo and confirm the sale price, (5) proactively propose to the user: remove the existing immersion blender from the cart, add the cheaper purple one, and reply to the friend confirming the swap, and (6) after the user accepts, remove the old line item from the cart, add the new one, and send a reply to the same conversation. The photo is required because the message only says "purple stick blender" without naming the brand or model; vision determines which catalog variant matches, and the cart-swap depends on first grounding both the existing cart item (via list_cart) and the replacement product (via search_product). This scenario exercises multimodal product-variant identification, cart inspection via list_cart, the rarely-used remove_from_cart side effect combined with add_to_cart, and a user-gated cart swap plus an outbound reply in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging (friend 1:1 conversation, pre-existing baseline history) ---
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        friend_name = "Maya Chen"
        friend_phone = "+1-555-0145"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")

        friend_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        # Baseline history: yesterday the user told the friend they were about to buy an
        # immersion blender (this grounds the friend's "You mentioned you were about to buy
        # one" follow-up that triggers the run in Step 3).
        day_before_ts = self.start_time - 86_400
        user_msg = MessageV2(
            sender_id=self.messaging.current_user_id,
            content=(
                "About to order that ProBlend immersion blender from the kitchen shop "
                "— the 5-speed one with the whisk and chopper attachments. Cart's ready, "
                "just need to hit checkout."
            ),
            timestamp=day_before_ts,
        )
        friend_reply = MessageV2(
            sender_id=self.friend_id,
            content="Nice, sounds like a solid pick. Send me the listing when you do?",
            timestamp=day_before_ts + 600,
        )
        friend_conversation.messages.extend([user_msg, friend_reply])
        friend_conversation.update_last_updated(friend_reply.timestamp)
        self.messaging.add_conversation(friend_conversation)
        self.friend_conversation_id = friend_conversation.conversation_id

        # --- Shopping catalog + cart baseline ---
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Pricier immersion blender currently sitting in the user's cart (the one to swap out).
        problend_pid = self.shopping.add_product(name="ProBlend Immersion Blender 5-Speed")
        self.problend_product_id = problend_pid
        self.existing_blender_item_id = self.shopping.add_item_to_product(
            product_id=problend_pid,
            price=54.99,
            options={
                "color": "black",
                "attachments": "blender + whisk + chopper",
                "power_w": 300,
                "warranty": "1 year",
            },
            available=True,
        )
        # Seed the cart with the pricier immersion blender (quantity 1).
        self.shopping.add_to_cart(self.existing_blender_item_id, 1)

        # Cheaper purple stick blender matching the friend's photo (sale price $32).
        purple_pid = self.shopping.add_product(
            name="PurpleGrip Immersion Stick Blender with Whisk"
        )
        self.purple_product_id = purple_pid
        self.purple_blender_item_id = self.shopping.add_item_to_product(
            product_id=purple_pid,
            price=32.00,
            options={
                "color": "purple",
                "attachments": "blender + whisk",
                "power_w": 250,
                "warranty": "1 year",
            },
            available=True,
        )

        # Decoy products to make search non-trivial.
        decoy_kettle_pid = self.shopping.add_product(name="QuickBoil Electric Kettle 1.8L")
        self.shopping.add_item_to_product(
            product_id=decoy_kettle_pid,
            price=39.99,
            options={"color": "silver", "capacity_l": 1.8, "power_w": 1500},
            available=True,
        )
        decoy_standard_pid = self.shopping.add_product(name="FreshBlend Countertop Blender 700W")
        self.shopping.add_item_to_product(
            product_id=decoy_standard_pid,
            price=59.99,
            options={"color": "black", "power_w": 700, "jar_l": 1.5},
            available=True,
        )

        # --- Visual asset: load the purple stick blender photo into Files ---
        local_photo_path = Path(
            os.getenv(
                "PARE_STICK_BLENDER_PHOTO_LOCAL_PATH",
                str(DEFAULT_LOCAL_STICK_BLENDER_PHOTO_PATH),
            )
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Purple stick blender photo not found: {local_photo_path}. "
                f"Place IMG_2411.jpg under {DEFAULT_LOCAL_STICK_BLENDER_PHOTO_PATH.parent}."
            )
        with self.files.open(STICK_BLENDER_PHOTO_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))

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

        # Pre-compute plain string IDs outside capture_mode() so we never pass Event
        # objects into app tool calls.
        friend_id = self.friend_id
        conversation_id = self.friend_conversation_id
        existing_blender_item_id = self.existing_blender_item_id
        purple_blender_item_id = self.purple_blender_item_id

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Exogenous trigger: the friend (Maya) messages the user in the pre-existing
            # 1:1 conversation with a photo of a purple stick blender on sale for $32 and
            # suggests swapping out the pricier one in the user's cart. The photo is the
            # only source for the product variant — the message text gives no brand/model.
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=conversation_id,
                sender_id=friend_id,
                content=(
                    "Saw this at the shop — purple stick blender on sale for $32. "
                    "You mentioned you were about to buy one — if you haven't checked out "
                    "yet, swap it in? Way cheaper than yours."
                ),
                attachment_path=STICK_BLENDER_PHOTO_SANDBOX_PATH,
            ).delayed(3)

            # --- ORACLE EVENTS ---
            # Motivation: `incoming_message_event` delivered a new message from Maya in the
            # 1:1 conversation ("Saw this at the shop ... swap it in?"); read it to confirm
            # the request and the photo attachment.
            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=2)
            )

            # Motivation: `read_message_event` exposed an image attachment in Maya's message;
            # visually inspect it via Files to identify the product variant (purple-handled
            # stick blender with whisk) that the message text alone does not name.
            view_photo_event = (
                files.display(path=STICK_BLENDER_PHOTO_SANDBOX_PATH)
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            # Motivation: Maya's message says "swap it in? Way cheaper than yours" — the
            # agent checks the current Shopping cart to confirm it already contains an
            # immersion blender line item and to retrieve its item_id for the swap.
            list_cart_event = (
                shopping_app.list_cart().oracle().depends_on(view_photo_event, delay_seconds=2)
            )

            # Motivation: the viewed photo shows a purple-handled stick blender; search the
            # Shopping catalog for a matching purple stick blender variant to ground the
            # replacement item_id and confirm the $32 sale price.
            search_product_event = (
                shopping_app.search_product(product_name="purple stick blender", offset=0, limit=10)
                .oracle()
                .depends_on(list_cart_event, delay_seconds=2)
            )

            # Motivation: grounded by `incoming_message_event` ("swap it in? Way cheaper
            # than yours"), the cart contents from `list_cart_event` (existing immersion
            # blender at $54.99), and the photo + `search_product_event` (purple stick
            # blender at $32). Propose the cart swap + reply to Maya before any writes.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya messaged a photo of a purple-handled stick blender with whisk "
                        "attachment on sale for $32, suggesting I swap it for the immersion "
                        "blender currently in your cart ($54.99). I searched Shopping and "
                        "found the matching purple stick blender variant. Want me to remove "
                        "the existing immersion blender from the cart, add the purple one, "
                        "and reply to Maya confirming the swap?"
                    )
                )
                .oracle()
                .depends_on(search_product_event, delay_seconds=2)
            )

            # User accepts the agent's proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please swap the cart and reply to Maya."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Motivation: user accepted the proposal; remove the existing pricier immersion
            # blender line item (item_id grounded via `list_cart_event`) from the cart.
            remove_from_cart_event = (
                shopping_app.remove_from_cart(item_id=existing_blender_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: user accepted; add the cheaper purple stick blender variant
            # (item_id grounded via `search_product_event`) to the cart.
            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=purple_blender_item_id, quantity=1)
                .oracle()
                .depends_on(remove_from_cart_event, delay_seconds=2)
            )

            # Motivation: user accepted the proposal which included replying to Maya;
            # send the confirmation back to the same 1:1 conversation (recipient friend_id
            # grounded by `incoming_message_event`).
            reply_to_friend_event = (
                messaging_app.send_message(
                    user_id=friend_id,
                    content=(
                        "Thanks for the tip! Swapped the ProBlend out of my cart and added "
                        "the purple stick blender you spotted — saving the $23. Appreciate it!"
                    ),
                )
                .oracle()
                .depends_on(add_to_cart_event, delay_seconds=2)
            )

        # Register ALL events so they actually execute.
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            view_photo_event,
            list_cart_event,
            search_product_event,
            proposal_event,
            acceptance_event,
            remove_from_cart_event,
            add_to_cart_event,
            reply_to_friend_event,
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

            # Check 2 — Task: agent completed the promised cart swap + reply to Maya.
            # All three coordinated writes must be present in the agent event log:
            #   (a) remove_from_cart on the existing pricier immersion blender item,
            #   (b) add_to_cart on the cheaper purple stick blender item,
            #   (c) send_message reply to the friend (Maya) in the 1:1 conversation.
            existing_blender_item_id = self.existing_blender_item_id
            purple_blender_item_id = self.purple_blender_item_id
            friend_id = self.friend_id

            def _args(action: Action) -> dict:
                return (
                    action.resolved_args if action.resolved_args else action.args
                )

            removed_existing = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "remove_from_cart"
                and _args(e.action).get("item_id") == existing_blender_item_id
                for e in agent_entries
            )

            added_purple = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and _args(e.action).get("item_id") == purple_blender_item_id
                for e in agent_entries
            )

            replied_to_friend = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message"
                and _args(e.action).get("user_id") == friend_id
                for e in agent_entries
            )

            task_completed = removed_existing and added_purple and replied_to_friend

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not removed_existing:
                rationale = "task not completed: existing immersion blender not removed from cart"
            elif not added_purple:
                rationale = "task not completed: purple stick blender not added to cart"
            else:
                rationale = "task not completed: reply to Maya not sent"
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
