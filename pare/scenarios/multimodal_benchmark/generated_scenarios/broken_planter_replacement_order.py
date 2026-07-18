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


@register_scenario("broken_planter_replacement_order")
class BrokenPlanterReplacementOrder(PAREScenario):
    """A roommate sends a Messages conversation reporting that a package from the user's recent shopping order arrived with one item broken, and asks the user to arrange a replacement.

The incoming message includes the order number (#ORD-4821) and a photo attachment showing the damaged contents; the photo is reachable by opening the conversation and downloading the attachment, then displayed via Files at a sandbox path. The assistant must: (1) read the incoming message, (2) view the broken-item photo and infer from visual evidence that a white ceramic planter is shattered, (3) look up order #ORD-4821 in Shopping via list_orders / view_order to confirm which line item was the planter, (4) search Shopping for a matching replacement planter, (5) proactively propose adding the replacement to cart and replying to the roommate, and (6) after user acceptance, add the replacement planter to cart and send a confirmation message back to the roommate.

The photo is required because the message only says "one item broken" without naming which one; vision identifies the shattered white ceramic planter, and the replacement search depends on matching that item against the order's line items. This scenario exercises multimodal damage identification, cross-app order lookup through Shopping, and a user-gated cart write plus an outbound reply in a shared conversation.."""

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

        # Messaging carries the roommate thread (with the broken-planter photo attached
        # to the incoming message in Step 3); Shopping holds the prior order and the
        # replacement planter catalog. Both apps share the sandbox Files so attachment
        # bytes are resolvable via internal_fs.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.shopping = StatefulShoppingApp(name="Shopping")

        # --- Visual asset: broken planter photo loaded into sandbox Files at
        #     /downloads/IMG_2411.jpg. Step 3 attaches this path to the roommate's
        #     incoming message via create_and_add_message(attachment_path=...), and
        #     the agent can later download it or read it via Files.display(...).
        broken_planter_local_path = Path(
            os.getenv(
                "PARE_BROKEN_PLANTER_PHOTO_PATH",
                str(
                    Path(__file__).parent.parent
                    / "multimodal_benchmark"
                    / "assets"
                    / "image_assets"
                    / "broken_planter_replacement_order"
                    / "IMG_2411.jpg"
                ),
            )
        )
        if not broken_planter_local_path.exists():
            raise FileNotFoundError(
                f"Broken planter photo not found: {broken_planter_local_path}. "
                f"Place IMG_2411.jpg under the broken_planter_replacement_order asset directory."
            )
        self.broken_planter_sandbox_path = "/downloads/IMG_2411.jpg"
        self.files.mkdir("/downloads", create_parents=True)
        with self.files.open(self.broken_planter_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(broken_planter_local_path.read_bytes()))

        # --- Shopping catalog: the order's line items plus a searchable replacement.
        #     The planter is the broken item; the other two items are non-planter so
        #     visual identification is required to pick the right line item.
        planter_product_id = self.shopping.add_product(name="White Ceramic Planter")
        self.planter_item_id = self.shopping.add_item_to_product(
            product_id=planter_product_id,
            price=28.00,
            options={"color": "white", "size": "6 inch"},
            available=True,
        )
        self.planter_product_id = planter_product_id

        terracotta_product_id = self.shopping.add_product(name="Terracotta Plant Pot")
        terracotta_item_id = self.shopping.add_item_to_product(
            product_id=terracotta_product_id,
            price=15.00,
            options={"color": "terracotta", "size": "8 inch"},
            available=True,
        )
        trowel_product_id = self.shopping.add_product(name="Garden Hand Trowel")
        trowel_item_id = self.shopping.add_item_to_product(
            product_id=trowel_product_id,
            price=12.50,
            options={"color": "steel"},
            available=True,
        )

        # --- Prior order #ORD-4821 (delivered) containing the planter + 2 other items.
        #     The agent will list_orders / get_order_details to discover these line items
        #     and match the shattered planter against the White Ceramic Planter variant.
        self.order_id = "ORD-4821"
        order_date_ts = self.start_time - 5 * 86_400
        self.shopping.add_order_multiple_items(
            order_id=self.order_id,
            order_status="delivered",
            order_date=order_date_ts,
            order_total=55.50,
            items={
                self.planter_item_id: 1,
                terracotta_item_id: 1,
                trowel_item_id: 1,
            },
        )

        # --- Messaging: roommate conversation with pre-existing baseline history.
        #     The triggering "one item is broken" message + photo arrives in Step 3
        #     via create_and_add_message; here we only seed the contact and prior chat.
        roommate_name = "Riley Park"
        roommate_phone = "+1-555-0148"
        self.messaging.add_contacts([(roommate_name, roommate_phone)])
        self.roommate_id = self.messaging.get_user_id(roommate_name)
        if self.roommate_id is None:
            raise RuntimeError(f"Failed to resolve messaging user id for {roommate_name}")

        conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.roommate_id],
            title=roommate_name,
        )
        prior_ts = self.start_time - 2 * 86_400
        conversation.messages.append(
            MessageV2(
                sender_id=self.roommate_id,
                content="Hey, the package from your shopping order just got dropped at the door!",
                timestamp=prior_ts,
            )
        )
        conversation.update_last_updated(prior_ts)
        self.messaging.add_conversation(conversation)
        self.roommate_conversation_id = conversation.conversation_id

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
            # --- ENV: roommate Riley Park posts the broken-item report (with photo
            #     attachment) into the existing Messages conversation. This is the
            #     exogenous trigger that motivates every subsequent oracle action.
            broken_item_message_event = messaging_app.create_and_add_message(
                conversation_id=self.roommate_conversation_id,
                sender_id=self.roommate_id,
                content=(
                    "Hey, your shopping order #ORD-4821 just got delivered and one item "
                    "arrived broken. I took a photo of the box — can you order a replacement "
                    "for the broken piece and let me know once it's in your cart? Thanks!"
                ),
                attachment_path=self.broken_planter_sandbox_path,
            ).delayed(5)

            # --- ORACLE: env notification names conversation "Riley Park" and reports
            #     "one item arrived broken" + "#ORD-4821"; read the thread to see the
            #     full message and the attached photo.
            read_conversation_event = (
                messaging_app.read_conversation(
                    conversation_id=self.roommate_conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(broken_item_message_event, delay_seconds=2)
            )

            # --- ORACLE: the message carries an image attachment at
            #     /downloads/IMG_2411.jpg; display it via Files so the agent can
            #     visually identify which item is broken before any proposal.
            view_photo_event = (
                files.display(path=self.broken_planter_sandbox_path)
                .oracle()
                .depends_on(read_conversation_event, delay_seconds=1)
            )

            # --- ORACLE: the message cites order "#ORD-4821"; look it up in Shopping
            #     to enumerate its line items so the broken item can be matched
            #     against the photo evidence.
            order_detail_event = (
                shopping_app.get_order_details(order_id=self.order_id)
                .oracle()
                .depends_on(view_photo_event, delay_seconds=2)
            )

            # --- ORACLE: the photo shows a shattered white ceramic planter and the
            #     order line items include "White Ceramic Planter"; search the catalog
            #     for a matching replacement variant to obtain its item_id.
            search_planter_event = (
                shopping_app.search_product(product_name="White Ceramic Planter")
                .oracle()
                .depends_on(order_detail_event, delay_seconds=1)
            )

            # --- ORACLE proposal: cite the roommate's broken-item message ("order
            #     #ORD-4821 ... one item arrived broken ... order a replacement"),
            #     the visual evidence (shattered white ceramic planter), and the
            #     matching order line item; ask permission before any write.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Riley messaged that order #ORD-4821 arrived with one item broken. "
                        "I read the thread and viewed the attached photo — it shows a shattered "
                        "white ceramic planter in the box. The order's line items include a "
                        "\"White Ceramic Planter\", which matches the broken piece, and I found "
                        "the same variant available in Shopping. Want me to add the replacement "
                        "White Ceramic Planter to your cart and reply to Riley to confirm?"
                    )
                )
                .oracle()
                .depends_on(search_planter_event, delay_seconds=1)
            )

            # --- USER: accepts the proposal (gates the cart write + outbound reply).
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please add the replacement planter to my cart and reply to Riley."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- ORACLE (user-gated write): add the matching White Ceramic Planter
            #     variant to the cart after acceptance.
            add_to_cart_event = (
                shopping_app.add_to_cart(item_id=self.planter_item_id, quantity=1)
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- ORACLE (user-gated write): reply in the roommate conversation to
            #     confirm the replacement has been added to the cart.
            reply_to_roommate_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=self.roommate_conversation_id,
                    content=(
                        "Hey Riley — I saw the broken planter in the photo. "
                        "I've added a replacement White Ceramic Planter to my cart "
                        "and will reorder it now. Thanks for flagging it!"
                    ),
                )
                .oracle()
                .depends_on(add_to_cart_event, delay_seconds=1)
            )

        # Register ALL events in order.
        self.events: list[Event] = [
            broken_item_message_event,
            read_conversation_event,
            view_photo_event,
            order_detail_event,
            search_planter_event,
            proposal_event,
            acceptance_event,
            add_to_cart_event,
            reply_to_roommate_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        # Local imports keep this check isolated from the Apps & Data / Events Flow sections.
        from are.simulation.types import Action, EventType

        try:
            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # --- Check 1: Proposal ---
            # The proactive agent must have offered help to the user via the
            # PAREAgentUserInterface.send_message_to_user(...) tool. We do not
            # keyword-match the proposal body and we do not validate acceptance.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # --- Check 2: Task ---
            # The promised user-visible side effects are:
            #   (a) the matching White Ceramic Planter variant is added to the cart
            #       (StatefulShoppingApp.add_to_cart with item_id == self.planter_item_id,
            #        quantity >= 1); and
            #   (b) a confirmation reply is sent in the roommate's Messages conversation
            #       (StatefulMessagingApp.send_message_to_group_conversation with
            #        conversation_id == self.roommate_conversation_id).
            # Both writes must succeed for the task to count as completed.
            added_to_cart = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulShoppingApp"
                and e.action.function_name == "add_to_cart"
                and str((e.action.args or {}).get("item_id", "")) == str(self.planter_item_id)
                and int((e.action.args or {}).get("quantity", 0) or 0) >= 1
                for e in agent_entries
            )

            replied_to_roommate = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name
                in ("send_message_to_group_conversation", "send_message")
                and str((e.action.args or {}).get("conversation_id", ""))
                == str(self.roommate_conversation_id)
                for e in agent_entries
            )

            task_completed = added_to_cart and replied_to_roommate

            success = proposal_found and task_completed

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("no proactive proposal found")
                if not added_to_cart:
                    failed.append(
                        f"task not completed: replacement planter "
                        f"(item_id={self.planter_item_id}) not added to cart"
                    )
                if not replied_to_roommate:
                    failed.append(
                        "task not completed: no confirmation reply sent to the roommate thread"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
