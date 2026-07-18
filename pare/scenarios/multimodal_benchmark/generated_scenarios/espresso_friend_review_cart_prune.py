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
DEFAULT_ESPRESSO_PHOTO_PATH = (
    "/Users/jasonz/Projects/m-pare/pare/scenarios/multimodal_benchmark/assets/"
    "image_assets/espresso_friend_review_cart_prune/espresso_counter.jpg"
)


@register_scenario("espresso_friend_review_cart_prune")
class EspressoFriendReviewCartPrune(PAREScenario):
    """A friend messages a photo of a kitchen appliance she just bought and warns the user it is far bulkier than the listing photos suggested, asking the user to measure before checking out; the agent visually matches the photo to the right line item in the user's cart and offers to remove it.

The photo (a real shot of an espresso machine sitting on a kitchen counter, partially tucked under the cabinets with a steaming wand visible) is delivered as a Messages attachment in an existing one-to-one conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it. The friend's message reads: "Got the one you were eyeing last week — heads up, it's way bigger than the listing photos looked. Barely fits under my cabinets. Might want to measure your counter before you check out." The message does not name the product, and the user's Shopping cart currently holds several small appliances (an espresso machine, a milk frother, and a coffee grinder), so the assistant must: (1) read the incoming message in Messages, (2) view the photo and visually identify the appliance as an espresso machine (so it can be matched to the correct cart line item, not the frother or grinder), (3) list the current Shopping cart via list_cart to surface all line items and their item ids, (4) proactively propose removing the espresso machine from the cart and replying to the friend thanking her for the heads-up, and (5) after the user accepts, remove the espresso machine line item from the cart and send the thank-you reply to the same conversation.

The photo is required because the message only says "the one you were eyeing" without naming the product, and the cart contains multiple candidate appliances; vision identifies the appliance type (espresso machine with a steam wand) so the agent can match it to the correct cart line item, and the cart-removal depends on that visual match. This scenario exercises multimodal appliance-type identification from a real-world in-use photo (not product packaging), cart inspection via list_cart, the rarely-used remove_from_cart side effect used for a decline rather than a swap, and a user-gated cart prune plus an outbound thank-you reply in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging app (existing one-to-one conversation with a friend) ---
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # --- Shopping app (small-appliance comparison cart state) ---
        self.shopping = StatefulShoppingApp(name="Shopping")

        # --- Visual asset: amateur counter photo of an espresso machine,
        #     written into the Files sandbox so it can be displayed to the
        #     agent during the run. ---
        local_espresso_photo_path = Path(
            os.getenv("PARE_ESPRESSO_PHOTO_LOCAL_PATH", str(DEFAULT_ESPRESSO_PHOTO_PATH))
        )
        if not local_espresso_photo_path.exists():
            raise FileNotFoundError(
                f"Espresso counter photo not found: {local_espresso_photo_path}. "
                f"Place espresso_counter.jpg under {DEFAULT_ESPRESSO_PHOTO_PATH}."
            )
        with self.files.open("/espresso_counter.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_espresso_photo_path.read_bytes()))
        self.espresso_photo_sandbox_path = "/espresso_counter.jpg"

        # --- Shopping catalog: three small kitchen appliances the user has
        #     been comparison-shopping. Each is its own product so the cart
        #     holds three distinct candidate line items. ---
        espresso_product_id = self.shopping.add_product(name="BrewPro Espresso Machine 15 Bar")
        self.espresso_product_id = espresso_product_id
        self.espresso_item_id = self.shopping.add_item_to_product(
            product_id=espresso_product_id,
            price=249.99,
            options={
                "finish": "stainless steel",
                "pressure_bar": 15,
                "features": ["steam wand", "portafilter", "drip tray"],
                "warranty": "1 year",
            },
            available=True,
        )

        frother_product_id = self.shopping.add_product(name="MilkFroth Handheld Frother")
        self.frother_product_id = frother_product_id
        self.frother_item_id = self.shopping.add_item_to_product(
            product_id=frother_product_id,
            price=14.99,
            options={
                "color": "black",
                "power": "battery operated",
                "type": "handheld milk frother",
            },
            available=True,
        )

        grinder_product_id = self.shopping.add_product(name="GrindRight Burr Coffee Grinder")
        self.grinder_product_id = grinder_product_id
        self.grinder_item_id = self.shopping.add_item_to_product(
            product_id=grinder_product_id,
            price=89.99,
            options={
                "color": "silver",
                "grind": "burr",
                "settings": 16,
            },
            available=True,
        )

        # Pre-existing cart: all three small appliances already added by the
        # user before start_time, so the agent must visually identify which
        # line item the friend's photo refers to.
        self.shopping.add_to_cart(item_id=self.espresso_item_id, quantity=1)
        self.shopping.add_to_cart(item_id=self.frother_item_id, quantity=1)
        self.shopping.add_to_cart(item_id=self.grinder_item_id, quantity=1)

        # --- Messaging: existing one-to-one conversation with the friend who
        #     bought the same espresso machine. The triggering message + photo
        #     arrive as an environment event in Step 3 (build_events_flow), not
        #     here; only pre-existing history is seeded now. ---
        friend_name = "Priya Sharma"
        friend_phone = "+1-555-0193"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")
        self.friend_name = friend_name

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        # Pre-existing conversation history from before start_time (the user
        # was "eyeing" the espresso machine last week, per the friend's trigger
        # message).
        last_week_ts = self.start_time - 7 * 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.friend_id,
                content=(
                    "Saw you bookmarking that BrewPro espresso machine last weekend — "
                    "have you pulled the trigger yet?"
                ),
                timestamp=last_week_ts,
            )
        )
        conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="Not yet, still debating. The listing photos make it look pretty compact.",
                timestamp=last_week_ts + 120,
            )
        )
        conversation.update_last_updated(last_week_ts + 120)
        self.messaging.add_conversation(conversation)
        self.friend_conversation_id = conversation.conversation_id

        # TODO: Register all apps here in self.apps
        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.shopping]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        # Plain values precomputed outside capture_mode() so we never pass Event
        # objects where simple strings/ids are required.
        friend_id = self.friend_id
        conversation_id = self.friend_conversation_id
        espresso_item_id = self.espresso_item_id
        espresso_photo_sandbox_path = self.espresso_photo_sandbox_path

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Friend Priya sends a photo + warning in the existing one-to-one
            # conversation. The message does NOT name the product; the agent must
            # inspect the attached photo to identify the appliance, then match it
            # to the right cart line item. `create_and_add_message` is a
            # template-covered env event for both user and agent streams.
            incoming_photo_message_event = messaging_app.create_and_add_message(
                conversation_id=conversation_id,
                sender_id=friend_id,
                content=(
                    "Got the one you were eyeing last week — heads up, it's way bigger "
                    "than the listing photos looked. Barely fits under my cabinets. "
                    "Might want to measure your counter before you check out."
                ),
                attachment_path=espresso_photo_sandbox_path,
            ).delayed(5)

            # --- ORACLE EVENTS ---
            # Motivation: the new incoming message from `incoming_photo_message_event`
            # references "the one you were eyeing last week" with an attached photo;
            # the agent reads the conversation to surface the message text + attachment.
            read_conversation_event = (
                messaging_app.read_conversation(conversation_id=conversation_id, offset=0, limit=10)
                .oracle()
                .depends_on(incoming_photo_message_event, delay_seconds=3)
            )

            # Motivation: the message asks the agent to gauge size from the photo
            # ("way bigger than the listing photos looked", "barely fits under my
            # cabinets"); the agent displays the attached image to visually identify
            # the appliance type before matching it to a cart line item.
            view_photo_event = (
                files.display(path=espresso_photo_sandbox_path)
                .oracle()
                .depends_on(read_conversation_event, delay_seconds=2)
            )

            # Motivation: the friend's message says "before you check out", implying
            # the item is already in the user's cart; the agent lists the cart to
            # surface candidate line items and their item_ids so the visually
            # identified espresso machine can be matched to the correct line.
            list_cart_event = (
                shopping_app.list_cart().oracle().depends_on(view_photo_event, delay_seconds=2)
            )

            # Motivation: grounded by `incoming_photo_message_event` ("way bigger
            # than the listing photos looked", "Might want to measure your counter
            # before you check out") plus the visual ID from `view_photo_event`
            # (espresso machine with steam wand / portafilter) and the cart line
            # items from `list_cart_event`; agent proposes removing the matched
            # espresso machine line item and replying to thank Priya.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Priya messaged a photo of the espresso machine she bought and "
                        "warned it's far bulkier than the listing photos — barely fits "
                        "under her cabinets. I viewed the photo and matched it to the "
                        "BrewPro Espresso Machine line item already in your cart (along "
                        "with the milk frother and coffee grinder). Want me to remove the "
                        "espresso machine from your cart and reply to Priya thanking her "
                        "for the heads-up?"
                    )
                )
                .oracle()
                .depends_on(list_cart_event, delay_seconds=2)
            )

            # User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please remove the espresso machine from my cart and thank Priya."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Motivation: user accepted the proposal; agent performs the promised
            # cart prune of the visually matched espresso machine line item.
            remove_from_cart_event = (
                shopping_app.remove_from_cart(item_id=espresso_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: user accepted the proposal which included thanking Priya;
            # agent sends the thank-you reply to the same one-to-one conversation.
            send_thank_you_event = (
                messaging_app.send_message(
                    user_id=friend_id,
                    content=(
                        "Thanks for the heads-up! Just pulled the espresso machine from "
                        "my cart — I'll measure the counter before reconsidering. Appreciate it."
                    ),
                )
                .oracle()
                .depends_on(remove_from_cart_event, delay_seconds=2)
            )

        # TODO: Register ALL events here in self.events
        self.events: list[Event] = [
            incoming_photo_message_event,
            read_conversation_event,
            view_photo_event,
            list_cart_event,
            proposal_event,
            acceptance_event,
            remove_from_cart_event,
            send_thank_you_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import EventType

            log_entries = env.event_log.list_view()

            espresso_item_id = self.espresso_item_id
            friend_id = self.friend_id

            # --- Check 1 — Proposal: agent offered proactive help via the
            # PAREAgentUserInterface.send_message_to_user(...) tool. ---
            proposal_found = any(
                e.event_type == EventType.AGENT
                and getattr(e.action, "class_name", None) == "PAREAgentUserInterface"
                and getattr(e.action, "function_name", None) == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2 — Task: agent completed the promised cart prune AND
            # sent the thank-you reply to the friend. Both coordinated writes
            # must be present in the AGENT event log with the correct
            # structural identifiers (espresso cart line item id, friend id). ---
            cart_pruned = False
            thank_you_sent = False
            for e in log_entries:
                if e.event_type != EventType.AGENT:
                    continue
                action = e.action
                class_name = getattr(action, "class_name", None)
                function_name = getattr(action, "function_name", None)
                args = (
                    action.resolved_args if getattr(action, "resolved_args", None) else action.args
                ) if action is not None else {}
                # Drop the implicit self arg if present.
                args = {k: v for k, v in args.items() if k != "self"}

                if (
                    class_name == "StatefulShoppingApp"
                    and function_name == "remove_from_cart"
                    and args.get("item_id") == espresso_item_id
                ):
                    cart_pruned = True

                if (
                    class_name == "StatefulMessagingApp"
                    and function_name == "send_message"
                    and args.get("user_id") == friend_id
                ):
                    thank_you_sent = True

            task_completed = cart_pruned and thank_you_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found:
                rationale = "no proactive proposal found"
            elif not cart_pruned and not thank_you_sent:
                rationale = (
                    "task not completed: espresso machine not removed from cart and "
                    "thank-you reply not sent"
                )
            elif not cart_pruned:
                rationale = "task not completed: espresso machine not removed from cart"
            else:
                rationale = "task not completed: thank-you reply not sent to friend"
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
