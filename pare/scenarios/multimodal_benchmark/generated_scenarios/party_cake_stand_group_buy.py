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
)
from pare.apps.shopping import StatefulShoppingApp
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
    / "party_cake_stand_group_buy"
)


@register_scenario("party_cake_stand_group_buy")
class PartyCakeStandGroupBuy(PAREScenario):
    """A three-person group chat is planning a surprise birthday party. One friend, who is at the bakery picking up the cake, posts a photo of the cake they ordered and asks the user (who is near a party-supplies shop) to grab a matching cake stand and use any applicable discount code before checking out, then report the total back to the group. The cake photo is delivered as a message attachment in the existing group conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it.

The assistant must: (1) open the group conversation in Messages and read the incoming request, (2) view the cake photo and infer from visual evidence that it is a tall multi-tier round cake (so a tall tiered stand is required, not a flat plate), (3) search Shopping for a cake stand and identify a tall tiered variant, (4) call the Shopping discount-code APIs to find a code that applies to that item and verify it, (5) proactively propose to the user: add the stand to cart, check out with the discount, and reply to the group with the total, and (6) after the user accepts, add the stand to cart, check out with the verified discount code, and send a reply to the same group conversation with the order total. The photo is required because the message only says "a cake" without describing tier count or shape; vision determines which stand variant fits, and the discount-code lookup + checkout depend on first grounding the correct product. This scenario exercises multimodal product-shape inference, group-conversation reading and replying, discount-code discovery and verification, and a user-gated discounted checkout with an outbound group reply.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Messaging + Shopping apps. Messaging shares the sandbox filesystem so that
        # Step 3 can attach /party_cake.jpg to an incoming group message and the agent
        # can later download and display it via Files.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.shopping = StatefulShoppingApp(name="Shopping")

        # --- Visual asset: cake photo loaded into the sandbox (exists before events). ---
        cake_photo_path = SCENARIO_ASSET_DIR / "IMG_3092.jpg"
        if not cake_photo_path.exists():
            raise FileNotFoundError(
                f"Cake photo not found: {cake_photo_path}. "
                f"Place IMG_3092.jpg under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open("/party_cake.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(cake_photo_path.read_bytes()))

        # --- Shopping catalog: cake stands + party-supply browse options. ---
        # Target: tall tiered cake stand that fits a tall multi-tier round cake.
        tiered_pid = self.shopping.add_product(name="Tall Tiered Cake Stand")
        self.tiered_stand_product_id = tiered_pid
        self.tiered_stand_item_id = self.shopping.add_item_to_product(
            product_id=tiered_pid,
            price=34.99,
            options={
                "material": "metal",
                "tiers": 3,
                "height_cm": 28,
                "base_diameter_cm": 18,
                "color": "gold",
            },
            available=True,
        )

        # Decoy: flat plate (wrong for a tall multi-tier cake).
        flat_pid = self.shopping.add_product(name="Flat Ceramic Cake Plate")
        self.flat_plate_item_id = self.shopping.add_item_to_product(
            product_id=flat_pid,
            price=19.99,
            options={
                "material": "ceramic",
                "diameter_cm": 30,
                "color": "white",
            },
            available=True,
        )

        # Additional party-supply browse options so product search is non-trivial.
        cupcake_pid = self.shopping.add_product(name="Cupcake Tower Display Stand")
        self.cupcake_tower_item_id = self.shopping.add_item_to_product(
            product_id=cupcake_pid,
            price=24.99,
            options={
                "material": "acrylic",
                "tiers": 4,
                "height_cm": 22,
            },
            available=True,
        )

        lights_pid = self.shopping.add_product(name="Warm White String Lights 10m")
        self.string_lights_item_id = self.shopping.add_item_to_product(
            product_id=lights_pid,
            price=12.99,
            options={"length_m": 10, "color": "warm white"},
            available=True,
        )

        # Discount codes: PARTY15 applies to the tall tiered stand (the correct item);
        # PLATE10 is a decoy that only applies to the flat plate, so the agent must
        # discover + verify the applicable code via the Shopping discount-code APIs.
        self.discount_code = "PARTY15"
        self.shopping.add_discount_code(self.tiered_stand_item_id, {self.discount_code: 15.0})
        self.shopping.add_discount_code(self.flat_plate_item_id, {"PLATE10": 10.0})

        # --- Messaging: pre-existing 3-person group chat with baseline party-planning history. ---
        maya_name = "Maya Patel"
        maya_phone = "+1-555-0117"
        jordan_name = "Jordan Lee"
        jordan_phone = "+1-555-0118"
        self.messaging.add_contacts([(maya_name, maya_phone), (jordan_name, jordan_phone)])
        self.maya_id = self.messaging.get_user_id(maya_name)
        self.jordan_id = self.messaging.get_user_id(jordan_name)
        if self.maya_id is None or self.jordan_id is None:
            raise RuntimeError("Failed to resolve messaging user ids for party group contacts")

        group_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.maya_id, self.jordan_id],
            title="Sam's Surprise Birthday",
        )
        # Baseline messages from the day before start_time (2025-11-17 evening UTC).
        prior_evening = self.start_time - 86_400  # 2025-11-17 09:00 UTC
        msg_maya_ts = prior_evening + 10 * 3600 + 30 * 60  # 19:30
        msg_jordan_ts = prior_evening + 10 * 3600 + 35 * 60  # 19:35
        msg_user_ts = prior_evening + 10 * 3600 + 40 * 60  # 19:40
        group_conversation.messages.append(
            MessageV2(
                sender_id=self.maya_id,
                content=(
                    "Can't wait for Saturday's surprise party for Sam! I'm picking up the "
                    "cake from Sweet Crumb Bakery tomorrow morning."
                ),
                timestamp=msg_maya_ts,
            )
        )
        group_conversation.messages.append(
            MessageV2(
                sender_id=self.jordan_id,
                content=(
                    "I've got balloons and decorations covered. Can you grab a cake stand "
                    "and any other party-supply bits?"
                ),
                timestamp=msg_jordan_ts,
            )
        )
        group_conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content=(
                    "Sure - I'll swing by Party Plus after lunch and find a stand that fits "
                    "the cake."
                ),
                timestamp=msg_user_ts,
            )
        )
        group_conversation.update_last_updated(msg_user_ts)
        self.messaging.add_conversation(group_conversation)
        self.group_conversation_id = group_conversation.conversation_id

        # Register all apps so build_events_flow() can look them up via get_typed_app.
        self.apps = [self.agent_ui, self.system_app, self.files, self.messaging, self.shopping]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        # IDs seeded in init_and_populate_apps() — the agent must derive these from
        # observed evidence (the group conversation read + a Shopping search) rather
        # than from these constants; we only use them here to script the oracle flow.
        group_conversation_id = self.group_conversation_id
        maya_id = self.maya_id
        tiered_stand_item_id = self.tiered_stand_item_id
        discount_code = self.discount_code

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment event: Maya (at the bakery) posts the cake
            # photo + request into the existing 3-person group chat. This is the
            # exogenous trigger that motivates every subsequent oracle action.
            # The message also names a concrete discount code (PARTY15) she saw on a
            # Party Plus flyer so the agent has a specific code to verify via the
            # Shopping discount-code APIs (get_all_discount_codes is not event-
            # registered, so discovery must come from the env cue instead).
            # create_and_add_message has notification templates for both user and
            # agent streams in pare/apps/notification_templates.py.
            incoming_cake_photo_event = messaging_app.create_and_add_message(
                conversation_id=group_conversation_id,
                sender_id=maya_id,
                content=(
                    "At Sweet Crumb now - here's the cake! It's a tall multi-tier round one. "
                    "Can you grab a matching cake stand from Party Plus and use any applicable "
                    "discount code before checking out, then tell us the total back here? "
                    "I saw a PARTY15 promo code on a Party Plus flyer - try that one if it works. "
                    "Thanks!"
                ),
                attachment_path="/party_cake.jpg",
            ).delayed(3)

            # Oracle: read the group conversation so the agent sees Maya's request
            # text + the "PARTY15" code + the attached cake photo message
            # (motivated by the incoming_cake_photo_event env notification).
            read_group_event = (
                messaging_app.read_conversation(
                    conversation_id=group_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_cake_photo_event, delay_seconds=3)
            )

            # Oracle: display the attached cake photo via Files to visually inspect
            # tier count / shape before any product or proposal decision
            # (motivated by the cake-photo message read above).
            view_cake_photo_event = (
                files.display(path="/party_cake.jpg")
                .oracle()
                .depends_on(read_group_event, delay_seconds=2)
            )

            # Oracle: search Shopping for "cake stand" to find a stand matching the
            # tall multi-tier round cake observed in the photo (motivated by Maya's
            # "grab a matching cake stand" request + the visual evidence above).
            search_stand_event = (
                shopping_app.search_product(product_name="cake stand", offset=0, limit=10)
                .oracle()
                .depends_on(view_cake_photo_event, delay_seconds=2)
            )

            # Oracle: verify PARTY15 applies to the chosen stand item before
            # proposing it (motivated by Maya's "I saw a PARTY15 promo code ...
            # try that one if it works" in the incoming_cake_photo_event).
            verify_discount_event = (
                shopping_app.get_discount_code_info(discount_code=discount_code)
                .oracle()
                .depends_on(search_stand_event, delay_seconds=2)
            )

            # Proposal: cite Maya's cake-photo message ("grab a matching cake stand
            # ... use any applicable discount code ... tell us the total", "PARTY15
            # promo code ... try that one") + the visual inspection (tall three-tier
            # round cake) + the verified PARTY15 code. No write happens here; only
            # after user acceptance below.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya just posted the cake photo in 'Sam's Surprise Birthday' and asked me "
                        "to grab a matching stand, apply a valid discount, and report the total "
                        "back to the group. I viewed the photo - it's a tall three-tier round cake, "
                        "so the Tall Tiered Cake Stand (gold, $34.99) is the right fit, not a flat "
                        "plate. I verified the PARTY15 code she mentioned applies to it (15% off, "
                        "total about $29.74). Want me to add it to cart, check out with PARTY15, "
                        "and reply to the group with the total?"
                    )
                )
                .oracle()
                .depends_on(verify_discount_event, delay_seconds=2)
            )

            # User accepts the proposal (motivated by the proposal_event above).
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes - please add the tall tiered stand, check out with PARTY15, "
                        "and reply to the group with the total."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- User-gated write actions (all depend on acceptance_event) ---

            # Oracle write: add the tall tiered stand to cart (item_id is the one
            # the agent would have discovered via search_product above; gated by
            # the user's acceptance_event).
            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=tiered_stand_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Oracle write: check out with the verified PARTY15 discount code
            # (gated by acceptance_event via the add_to_cart dependency chain).
            checkout_event = (
                shopping_app.checkout(discount_code=discount_code)
                .oracle()
                .depends_on(add_to_cart_event, delay_seconds=2)
            )

            # Oracle write: reply to the same group conversation with the order
            # total (motivated by Maya's "tell us the total back here" in the
            # incoming_cake_photo_event; gated by acceptance_event via the
            # checkout dependency chain).
            reply_group_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=group_conversation_id,
                    content=(
                        "Got the cake stand! Picked up the Tall Tiered Gold Cake Stand ($34.99) "
                        "and applied PARTY15 (15% off) - total came to $29.74. Should be perfect "
                        "for the tall three-tier cake."
                    ),
                )
                .oracle()
                .depends_on(checkout_event, delay_seconds=2)
            )

        # Register ALL events so they actually execute.
        self.events: list[Event] = [
            incoming_cake_photo_event,
            read_group_event,
            view_cake_photo_event,
            search_stand_event,
            verify_discount_event,
            proposal_event,
            acceptance_event,
            add_to_cart_event,
            checkout_event,
            reply_group_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            # Identifiers seeded in init_and_populate_apps() that the task check
            # must match against (the agent's writes must target the correct item,
            # the correct verified discount code, and the correct group conversation).
            tiered_stand_item_id = self.tiered_stand_item_id
            discount_code = self.discount_code
            group_conversation_id = self.group_conversation_id

            # --- Check 1: Proposal ---
            # Prove the proactive agent offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). Only AGENT events
            # count; acceptance is intentionally not validated here.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2: Task ---
            # The promised user-visible side effects, all folded into a single
            # task check (every required write must pass):
            #   (a) add the tall tiered stand to cart (correct item_id),
            #   (b) check out with the verified PARTY15 discount code, and
            #   (c) reply to the SAME group conversation with the order total.
            add_to_cart_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and e.action.args.get("item_id") == tiered_stand_item_id
                for e in log_entries
            )

            checkout_with_discount_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "checkout"
                and e.action.args.get("discount_code") == discount_code
                for e in log_entries
            )

            group_reply_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == group_conversation_id
                for e in log_entries
            )

            task_completed = (
                add_to_cart_found
                and checkout_with_discount_found
                and group_reply_found
            )

            success = proposal_found and task_completed

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append("no proactive proposal found")
                if not add_to_cart_found:
                    failed_checks.append(
                        "task not completed: tall tiered cake stand not added to cart"
                    )
                if not checkout_with_discount_found:
                    failed_checks.append(
                        f"task not completed: checkout not completed with discount code {discount_code}"
                    )
                if not group_reply_found:
                    failed_checks.append(
                        "task not completed: no reply sent to the group conversation"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
