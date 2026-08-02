"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, Event, EventRegisterer, EventType

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
import os
from datetime import timedelta

from are.simulation.apps.email_client import Email, EmailFolderName

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulEmailApp,
)
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

# TODO: replace these with paths from the resolved VisualAssetSpec / asset manifest.
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"


@register_scenario("pipe_damage_claim_reply_reminder")
class PipeDamageClaimReplyReminder(PAREScenario):
    """A home-insurance adjuster emails the user a photo of water damage under their kitchen sink and asks them to reply confirming the damage is consistent with a pipe-failure claim, to include their policy number from their original "Homeowners Policy Welcome" email when replying, and to set a reminder to submit a contractor estimate before the 30-day deadline stated in the email body. The attached photo (a local image asset seeded in Files and attached to the email) is the only source for the actual damage appearance — a corroded copper pipe joint and water staining on the cabinet base — so the assistant must view the image to confirm the damage is consistent with a pipe failure rather than, e.g., an appliance flood; the email body deliberately does not describe the photo's contents. A prior "Homeowners Policy Welcome" email from the insurer is already in the user's inbox from when the policy was opened, and the adjuster's email names that subject so the policy number can be retrieved by searching the inbox.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the damage photo via Files to confirm the visible cause is a corroded pipe joint, (3) search emails for the "Homeowners Policy Welcome" message (the subject is named in the adjuster's email), open it, and read the policy number from its body, (4) proactively propose replying to the adjuster confirming the pipe-failure damage and including the retrieved policy number, and creating a reminder for the contractor-estimate deadline stated in the email body, and (5) after user acceptance, reply to the adjuster's email with the confirmation and policy number, and create the reminder with `due_datetime` set to the deadline from the email body.

This scenario exercises multimodal grounding on a photo of physical damage where vision supplies cause/appearance rather than printed text, cross-app coordination across Email + Files + Reminders, the use of `search_emails` to retrieve a policy number from a prior inbox message whose subject is explicitly named in the trigger (rather than relying on a magic query string), and the combination of `reply_to_email` (outward claim confirmation with a retrieved reference) with `add_reminder` (the user's own estimate-submission deadline) — all cued explicitly by the adjuster's request to confirm, reference the policy, and track the deadline.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Cross-app coordination: Email (insurer + adjuster messages, image attachment)
        # and Reminders (contractor-estimate deadline).
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Stable IDs / scenario constants shared with build_events_flow() and validate().
        self.adjuster_email_id = "adjuster_pipe_damage_email"
        self.policy_welcome_email_id = "homeowners_policy_welcome"
        self.policy_number = "HG-2024-090874"
        # 30-day contractor-estimate deadline stated in the adjuster's email body.
        scenario_start = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.contractor_estimate_deadline_dt = scenario_start + timedelta(days=30)
        self.contractor_estimate_deadline = self.contractor_estimate_deadline_dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Load the local pipe-damage photo into the sandbox Files so the adjuster's
        # trigger email (sent in build_events_flow) can attach it and the agent can
        # display it to confirm the cause of damage.
        local_pipe_photo_path = Path(
            os.getenv(
                "PARE_PIPE_DAMAGE_PHOTO_LOCAL_PATH",
                str(
                    Path(__file__).resolve().parent.parent
                    / "multimodal_benchmark"
                    / "assets"
                    / "image_assets"
                    / "pipe_damage_claim_reply_reminder"
                    / "IMG_2412.jpg"
                ),
            )
        )
        if not local_pipe_photo_path.exists():
            raise FileNotFoundError(
                f"Pipe damage photo not found: {local_pipe_photo_path}. "
                "Place IMG_2412.jpg under "
                "pare/scenarios/multimodal_benchmark/assets/image_assets/pipe_damage_claim_reply_reminder/."
            )
        with self.files.open("/IMG_2412.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_pipe_photo_path.read_bytes()))

        # Baseline inbox: the original "Homeowners Policy Welcome" email from the
        # insurer, delivered when the policy was opened (well before start_time).
        # The adjuster's trigger email (Step 3) names this subject so the agent can
        # use search_emails(...) to retrieve the policy number from its body.
        policy_welcome_ts = datetime(2025, 9, 1, 14, 30, 0, tzinfo=UTC).timestamp()
        policy_welcome_email = Email(
            email_id=self.policy_welcome_email_id,
            sender="claims@homeguard-insurance.com",
            recipients=[self.email.user_email],
            subject="Homeowners Policy Welcome",
            content=(
                "Welcome to Homeguard Insurance!\n\n"
                f"Your Homeowners Policy is now active. Your policy number is {self.policy_number}.\n"
                "Please keep this number handy for any future claims or correspondence.\n\n"
                "Your policy documents and coverage summary are attached to this message.\n\n"
                "Thank you for insuring your home with us,\n"
                "Homeguard Insurance Claims Team"
            ),
            timestamp=policy_welcome_ts,
            is_read=True,
        )
        self.email.folders[EmailFolderName.INBOX].add_email(policy_welcome_email)

        self.apps = [self.agent_ui, self.system_app, self.files, self.email, self.reminder]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        # Precompute plain values used inside event calls (do NOT pass Event objects
        # where simple strings are required).
        adjuster_email_id = self.adjuster_email_id
        policy_welcome_email_id = self.policy_welcome_email_id
        adjuster_sender = "claims@homeguard-insurance.com"
        adjuster_deadline = self.contractor_estimate_deadline
        photo_sandbox_path = "/IMG_2412.jpg"

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment event: the adjuster's claim-confirmation
            # request email arrives in the user's inbox with the damage photo
            # attached. This is the exogenous trigger for the whole scenario; it
            # explicitly names the "Homeowners Policy Welcome" subject (so the
            # agent can search for it) and states the 30-day contractor-estimate
            # deadline date.
            adjuster_email_event = email_app.send_email_to_user_with_id(
                email_id=adjuster_email_id,
                sender=adjuster_sender,
                subject="Claim HG-2024-090874 — please confirm pipe-failure damage",
                content=(
                    "Hi,\n\n"
                    "Thanks for opening the water-damage claim. I've attached a photo of the area "
                    "under your kitchen sink. Please reply to confirm whether the visible damage is "
                    "consistent with a pipe-failure claim (and not, e.g., an appliance flood), and "
                    "include your policy number when replying — you can find it in your original "
                    "\"Homeowners Policy Welcome\" email from us (search your inbox by that subject).\n\n"
                    "Also, please remember to submit a contractor estimate before the 30-day deadline: "
                    f"{adjuster_deadline}.\n\n"
                    "Thanks,\n"
                    "Homeguard Insurance Claims Team"
                ),
                attachment_paths=[photo_sandbox_path],
            ).delayed(5)

            # --- Oracle read: agent reads the adjuster's incoming email (exposes
            # the photo attachment via multimodal observation). Motivated by the
            # new-inbox notification for the adjuster's email above.
            read_adjuster_email_event = (
                email_app.get_email_by_id(email_id=adjuster_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(adjuster_email_event, delay_seconds=2)
            )

            # --- Oracle visual inspection: agent displays the attached damage
            # photo via Files to confirm the visible cause (corroded copper pipe
            # joint + water staining) before proposing. The image is the only
            # source for the damage appearance; the email body deliberately does
            # not describe the photo's contents.
            view_photo_event = (
                files.display(path=photo_sandbox_path)
                .oracle()
                .depends_on(read_adjuster_email_event, delay_seconds=1)
            )

            # --- Oracle observation: the adjuster's email explicitly names the
            # "Homeowners Policy Welcome" subject; agent searches the inbox for
            # that prior message to retrieve the policy number required for the
            # reply.
            search_policy_email_event = (
                email_app.search_emails(query="Homeowners Policy Welcome", folder_name="INBOX")
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            # --- Oracle read: agent opens the matched "Homeowners Policy
            # Welcome" email (id revealed by the search above) to read the
            # policy number from its body.
            read_policy_email_event = (
                email_app.get_email_by_id(email_id=policy_welcome_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(search_policy_email_event, delay_seconds=1)
            )

            # --- Oracle proposal: grounded in the adjuster's email
            # ("confirm pipe-failure damage", "include your policy number",
            # "submit a contractor estimate before the 30-day deadline:
            # {adjuster_deadline}") and the photo inspection (corroded pipe
            # joint), and citing the policy number retrieved from the welcome
            # email. Propose replying to the adjuster with confirmation + policy
            # number, and creating a reminder for the deadline — gated on user
            # acceptance before any write.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read the adjuster's email from claims@homeguard-insurance.com asking me to "
                        "confirm the damage is consistent with a pipe-failure claim, include your policy "
                        "number from the \"Homeowners Policy Welcome\" email, and track the contractor-"
                        f"estimate deadline ({adjuster_deadline}). I inspected the attached photo via "
                        "Files — the visible cause is a corroded copper pipe joint with mineral buildup "
                        "and water staining on the cabinet base below, consistent with a pipe failure "
                        "(no appliance or standing water on the floor). I also searched the inbox and "
                        f"found your policy number ({self.policy_number}) in the welcome email.\n\n"
                        "Would you like me to (1) reply to the adjuster confirming the pipe-failure "
                        f"damage and including policy number {self.policy_number}, and (2) create a "
                        f"reminder to submit the contractor estimate by {adjuster_deadline}?"
                    )
                )
                .oracle()
                .depends_on(read_policy_email_event, delay_seconds=1)
            )

            # --- User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(content="Yes, please send the reply and create the reminder.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- Oracle write (user-gated): reply to the adjuster's email
            # confirming pipe-failure damage and including the policy number
            # retrieved earlier. Grounded in the adjuster's email_id (read
            # above) and the policy number revealed by read_policy_email_event.
            reply_email_event = (
                email_app.reply_to_email(
                    email_id=adjuster_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi,\n\n"
                        "I reviewed the attached photo. The visible damage is consistent with a "
                        "pipe-failure claim — a corroded copper pipe joint with mineral buildup and "
                        "water staining on the cabinet base below, with no sign of an appliance flood.\n\n"
                        f"My policy number is {self.policy_number}.\n\n"
                        "I'll submit a contractor estimate before the 30-day deadline. Thanks for your help.\n\n"
                        "Best,\n"
                        "John"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- Oracle write (user-gated): create a reminder for the
            # contractor-estimate deadline stated in the adjuster's email body
            # (adjuster_deadline). Depends on the reply so the two writes are
            # ordered, and transitively on the user's acceptance.
            add_reminder_event = (
                reminder_app.add_reminder(
                    title="Submit contractor estimate — Homeguard pipe-damage claim",
                    due_datetime=adjuster_deadline,
                    description=(
                        "Submit the contractor estimate for the kitchen-sink pipe-failure claim "
                        f"(policy {self.policy_number}) before the 30-day deadline stated in the "
                        "adjuster's claim-confirmation email."
                    ),
                )
                .oracle()
                .depends_on(reply_email_event, delay_seconds=1)
            )

        # Register ALL events so the runtime executes them.
        self.events: list[Event] = [
            adjuster_email_event,
            read_adjuster_email_event,
            view_photo_event,
            search_policy_email_event,
            read_policy_email_event,
            proposal_event,
            acceptance_event,
            reply_email_event,
            add_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            adjuster_email_id = self.adjuster_email_id
            expected_deadline = self.contractor_estimate_deadline

            def _event_args(e) -> dict[str, Any]:
                action = getattr(e, "action", None)
                if not isinstance(action, Action):
                    return {}
                args = action.resolved_args if action.resolved_args else action.args
                return {k: v for k, v in args.items() if k != "self"}

            # --- Check 1: Proposal ---
            # Prove the proactive agent offered help via a message to the user.
            # We assert only on the structural identity of the call
            # (class/function names), not on free-form message text.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # --- Check 2: Task ---
            # The promised user-visible side effects are:
            #   (a) reply_to_email on the adjuster's email (matching the seeded
            #       adjuster_email_id), and
            #   (b) add_reminder with due_datetime equal to the 30-day
            #       contractor-estimate deadline stated in the adjuster's email.
            # Both writes must be present as AGENT events for task_completed.
            reply_to_adjuster_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "reply_to_email"
                and _event_args(e).get("email_id") == adjuster_email_id
                for e in log_entries
            )

            reminder_with_deadline_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and str(_event_args(e).get("due_datetime", "")).strip()
                == str(expected_deadline).strip()
                for e in log_entries
            )

            task_completed = reply_to_adjuster_found and reminder_with_deadline_found

            success = proposal_found and task_completed

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("no proactive proposal found")
                if not reply_to_adjuster_found:
                    failed.append(
                        "task not completed: reply_to_email on adjuster email "
                        f"{adjuster_email_id} not found"
                    )
                if not reminder_with_deadline_found:
                    failed.append(
                        "task not completed: add_reminder with due_datetime "
                        f"{expected_deadline} not found"
                    )
                return ScenarioValidationResult(
                    success=False,
                    rationale="; ".join(failed),
                )

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
