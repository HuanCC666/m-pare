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

# Imports for the apps and helpers used in this scenario.
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


@register_scenario("air_fryer_clearance_duplicate_decline")
class AirFryerClearanceDuplicateDecline(PAREScenario):
    """A friend, Maya, messages the user a store-shelf photo of an air fryer on clearance and offers to pick one up; the agent checks the user's Shopping order history, confirms the user already owns the same variant, and proposes a polite decline reply.

Maya's message in the existing one-to-one Messages conversation reads: "Target has the CrispAir Air Fryer 6-quart on clearance for $79 (was $129) — want me to grab one for you? They had red and black; I snapped the red one on the shelf." The photo (a real shot of a red basket-style 6-quart air fryer on a store shelf) is delivered as a Messages attachment; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it. The assistant must: (1) read Maya's incoming message, (2) view the photo and infer from visual evidence that the highlighted air fryer is the red 6-quart variant (so the agent knows which variant to match against order history, not the black one Maya also mentioned), (3) list the user's Shopping orders via list_orders and call view_order on the delivered order to read its line items, (4) confirm that a delivered order already contains a CrispAir Air Fryer 6-quart in red, (5) proactively propose to the user: reply to Maya declining the offer because the user already owns that exact variant, and (6) after the user accepts, send a polite decline reply to Maya in the same conversation. The photo is required because Maya's text names both red and black variants but only the photo shows which one she is highlighting; vision pins the variant so the agent can safely match it against the user's past order line item and avoid a duplicate purchase.

This scenario exercises multimodal product-variant identification from a store-shelf photo, read-only Shopping order-history lookup via list_orders and view_order (no cart write, no checkout), and a user-gated outbound decline reply in a one-to-one conversation.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps and seed baseline state.

        Baseline (pre-`start_time`) state only:
        - Messages: an *existing* one-to-one conversation with Maya (prior history),
          so the Step 3 trigger message lands in a real thread the agent can reply in.
        - Shopping: a CrispAir Air Fryer 6-quart product with red and black variants
          plus a delivered order containing the red variant, so the agent can confirm
          the user already owns that exact variant.
        - Files: the store-shelf photo written into the sandbox at /air_fryer_shelf.jpg
          for later attachment to Maya's incoming message (delivered in Step 3).
        """
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging: pre-existing one-to-one conversation with Maya ---
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        maya_name = "Maya Reyes"
        maya_phone = "+1-555-0143"
        self.messaging.add_contacts([(maya_name, maya_phone)])
        self.maya_id = self.messaging.get_user_id(maya_name)
        if self.maya_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {maya_name}")

        maya_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.maya_id],
            title=maya_name,
        )
        # Seed a short prior exchange so this is an existing thread, not a fresh one.
        prior_ts = self.start_time - 3 * 86_400
        maya_conversation.messages.append(
            MessageV2(
                sender_id=self.maya_id,
                content="Hey! Still into kitchen gadgets? Saw a few deals lately.",
                timestamp=prior_ts,
            )
        )
        maya_conversation.messages.append(
            MessageV2(
                sender_id=self.messaging.current_user_id,
                content="Yeah, always curious — what'd you find?",
                timestamp=prior_ts + 600,
            )
        )
        maya_conversation.update_last_updated(prior_ts + 600)
        self.messaging.add_conversation(maya_conversation)
        self.maya_conversation_id = maya_conversation.conversation_id

        # --- Shopping: catalog + delivered order with the red 6-quart CrispAir ---
        self.shopping = StatefulShoppingApp(name="Shopping")

        crispair_pid = self.shopping.add_product(name="CrispAir Air Fryer 6-quart")
        self.crispair_product_id = crispair_pid

        # Red variant is the one Maya highlights on the shelf and the one the user
        # already owns (delivered order below). Black variant is a deliberate
        # distractor so the agent must use the photo to pin the variant.
        self.crispair_red_item_id = self.shopping.add_item_to_product(
            product_id=crispair_pid,
            price=129.00,
            options={
                "color": "red",
                "size": "6-quart",
                "capacity_qt": 6,
                "basket_style": "basket",
            },
            available=True,
        )
        self.crispair_black_item_id = self.shopping.add_item_to_product(
            product_id=crispair_pid,
            price=129.00,
            options={
                "color": "black",
                "size": "6-quart",
                "capacity_qt": 6,
                "basket_style": "basket",
            },
            available=True,
        )

        # Decoy product so the catalog isn't a single-item store.
        toaster_pid = self.shopping.add_product(name="ToastMate Toaster 2-slice")
        self.shopping.add_item_to_product(
            product_id=toaster_pid,
            price=34.99,
            options={"color": "stainless steel", "slots": 2},
            available=True,
        )

        # Delivered order from ~3 weeks ago containing the red 6-quart CrispAir.
        # This is the baseline fact the agent must discover via list_orders + view_order
        # to justify declining Maya's offer.
        delivered_order_ts = self.start_time - 21 * 86_400
        self.delivered_air_fryer_order_id = "order-crispair-red-delivered"
        self.shopping.add_order(
            order_id=self.delivered_air_fryer_order_id,
            order_status="delivered",
            order_date=delivered_order_ts,
            order_total=129.00,
            item_id=self.crispair_red_item_id,
            quantity=1,
        )

        # --- Visual asset: air fryer store-shelf photo into Files sandbox ---
        # Maya's incoming message (with this photo attached) is delivered in Step 3,
        # not here. Step 2 only stages the bytes at a stable sandbox path.
        local_photo_path = Path(
            os.getenv(
                "PARE_AIR_FRYER_PHOTO_LOCAL_PATH",
                "/Users/jasonz/Projects/m-pare/pare/scenarios/multimodal_benchmark/assets/image_assets/air_fryer_clearance_duplicate_decline/air_fryer_shelf.jpg",
            )
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Air fryer store-shelf photo not found: {local_photo_path}. "
                "Place air_fryer_shelf.jpg under the resolved asset directory."
            )
        with self.files.open("/air_fryer_shelf.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))
        self.air_fryer_photo_sandbox_path = "/air_fryer_shelf.jpg"

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

        # Precompute plain string IDs/handles outside capture_mode so they are not
        # accidentally passed as Event objects into tool calls.
        maya_conversation_id = self.maya_conversation_id
        maya_id = self.maya_id
        air_fryer_photo_sandbox_path = self.air_fryer_photo_sandbox_path
        delivered_air_fryer_order_id = self.delivered_air_fryer_order_id

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Exogenous trigger: Maya sends a new message in the existing one-to-one
            # conversation with a store-shelf photo of the clearance air fryer and
            # offers to pick one up. Has a notification template entry for both
            # user and agent streams (StatefulMessagingApp.create_and_add_message).
            incoming_message_event = messaging_app.create_and_add_message(
                conversation_id=maya_conversation_id,
                sender_id=maya_id,
                content=(
                    "Target has the CrispAir Air Fryer 6-quart on clearance for $79 (was $129) "
                    "— want me to grab one for you? They had red and black; I snapped the red one on the shelf."
                ),
                attachment_path=air_fryer_photo_sandbox_path,
            ).delayed(3)

            # --- ORACLE EVENTS ---
            # Agent reads the new incoming message from Maya in the existing thread,
            # motivated by the create_and_add_message env event above ("New message
            # from ... in conversation ...").
            read_conversation_event = (
                messaging_app.read_conversation(
                    conversation_id=maya_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(incoming_message_event, delay_seconds=2)
            )

            # Agent displays the photo attachment via Files to inspect it visually,
            # motivated by the conversation read above revealing an image attachment
            # and Maya's text naming both red and black variants (only the photo
            # disambiguates which one she is offering).
            view_photo_event = (
                files.display(path=air_fryer_photo_sandbox_path)
                .oracle()
                .depends_on(read_conversation_event, delay_seconds=1)
            )

            # Agent lists the user's Shopping orders to check whether the user
            # already owns the highlighted variant, motivated by the photo
            # inspection confirming Maya is offering the red 6-quart CrispAir.
            list_orders_event = (
                shopping_app.list_orders()
                .oracle()
                .depends_on(view_photo_event, delay_seconds=2)
            )

            # Agent inspects the delivered order's line items to confirm the
            # red 6-quart CrispAir is already owned, motivated by list_orders
            # returning the delivered order's order_id.
            view_order_event = (
                shopping_app.get_order_details(order_id=delivered_air_fryer_order_id)
                .oracle()
                .depends_on(list_orders_event, delay_seconds=1)
            )

            # Agent proactively proposes declining Maya's offer, motivated by the
            # photo (red 6-quart CrispAir on clearance for $79) and the order
            # details confirming a delivered CrispAir Air Fryer 6-quart in red
            # already exists in the user's Shopping history.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya messaged offering the red CrispAir Air Fryer 6-quart from Target "
                        "clearance ($79). I checked the photo and your Shopping orders — you already "
                        "have that exact red 6-quart CrispAir from a delivered order. "
                        "Want me to reply to Maya declining so you don't end up with a duplicate?"
                    )
                )
                .oracle()
                .depends_on(view_order_event, delay_seconds=1)
            )

            # User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please tell Maya no thanks — I already have that one."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # User-gated write: agent replies to Maya in the same conversation
            # with a polite decline, after the user accepted the proposal.
            reply_message_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=maya_conversation_id,
                    content=(
                        "Thanks for the heads up and for grabbing the photo! I actually already have "
                        "that exact red CrispAir 6-quart from a previous order, so I'll pass this time. "
                        "Appreciate it though!"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events: list[Event] = [
            incoming_message_event,
            read_conversation_event,
            view_photo_event,
            list_orders_event,
            view_order_event,
            proposal_event,
            acceptance_event,
            reply_message_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Check 1 — Proposal: the proactive agent offered help to the user
            # via PAREAgentUserInterface.send_message_to_user(...).
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: after acceptance, the agent sent the polite decline
            # reply to Maya in the same one-to-one conversation, identified
            # structurally by the seeded conversation_id. No free-form body match.
            expected_conversation_id = self.maya_conversation_id
            task_completed = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and (e.action.args or {}).get("conversation_id")
                == expected_conversation_id
                for e in agent_entries
            )

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            if not proposal_found and not task_completed:
                rationale = "no proactive proposal found and task not completed: decline reply not sent to Maya's conversation"
            elif not proposal_found:
                rationale = "no proactive proposal found"
            else:
                rationale = "task not completed: decline reply not sent to Maya's conversation"
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
