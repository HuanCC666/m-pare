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

# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
DEFAULT_LOCAL_BOOKSHELF_PHOTO_PATH = (
    Path(__file__).resolve().parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "moving_sale_bookshelf_neighbor_add"
    / "bookshelf.jpg"
)
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"


@register_scenario("moving_sale_bookshelf_neighbor_add")
class MovingSaleBookshelfNeighborAdd(PAREScenario):
    """A friend, Maya, posts in an existing three-person Messages group chat ("Weekend Hike Crew") a photo of a bookshelf her neighbor is selling and asks the user to coordinate the pickup directly with the seller. Her message reads: "My neighbor Raj is selling this bookshelf for $60 — solid walnut, 5 shelves, about 5 ft tall. Looks just like the one you said you wanted for your living room. If you want it, add Raj Patel to this chat so he can arrange pickup with you directly, and let me know so I can tell him." The bookshelf photo is delivered as a Messages attachment in the existing group conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it.

The assistant must: (1) open the group conversation in Messages and read Maya's incoming request, (2) view the bookshelf photo and infer from visual evidence that it is a tall walnut-finish 5-shelf bookshelf (so a comparable can be found in Shopping, not just any bookshelf), (3) search Shopping for a walnut bookshelf and use view_product on a matching tall 5-shelf model to read its new price and validate that $60 is a good deal (read-only — no cart write, no checkout), (4) look up "Raj Patel" in Messaging contacts via lookup_user_id / get_user_id to resolve his user id, (5) proactively propose to the user: add Raj Patel to the existing group conversation and reply to the group confirming interest in the bookshelf along with the price comparison, and (6) after the user accepts, add Raj to the group conversation and send the confirmation reply to the same group conversation. The photo is required because Maya's text says "bookshelf" and lists attributes but the Shopping comparison needs a visually grounded match (walnut finish, tall 5-shelf form factor) to find the right comparable; vision also confirms the item matches what the user wanted before the agent endorses the deal.

This scenario exercises multimodal furniture identification from a real photo, read-only Shopping product grounding for price validation (search_product + view_product, no cart or checkout), Messaging contact lookup and the rarely-used add_participant_to_conversation side effect to bring the seller into an existing group chat, and a user-gated outbound group reply carrying a price-comparison summary.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # --- Messaging: pre-existing three-person group chat "Weekend Hike Crew" ---
        # The user, Maya, and Chris are already in this group. Raj Patel is seeded as a
        # contact so the agent can resolve his user id later, but he is NOT a participant
        # yet; Maya's runtime message (Step 3) asks the agent to add him.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.messaging.add_users(["Maya Chen", "Raj Patel", "Chris Lee"])
        maya_id = self.messaging.get_user_id("Maya Chen")
        chris_id = self.messaging.get_user_id("Chris Lee")
        raj_id = self.messaging.get_user_id("Raj Patel")
        if maya_id is None or chris_id is None or raj_id is None:
            raise RuntimeError("Failed to resolve seeded messaging user ids")
        self.maya_user_id = maya_id
        self.chris_user_id = chris_id
        self.raj_user_id = raj_id

        group_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, maya_id, chris_id],
            title="Weekend Hike Crew",
        )
        # Baseline prior chat history (before start_time) so the group feels lived-in.
        prior_ts = self.start_time - 6 * 86_400  # six days before start_time
        group_conversation.messages.append(
            MessageV2(
                sender_id=chris_id,
                content="Trailhead parking is $5 — bring cash for Saturday's hike.",
                timestamp=prior_ts,
            )
        )
        group_conversation.messages.append(
            MessageV2(
                sender_id=maya_id,
                content="Sounds good. I'll bring extra snacks. See you all at 8am!",
                timestamp=prior_ts + 600,
            )
        )
        group_conversation.update_last_updated(prior_ts + 600)
        self.messaging.add_conversation(group_conversation)
        self.bookshelf_group_conversation_id = group_conversation.conversation_id

        # --- Shopping: baseline catalog with a walnut tall 5-shelf bookshelf comparable ---
        self.shopping = StatefulShoppingApp(name="Shopping")

        # Comparable: a new tall walnut 5-shelf bookshelf priced well above $60 so the
        # agent can validate that Maya's neighbor's $60 asking price is a good deal.
        walnut_bookshelf_pid = self.shopping.add_product(name="Walnut 5-Shelf Tall Bookshelf")
        self.walnut_bookshelf_product_id = walnut_bookshelf_pid
        self.walnut_bookshelf_item_id = self.shopping.add_item_to_product(
            product_id=walnut_bookshelf_pid,
            price=189.99,
            options={
                "color": "walnut",
                "finish": "dark-brown wood grain",
                "shelf_count": 5,
                "height": "5 ft",
                "material": "solid walnut",
                "form_factor": "tall rectangular freestanding bookshelf",
            },
            available=True,
        )

        # Distractor products so the agent must pick the right comparable by attributes.
        oak_bookshelf_pid = self.shopping.add_product(name="Oak 3-Shelf Bookshelf")
        self.oak_bookshelf_item_id = self.shopping.add_item_to_product(
            product_id=oak_bookshelf_pid,
            price=129.99,
            options={
                "color": "oak",
                "shelf_count": 3,
                "height": "3 ft",
                "material": "oak veneer",
            },
            available=True,
        )
        console_pid = self.shopping.add_product(name="Pine Console Table")
        self.console_item_id = self.shopping.add_item_to_product(
            product_id=console_pid,
            price=149.99,
            options={
                "color": "pine",
                "type": "console table",
                "length": "4 ft",
            },
            available=True,
        )

        # --- Visual asset: load bookshelf.jpg into the sandbox Files system ---
        local_photo_path = Path(
            os.getenv(
                "PARE_BOOKSHELF_PHOTO_LOCAL_PATH",
                str(DEFAULT_LOCAL_BOOKSHELF_PHOTO_PATH),
            )
        )
        if not local_photo_path.exists():
            raise FileNotFoundError(
                f"Bookshelf photo not found: {local_photo_path}. "
                f"Place bookshelf.jpg under {DEFAULT_LOCAL_BOOKSHELF_PHOTO_PATH.parent}."
            )
        with self.files.open("/bookshelf.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_photo_path.read_bytes()))
        self.bookshelf_photo_sandbox_path = "/bookshelf.jpg"

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
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        shopping_app = self.get_typed_app(StatefulShoppingApp, "Shopping")

        # Plain values (computed outside capture_mode so we never pass Event objects into tool calls).
        group_conversation_id = self.bookshelf_group_conversation_id
        maya_user_id = self.maya_user_id
        raj_user_id = self.raj_user_id
        walnut_bookshelf_product_id = self.walnut_bookshelf_product_id
        bookshelf_photo_path = self.bookshelf_photo_sandbox_path

        maya_message = (
            "My neighbor Raj is selling this bookshelf for $60 — solid walnut, 5 shelves, "
            "about 5 ft tall. Looks just like the one you said you wanted for your living room. "
            "If you want it, add Raj Patel to this chat so he can arrange pickup with you "
            "directly, and let me know so I can tell him."
        )

        with EventRegisterer.capture_mode():
            # --- ENVIRONMENT EVENT: Maya posts the bookshelf photo + request in the
            # existing "Weekend Hike Crew" group chat. Has a notification template entry
            # for StatefulMessagingApp.create_and_add_message in both user/agent streams.
            maya_bookshelf_message_event = messaging_app.create_and_add_message(
                conversation_id=group_conversation_id,
                sender_id=maya_user_id,
                content=maya_message,
                attachment_path=bookshelf_photo_path,
            ).delayed(5)

            # --- ORACLE: Agent reads the newly-active "Weekend Hike Crew" group chat to
            # consume Maya's incoming request (motivated by the create_and_add_message
            # notification citing "add Raj Patel to this chat").
            read_group_event = (
                messaging_app.read_conversation(
                    conversation_id=group_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(maya_bookshelf_message_event, delay_seconds=2)
            )

            # --- ORACLE: Agent inspects the bookshelf photo bytes via Files.display so the
            # walnut / tall 5-shelf form factor is visually grounded before any Shopping
            # lookup or proposal (motivated by the photo attachment in Maya's message).
            view_bookshelf_photo_event = (
                files.display(path=bookshelf_photo_path)
                .oracle()
                .depends_on(read_group_event, delay_seconds=1)
            )

            # --- ORACLE: Agent searches Shopping for a walnut bookshelf comparable, motivated
            # by the visual evidence (walnut, tall, 5-shelf) plus Maya's text "solid walnut,
            # 5 shelves, about 5 ft tall".
            search_walnut_bookshelf_event = (
                shopping_app.search_product(product_name="walnut bookshelf")
                .oracle()
                .depends_on(view_bookshelf_photo_event, delay_seconds=1)
            )

            # --- ORACLE: Agent reads the matching new product's price ($189.99) via
            # get_product_details to validate $60 is a good deal (motivated by the search
            # result returning the Walnut 5-Shelf Tall Bookshelf).
            view_walnut_bookshelf_event = (
                shopping_app.get_product_details(product_id=walnut_bookshelf_product_id)
                .oracle()
                .depends_on(search_walnut_bookshelf_event, delay_seconds=1)
            )

            # --- ORACLE: Agent looks up "Raj Patel" in Messaging contacts to resolve the
            # user id needed to add him to the group (motivated by Maya's explicit request
            # "add Raj Patel to this chat").
            lookup_raj_event = (
                messaging_app.lookup_user_id(user_name="Raj Patel")
                .oracle()
                .depends_on(view_walnut_bookshelf_event, delay_seconds=1)
            )

            # --- ORACLE PROPOSAL: Agent proposes adding Raj to the group + replying with the
            # price comparison. Grounded in maya_bookshelf_message_event ("selling this
            # bookshelf for $60", "add Raj Patel to this chat"), the viewed photo
            # (walnut/tall 5-shelf), and the Shopping comparable's $189.99 new price.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Maya just posted in Weekend Hike Crew: her neighbor Raj is selling "
                        "a bookshelf for $60 (\"solid walnut, 5 shelves, about 5 ft tall\") "
                        "and asked me to add Raj Patel to the chat so he can arrange pickup "
                        "with you. I opened the photo — it's a tall walnut 5-shelf bookshelf, "
                        "matching what you wanted for the living room. A new Walnut 5-Shelf "
                        "Tall Bookshelf in Shopping is $189.99, so $60 is a good deal. "
                        "Want me to add Raj Patel to this group chat and reply confirming "
                        "interest + the price comparison?"
                    )
                )
                .oracle()
                .depends_on(lookup_raj_event, delay_seconds=1)
            )

            # --- USER: User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please add Raj Patel to the chat and reply with the price comparison."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # --- ORACLE WRITE: Agent adds Raj Patel to the existing group conversation
            # (user-gated by acceptance; uses raj_user_id resolved from lookup_raj_event).
            add_raj_event = (
                messaging_app.add_participant_to_conversation(
                    conversation_id=group_conversation_id,
                    user_id=raj_user_id,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- ORACLE WRITE: Agent replies in the same group conversation confirming
            # interest and the price comparison (user-gated by acceptance; depends on
            # Raj being added so he sees the reply).
            reply_group_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=group_conversation_id,
                    content=(
                        "Thanks Maya! I'm interested in the bookshelf at $60. For reference, "
                        "a comparable new Walnut 5-Shelf Tall Bookshelf retails around $189.99, "
                        "so this is a great deal. Raj — happy to coordinate pickup from here."
                    ),
                )
                .oracle()
                .depends_on(add_raj_event, delay_seconds=1)
            )

        self.events: list[Event] = [
            maya_bookshelf_message_event,
            read_group_event,
            view_bookshelf_photo_event,
            search_walnut_bookshelf_event,
            view_walnut_bookshelf_event,
            lookup_raj_event,
            proposal_event,
            acceptance_event,
            add_raj_event,
            reply_group_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()
            group_conversation_id = self.bookshelf_group_conversation_id
            raj_user_id = self.raj_user_id

            # --- Check 1: Proposal — the proactive agent offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). Content is not asserted.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2: Task — after the proposal, the agent (a) added Raj Patel to the
            # existing "Weekend Hike Crew" group conversation and (b) sent the confirmation
            # reply to that same group conversation. Both writes must be present and target
            # the correct conversation (and, for the add, the correct user id).
            raj_added_to_group = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "add_participant_to_conversation"
                and e.action.args.get("conversation_id") == group_conversation_id
                and e.action.args.get("user_id") == raj_user_id
                for e in log_entries
            )

            group_reply_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and e.action.args.get("conversation_id") == group_conversation_id
                for e in log_entries
            )

            task_completed = raj_added_to_group and group_reply_sent

            success = proposal_found and task_completed

            if not success:
                failed_checks: list[str] = []
                if not proposal_found:
                    failed_checks.append("no proactive proposal found")
                if not raj_added_to_group:
                    failed_checks.append(
                        "task not completed: Raj Patel was not added to the Weekend Hike Crew group conversation"
                    )
                if not group_reply_sent:
                    failed_checks.append(
                        "task not completed: no confirmation reply sent to the Weekend Hike Crew group conversation"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed_checks),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
