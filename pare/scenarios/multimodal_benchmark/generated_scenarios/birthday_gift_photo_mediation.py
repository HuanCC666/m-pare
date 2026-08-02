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


@register_scenario("birthday_gift_photo_mediation")
class BirthdayGiftPhotoMediation(PAREScenario):
    """Two friends are independently proposing a birthday gift for a mutual friend, Priya Mehta, and ask the user to mediate. Alex sends the user a one-to-one message suggesting they jointly chip in for a stand mixer. Sam separately sends the user a one-to-one message suggesting an espresso machine, attaches a photo Sam took of Priya's kitchen counter last week, and asks the user to use the photo to decide which appliance Priya doesn't already have, then start a new group chat with both Alex and Sam so the three of them can agree and split the cost. The kitchen photo is delivered as a Messages attachment in Sam's conversation; the assistant must open the conversation, download the attachment, and display it via Files at a sandbox path to inspect it.

The assistant must: (1) read both incoming one-to-one messages, (2) view Sam's kitchen photo and infer from visual evidence that an espresso machine is already sitting on Priya's counter (so Sam's own espresso machine idea is redundant, while Alex's stand mixer — not visible anywhere in the photo — is the fitting pick), (3) search Shopping for a stand mixer and use view_product to confirm a suitable model and its price (read-only — no cart write, no checkout), (4) look up Alex and Sam in Messaging contacts to resolve their user ids, (5) create a new group conversation with Alex and Sam, (6) proactively propose to the user: post the kitchen photo plus the stand mixer pick and price in the new group so Alex and Sam can settle on it, and (7) after user acceptance, send the photo and the stand mixer recommendation (with price) to the new group conversation.

The photo is required because Sam's text only says "here's Priya's kitchen — pick whichever she doesn't have" without describing what is on the counter; vision reveals the existing espresso machine, which inverts the naive choice (Sam's own suggestion) and resolves the conflict in favor of Alex's stand mixer. This scenario exercises multimodal kitchen-appliance identification for gift-conflict resolution, Messaging contact lookup and group conversation creation to mediate between two separately-proposing friends, Shopping read-only product grounding (search_product + view_product, no cart or checkout), and a user-gated outbound group reply carrying photo evidence plus a product recommendation.."""

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

        # Messaging app: connected to the shared sandbox FS so attachment_path sends
        # and downloads resolve through self.files.
        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        # Shopping app: rely on the default Meta-ARE catalog; the agent will use
        # search_product + view_product (read-only) to ground the stand mixer pick.
        # No cart, orders, or items are seeded here.
        self.shopping = StatefulShoppingApp(name="Shopping")

        # TODO: Populate apps with scenario specific data here.
        # Example pattern:
        # local_image_path = SCENARIO_ASSET_DIR / "<<asset_filename>>"
        # with self.files.open("<<sandbox_path>>", "wb") as f:
        #     f.write(jpeg_bytes_for_sandbox(local_image_path.read_bytes()))

        # Load Sam's kitchen-counter photo (taken last week) into the sandbox FS.
        # Step 3 will deliver it as a Messages attachment from Sam referencing this path.
        local_kitchen_photo = (
            SCENARIO_ASSET_DIR
            / "birthday_gift_photo_mediation"
            / "IMG_2411.jpg"
        )
        if not local_kitchen_photo.exists():
            raise FileNotFoundError(
                f"Kitchen photo not found: {local_kitchen_photo}. "
                f"Place IMG_2411.jpg under {local_kitchen_photo.parent}."
            )
        self.kitchen_photo_sandbox_path = "/priya_kitchen_counter.jpg"
        with self.files.open(self.kitchen_photo_sandbox_path, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_kitchen_photo.read_bytes()))

        # Contacts in Messaging: the user, two friends (Alex, Sam) who will separately
        # propose gifts, and the mutual birthday friend (Priya). These names are
        # pre-existing and let the agent resolve user ids via get_user_id / lookup_user_id.
        self.messaging.add_users(["Alex Chen", "Sam Rivera", "Priya Mehta"])
        self.alex_id = self.messaging.get_user_id("Alex Chen")
        self.sam_id = self.messaging.get_user_id("Sam Rivera")
        self.priya_id = self.messaging.get_user_id("Priya Mehta")
        if not (self.alex_id and self.sam_id and self.priya_id):
            raise RuntimeError(
                "Failed to resolve messaging user ids for Alex/Sam/Priya"
            )

        # Baseline 1:1 conversation with Alex — a short prior thread so the incoming
        # gift-suggestion trigger in Step 3 lands in an existing conversation.
        alex_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.alex_id],
            title="Alex Chen",
        )
        last_week_ts = self.start_time - 7 * 86_400
        alex_prior_msg = MessageV2(
            sender_id=self.alex_id,
            content="Hey! Are you free to chat about Priya's birthday next week?",
            timestamp=last_week_ts,
        )
        alex_conversation.messages.append(alex_prior_msg)
        alex_conversation.update_last_updated(last_week_ts)
        self.messaging.add_conversation(alex_conversation)
        self.alex_conversation_id = alex_conversation.conversation_id

        # Baseline 1:1 conversation with Sam — analogous prior thread.
        sam_conversation = ConversationV2(
            participant_ids=[self.messaging.current_user_id, self.sam_id],
            title="Sam Rivera",
        )
        sam_prior_msg = MessageV2(
            sender_id=self.sam_id,
            content="Priya's birthday is coming up — let's figure something out together.",
            timestamp=last_week_ts,
        )
        sam_conversation.messages.append(sam_prior_msg)
        sam_conversation.update_last_updated(last_week_ts)
        self.messaging.add_conversation(sam_conversation)
        self.sam_conversation_id = sam_conversation.conversation_id

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

        # IDs seeded in init_and_populate_apps() — the agent must derive these from
        # observed evidence (the 1:1 conversation reads) rather than from these constants;
        # we only use them here to script the oracle flow.
        alex_id = self.alex_id
        sam_id = self.sam_id
        alex_conversation_id = self.alex_conversation_id
        sam_conversation_id = self.sam_conversation_id
        kitchen_photo_path = self.kitchen_photo_sandbox_path

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment events: two friends independently message the
            # user 1:1 about Priya's birthday gift. Both create_and_add_message calls
            # have notification templates in pare/apps/notification_templates.py for
            # both user and agent streams. These exogenous triggers motivate every
            # subsequent oracle action.

            # Alex suggests a stand mixer and asks the user to coordinate with Sam.
            alex_msg_event = messaging_app.create_and_add_message(
                conversation_id=alex_conversation_id,
                sender_id=alex_id,
                content=(
                    "Hey! For Priya's birthday next week — I reckon a stand mixer would "
                    "be perfect for her. Want to chip in together? Could you check with "
                    "Sam and see if we can split it? Let me know what you think."
                ),
            ).delayed(3)

            # Sam separately suggests an espresso machine, attaches the kitchen photo,
            # and asks the user to use the photo to pick whichever appliance Priya
            # doesn't already have, then start a new group chat with Alex and Sam.
            sam_msg_event = messaging_app.create_and_add_message(
                conversation_id=sam_conversation_id,
                sender_id=sam_id,
                content=(
                    "Hey — so I know Alex is pushing for a stand mixer for Priya's "
                    "birthday, but I reckon an espresso machine would be more her thing. "
                    "I took a photo of Priya's kitchen counter last week (attached) — "
                    "can you look at it and pick whichever appliance she doesn't already "
                    "have? Then start a new group chat with me and Alex so the three of "
                    "us can agree and split the cost. Thanks!"
                ),
                attachment_path=kitchen_photo_path,
            ).delayed(8)

            # --- Oracle observations (READ-only, motivated by the env messages above) ---

            # Read Alex's 1:1 conversation so the agent sees Alex's stand-mixer
            # suggestion + resolves Alex's user_id from the message sender_id.
            # Motivated by alex_msg_event ("stand mixer ... chip in together ...
            # check with Sam").
            read_alex_event = (
                messaging_app.read_conversation(
                    conversation_id=alex_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(alex_msg_event, delay_seconds=3)
            )

            # Read Sam's 1:1 conversation so the agent sees Sam's espresso-machine
            # suggestion, the attached kitchen photo, and the request to start a
            # group chat, and resolves Sam's user_id from the message sender_id.
            # Motivated by sam_msg_event ("espresso machine ... photo of Priya's
            # kitchen counter ... pick whichever appliance she doesn't already have
            # ... start a new group chat with me and Alex").
            read_sam_event = (
                messaging_app.read_conversation(
                    conversation_id=sam_conversation_id, offset=0, limit=10
                )
                .oracle()
                .depends_on(sam_msg_event, delay_seconds=3)
            )

            # Inspect the attached kitchen photo via Files so the agent can infer
            # visually which large countertop appliance Priya already owns.
            # Motivated by Sam's attached photo in sam_msg_event ("I took a photo
            # of Priya's kitchen counter last week ... pick whichever appliance she
            # doesn't already have").
            view_kitchen_photo_event = (
                files.display(path=kitchen_photo_path)
                .oracle()
                .depends_on(read_sam_event, delay_seconds=2)
            )

            # Search Shopping for a stand mixer to ground a concrete pick (read-only;
            # no cart, no checkout). Motivated by Alex's "stand mixer" suggestion in
            # alex_msg_event combined with the visual evidence that no stand mixer is
            # present on Priya's counter (view_kitchen_photo_event) — making the stand
            # mixer the fitting gift.
            search_mixer_event = (
                shopping_app.search_product(
                    product_name="stand mixer", offset=0, limit=10
                )
                .oracle()
                .depends_on([read_alex_event, view_kitchen_photo_event], delay_seconds=2)
            )

            # --- Proposal: cite Alex's stand-mixer message + Sam's espresso/photo
            # message + the visual inspection (espresso machine already on counter,
            # no stand mixer) + the Shopping search. No write happens here; only
            # after user acceptance below.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Alex messaged suggesting a stand mixer for Priya's birthday, and "
                        "Sam messaged suggesting an espresso machine and attached a photo "
                        "of Priya's kitchen counter, asking me to pick whichever appliance "
                        "she doesn't already have and start a group chat with both of you "
                        "so you can agree and split the cost. I viewed the photo — there's "
                        "already an espresso machine on Priya's counter, so Sam's espresso "
                        "idea is out. No stand mixer is visible, so Alex's stand mixer is "
                        "the right pick. I searched Shopping and found a suitable stand "
                        "mixer. Want me to create a new group chat with Alex and Sam and "
                        "post the kitchen photo plus the stand mixer pick there so the "
                        "three of you can settle on it and split the cost?"
                    )
                )
                .oracle()
                .depends_on(search_mixer_event, delay_seconds=2)
            )

            # User accepts the proposal (motivated by proposal_event above).
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes — please create the group chat with Alex and Sam and share "
                        "the kitchen photo plus the stand mixer pick there."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- User-gated write actions (all depend on acceptance_event) ---

            # Create a new group conversation with Alex and Sam (user_ids resolved
            # from the 1:1 conversation reads above). Gated by acceptance_event.
            create_group_event = (
                messaging_app.create_group_conversation(
                    user_ids=[alex_id, sam_id], title="Priya Birthday Gift"
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

            # Post the kitchen photo + stand mixer recommendation to the new group
            # conversation. Motivated by Sam's "start a new group chat with me and
            # Alex so the three of us can agree and split the cost" in sam_msg_event
            # and the user's acceptance. The conversation_id is resolved at runtime
            # by the agent finding the group conversation created by
            # create_group_event; placeholder used here per established pattern.
            send_group_msg_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id="",
                    content=(
                        "Hi Alex and Sam — mediating Priya's birthday gift here. I looked "
                        "at Sam's kitchen photo: there's already an espresso machine on "
                        "Priya's counter, so an espresso machine is redundant. No stand "
                        "mixer is visible, so a stand mixer is the right pick (matches "
                        "Alex's idea). I searched Shopping and found a suitable stand "
                        "mixer. Attaching the kitchen photo for reference — let's agree on "
                        "the stand mixer and split the cost."
                    ),
                    attachment_path=kitchen_photo_path,
                )
                .oracle()
                .depends_on(create_group_event, delay_seconds=2)
            )

        # Register ALL events so they actually execute.
        self.events: list[Event] = [
            alex_msg_event,
            sam_msg_event,
            read_alex_event,
            read_sam_event,
            view_kitchen_photo_event,
            search_mixer_event,
            proposal_event,
            acceptance_event,
            create_group_event,
            send_group_msg_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import Action, EventType

            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            expected_group_participants = {self.alex_id, self.sam_id}
            expected_attachment = self.kitchen_photo_sandbox_path

            # Check 1 — Proposal: the agent proactively offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). We do not keyword-match
            # the proposal body and we do not validate acceptance.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: after acceptance, the agent (a) created a new group
            # conversation containing both Alex and Sam, and (b) sent a message to that
            # group conversation carrying the kitchen-photo attachment. Both writes must
            # pass for the task to count as completed.
            group_created = False
            group_message_sent = False
            for e in agent_entries:
                if not isinstance(e.action, Action):
                    continue
                if e.action.class_name != "StatefulMessagingApp":
                    continue
                args = e.action.args or {}
                if e.action.function_name == "create_group_conversation":
                    user_ids = args.get("user_ids")
                    if user_ids and set(user_ids) == expected_group_participants:
                        group_created = True
                elif e.action.function_name == "send_message_to_group_conversation":
                    if str(args.get("attachment_path", "")) == expected_attachment:
                        group_message_sent = True

            task_completed = group_created and group_message_sent

            success = proposal_found and task_completed
            if success:
                return ScenarioValidationResult(success=True)

            failed: list[str] = []
            if not proposal_found:
                failed.append("no proactive proposal found")
            if not group_created:
                failed.append(
                    "task not completed: group conversation with Alex and Sam not created"
                )
            elif not group_message_sent:
                failed.append(
                    "task not completed: kitchen photo not posted to the group conversation"
                )
            return ScenarioValidationResult(
                success=False, rationale="; ".join(failed)
            )

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
