"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
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
PET_FOOD_PHOTO_ASSET_PATH = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "pet_food_variant_checkout"
    / "IMG_2411.jpg"
)
PET_FOOD_PHOTO_SANDBOX_PATH = "/IMG_2411.jpg"


@register_scenario("pet_food_variant_checkout")
class PetFoodVariantCheckout(PAREScenario):
    """A friend who is out of town and has the user pet-sitting their golden retriever sends a Messages photo of the nearly-empty dog food bag and asks the user to reorder the exact same one on their behalf. The photo (a real shot of an orange-brand dog food bag with a large-breed chicken formula look) is delivered as a Messages attachment in the existing one-to-one conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it. The message text reads: "Buddy's almost out of kibble — can you grab another bag of the same one? It's the orange bag, chicken, large breed, 12 lb. Just add it to your cart and check out, I'll Venmo you for it." The catalog's matching product has several variants (chicken / salmon / lamb, 6 lb / 12 lb / 24 lb) that differ by bag color and size, so the text alone does not disambiguate the variant.

The assistant must: (1) read the incoming message in Messages, (2) view the bag photo and infer from the orange bag visual that the formula is the chicken large-breed variant (not the blue salmon or green lamb bags), (3) search Shopping for a large-breed dog food product and use view_product / view_variant on the matching product to confirm the orange 12 lb chicken variant, (4) proactively propose adding that variant to the cart and checking out, and (5) after the user accepts, add the variant to the cart and complete checkout. The photo is required because the message names "orange bag, chicken" but the catalog lists variants by color/size, and the agent must visually match the bag in the photo to the correct variant before writing to the cart. This scenario exercises multimodal product-variant identification from packaging appearance, variant grounding via view_product and view_variant, and a user-gated cart add plus checkout on a friend's behalf in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging app with Files backend so photo attachments can be downloaded/viewed.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # Shopping app holds the catalog of dog food variants the agent must ground against.
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Load the dog-food bag photo into the sandbox Files system so Step 3 can attach it
        # to the incoming Messages event and the agent can later display() it for inspection.
        local_photo_path = PET_FOOD_PHOTO_ASSET_PATH
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Pet food bag photo not found: {local_photo_path}. "
                f"Place IMG_2411.jpg at {PET_FOOD_PHOTO_ASSET_PATH}."
            )
        with self.files.open(PET_FOOD_PHOTO_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))
        self.pet_food_photo_sandbox_path = PET_FOOD_PHOTO_SANDBOX_PATH

        # --- Messaging baseline state ---
        # Pre-existing one-to-one conversation with the friend who owns Buddy (the golden
        # retriever). Only baseline (pre-trigger) messages live here; the photo-bearing
        # request message that triggers the agent is injected in Step 3 as an env event.
        friend_name = "Riley Chen"
        friend_phone = "+1-555-0142"
        self.messaging.add_contacts([(friend_name, friend_phone)])
        self.friend_id = self.messaging.get_user_id(friend_name)
        if self.friend_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {friend_name}")

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.friend_id],
            title=friend_name,
        )
        # Baseline exchange from the day before start_time establishing the pet-sitting context.
        day_before_ts = self.start_time - 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.friend_id,
                content=(
                    "Heading out of town for the long weekend — thanks again for watching Buddy! "
                    "His food bin is in the pantry and there's a spare leash by the door."
                ),
                timestamp=day_before_ts,
            )
        )
        conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="No problem at all, have a safe trip! Buddy and I will be fine.",
                timestamp=day_before_ts + 600,
            )
        )
        conversation.update_last_updated(day_before_ts + 600)
        self.messaging.add_conversation(conversation)
        self.friend_conversation_id = conversation.conversation_id

        # --- Shopping catalog baseline state ---
        # One large-breed dry dog food product with 9 variants (3 formulas x 3 sizes).
        # Bag color encodes the formula: chicken=orange, salmon=blue, lamb=green.
        # The target variant matching the orange 12 lb chicken bag is recorded for Step 3/4.
        product_id = self.shopping.add_product(name="Pawsome Harvest Large Breed Dry Dog Food")
        self.pet_food_product_id = product_id

        formula_color = {
            "chicken": "orange",
            "salmon": "blue",
            "lamb": "green",
        }
        size_prices = {
            6: 27.99,
            12: 49.99,
            24: 89.99,
        }
        self.pet_food_variant_item_ids: dict[tuple[str, int], str] = {}
        for formula, bag_color in formula_color.items():
            for size_lb, price in size_prices.items():
                item_id = self.shopping.add_item_to_product(
                    product_id=product_id,
                    price=price,
                    options={
                        "formula": formula,
                        "bag_color": bag_color,
                        "size_lb": size_lb,
                        "breed": "large breed",
                    },
                    available=True,
                )
                self.pet_food_variant_item_ids[(formula, size_lb)] = item_id

        # Target variant the agent must visually identify from the orange bag photo.
        self.target_variant_item_id = self.pet_food_variant_item_ids[("chicken", 12)]

        # A distractor product so search results are not singletons.
        distractor_pid = self.shopping.add_product(name="Pawsome Harvest Puppy Dry Dog Food")
        self.shopping.add_item_to_product(
            product_id=distractor_pid,
            price=34.99,
            options={
                "formula": "chicken",
                "bag_color": "yellow",
                "size_lb": 8,
                "breed": "puppy",
            },
            available=True,
        )

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

        # Plain string IDs from Step 2 seeding — use directly so we never pass Event
        # objects into app methods inside EventRegisterer.capture_mode().
        friend_id = self.friend_id
        friend_conversation_id = self.friend_conversation_id
        photo_sandbox_path = self.pet_food_photo_sandbox_path
        target_product_id = self.pet_food_product_id
        target_variant_item_id = self.target_variant_item_id

        with EventRegisterer.capture_mode():
            # === Non-oracle ENVIRONMENT trigger ===
            # Exogenous signal: friend (Riley) sends a Messages photo of the nearly-empty
            # orange dog food bag and asks the user to reorder the same variant on their behalf.
            # Rich cue text (so the agent can ground a specific proposal without guessing):
            #   "orange bag, chicken, large breed, 12 lb" + "add it to your cart and check out".
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=friend_conversation_id,
                sender_id=friend_id,
                content=(
                    "Buddy's almost out of kibble — can you grab another bag of the same one? "
                    "It's the orange bag, chicken, large breed, 12 lb. "
                    "Just add it to your cart and check out, I'll Venmo you for it."
                ),
                attachment_path=photo_sandbox_path,
            ).delayed(5)

            # === Oracle / agent observation + grounding chain ===
            # Motivation: incoming_message_event content asks the user to "grab another bag of the
            # same one" with an attached photo; agent reads the conversation to see the full request
            # plus the image attachment reference.
            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=friend_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=3)
            )

            # Motivation: read_message_event exposes a FileMessageV2 attachment at /IMG_2411.jpg;
            # agent displays the photo via Files so it can visually identify the orange bag
            # (the catalog disambiguates variants by bag_color, so the photo is required).
            view_photo_event = (
                files.display(path=photo_sandbox_path)
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            # Motivation: the message text says "large breed" + "chicken" + "12 lb" and the photo
            # confirms an orange bag; agent searches Shopping for a large-breed dog food product
            # to ground the variant against the catalog.
            search_product_event = (
                shopping_app.search_product(product_name="large breed dog food")
                .oracle()
                .depends_on(view_photo_event, delay_seconds=2)
            )

            # Motivation: search_product_event returns "Pawsome Harvest Large Breed Dry Dog Food"
            # (target_product_id); agent opens the product to inspect its 9 color/size variants and
            # identify the orange 12 lb chicken one (bag_color=orange matches the photo).
            view_product_event = (
                shopping_app.get_product_details(product_id=target_product_id)
                .oracle()
                .depends_on(search_product_event, delay_seconds=2)
            )

            # Motivation: view_product_event reveals the variants dict; combined with the orange
            # bag observed in view_photo_event and "chicken, large breed, 12 lb" from
            # incoming_message_event, agent proposes adding the matching variant to the cart and
            # checking out — citing the message request ("add it to your cart and check out").
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Riley's message in Messages asked me to grab another bag of Buddy's "
                        "kibble (orange bag, chicken, large breed, 12 lb) and check out. "
                        "I viewed the attached photo — the orange bag matches the chicken "
                        "large-breed 12 lb variant in Shopping. "
                        "Want me to add that variant to the cart and place the order?"
                    )
                )
                .oracle()
                .depends_on(view_product_event, delay_seconds=2)
            )

            # User accepts the proposal (gates the cart write + checkout).
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please add the orange 12 lb chicken variant and check out."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # Motivation: acceptance_event approved the write; agent adds the visually-grounded
            # target variant (orange/chicken/large breed/12 lb — target_variant_item_id revealed
            # by view_product_event) to the cart.
            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=target_variant_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Motivation: acceptance_event also approved "check out"; agent checks out the cart
            # containing the orange 12 lb chicken variant that add_to_cart_event just added.
            checkout_event = (
                shopping_app.checkout()
                .oracle()
                .depends_on(add_to_cart_event, delay_seconds=2)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            incoming_message_event,
            read_message_event,
            view_photo_event,
            search_product_event,
            view_product_event,
            proposal_event,
            acceptance_event,
            add_to_cart_event,
            checkout_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        from are.simulation.types import Action, EventType

        try:
            log_entries = env.event_log.list_view()

            target_variant_item_id = self.target_variant_item_id

            # Check 1 — Proposal: agent sent a proactive proposal to the user via
            # PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # Check 2 — Task: agent completed the promised cart add + checkout for the
            # visually-grounded target variant (orange/chicken/large breed/12 lb). Both
            # required writes must appear as AGENT events for task_completed to be True.
            target_variant_added_to_cart = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and str(e.action.args.get("item_id", "")) == target_variant_item_id
                and int(e.action.args.get("quantity", 0) or 0) >= 1
                for e in log_entries
            )

            checkout_completed = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "checkout"
                for e in log_entries
            )

            task_completed = target_variant_added_to_cart and checkout_completed

            success = proposal_found and task_completed

            if success:
                return ScenarioValidationResult(success=True)

            failed_checks: list[str] = []
            if not proposal_found:
                failed_checks.append("no proactive proposal found")
            if not target_variant_added_to_cart:
                failed_checks.append(
                    f"task not completed: target variant {target_variant_item_id} "
                    "not added to cart"
                )
            if not checkout_completed:
                failed_checks.append("task not completed: checkout not completed")

            rationale = "; ".join(failed_checks)
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
