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


@register_scenario("roof_hail_damage_repair_approval")
class RoofHailDamageRepairApproval(PAREScenario):
    """Agent approves a roofing repair estimate from a hail-damage inspection photo and saves the photo for the user's claim records.

A roofing contractor ("Summit Roofing") emails the user a photo taken during a roof inspection showing hail damage — dented and pocked shingles — and asks the user to: download the attached photo for their own claim records, reply to approve the repair estimate so materials can be ordered, and set a reminder to track the install appointment on the date stated in the email body. The attached photo (a local image asset seeded in Files and attached to the email) is the only source for the actual damage appearance; the email says "hail damage" but does not describe the shingle condition, so the assistant must view the image to confirm the damage is consistent with hail before proposing approval.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the roof photo via Files to observe the dented shingles, (3) download the attachment into a Files directory so the user keeps a copy for their claim records, (4) proactively propose replying to Summit Roofing to approve the estimate and creating an install-appointment reminder for the date stated in the email body, and (5) after user acceptance, reply to the contractor's email approving the repair and create the reminder with due_datetime set to the install date from the email body.

This scenario exercises multimodal grounding on a photo of physical damage where vision supplies condition/appearance (dented shingles) that the email text deliberately does not describe, cross-app coordination across Email + Files + Reminders, the novel use of `download_attachments` to persist an incoming damage photo for the user's own claim records, and the combination of `reply_to_email` (outward approval to the contractor) with `add_reminder` (the owner's own install-appointment tracking) — all cued explicitly by the contractor's request to save the photo, approve the estimate, and track the install date.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email + Reminders apps. Email shares the sandbox filesystem so that
        # the hail-damage photo can be attached to the incoming contractor email
        # in Step 3 and downloaded again via download_attachments.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the roof hail damage photo (local manifest-backed asset) into the
        # sandbox filesystem at /IMG_2415.jpg. This is the only source for the
        # actual damage appearance (dented/pocked shingles); the triggering
        # contractor email says "hail damage" but does not describe the shingle
        # condition, so the agent must view the image via files.display(...) to
        # confirm the damage is consistent with hail before proposing approval.
        local_roof_photo_path = (
            Path(__file__).parent.parent
            / "multimodal_benchmark"
            / "assets"
            / "image_assets"
            / "roof_hail_damage_repair_approval"
            / "IMG_2415.jpg"
        )
        if not local_roof_photo_path.exists():
            raise FileNotFoundError(
                f"Roof hail damage photo not found: {local_roof_photo_path}. "
                f"Place IMG_2415.jpg under {local_roof_photo_path.parent}."
            )
        with self.files.open("/IMG_2415.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_roof_photo_path.read_bytes()))

        # Email id reused by Step 3 when the contractor's estimate email arrives
        # as an environment event (not seeded here, since it is the runtime
        # trigger).
        self.roof_email_id = "summit_roofing_hail_estimate_email"

        # Install appointment date stated in the contractor email body. Kept on
        # the scenario as a ground-truth constant for Step 3 / Step 4 reference.
        # start_time is 2025-11-18 09:00 UTC; the install appointment is 9 days
        # later at 08:00 UTC => 2025-11-27 08:00:00.
        self.install_appointment_datetime = "2025-11-27 08:00:00"

        # Register all apps here in self.apps
        self.apps = [
            self.agent_ui,
            self.system_app,
            self.files,
            self.email,
            self.reminder,
        ]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        # Precompute plain values used inside event calls (do NOT pass Event
        # objects where simple strings are required).
        contractor_email_id = self.roof_email_id
        contractor_sender = "estimates@summitroofing.com"
        photo_sandbox_path = "/IMG_2415.jpg"
        install_appointment_datetime = self.install_appointment_datetime

        with EventRegisterer.capture_mode():
            # --- Non-oracle environment event: Summit Roofing's hail-damage
            # inspection estimate email arrives in the user's inbox with the
            # roof photo attached. This is the exogenous trigger for the whole
            # scenario. The body explicitly asks the user to (a) download the
            # attached photo for their own claim records, (b) reply to approve
            # the repair estimate so materials can be ordered, and (c) set a
            # reminder to track the install appointment on the stated date. The
            # email says "hail damage" but deliberately does NOT describe the
            # shingle condition, so the agent must view the image to confirm
            # the damage appearance is consistent with hail before proposing.
            contractor_email_event = email_app.send_email_to_user_with_id(
                email_id=contractor_email_id,
                sender=contractor_sender,
                subject="Roof inspection — hail damage estimate ready for your approval",
                content=(
                    "Hi John,\n\n"
                    "We completed the roof inspection today. The attached photo shows the hail damage "
                    "we found. Please:\n"
                    "1. Download the attached photo for your own claim records.\n"
                    "2. Reply to this email to approve the repair estimate so we can order materials.\n"
                    "3. Set a reminder to track the install appointment on "
                    f"{install_appointment_datetime}.\n\n"
                    "Once you reply with approval we'll lock in the install date and order the "
                    "shingles. Let me know if you have any questions.\n\n"
                    "Thanks,\n"
                    "Marcus\n"
                    "Summit Roofing"
                ),
                attachment_paths=[photo_sandbox_path],
            ).delayed(5)

            # --- Oracle read: agent reads the contractor's incoming email
            # (exposes the photo attachment via multimodal observation).
            # Motivated by the new-inbox notification for the contractor's
            # email above ("Roof inspection — hail damage estimate ready for
            # your approval").
            read_contractor_email_event = (
                email_app.get_email_by_id(email_id=contractor_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(contractor_email_event, delay_seconds=2)
            )

            # --- Oracle visual inspection: agent displays the attached roof
            # damage photo via Files to confirm the visible damage appearance
            # (dented/pocked shingles with impact bruises) is consistent with
            # hail before proposing approval. The image is the only source for
            # the damage appearance; the email body says "hail damage" but does
            # not describe the shingle condition.
            view_photo_event = (
                files.display(path=photo_sandbox_path)
                .oracle()
                .depends_on(read_contractor_email_event, delay_seconds=1)
            )

            # --- Oracle proposal: grounded in the contractor's email body
            # ("Download the attached photo for your own claim records",
            # "Reply to this email to approve the repair estimate so we can
            # order materials", "Set a reminder to track the install
            # appointment on {install_appointment_datetime}") and the photo
            # inspection (dented/pocked shingles consistent with hail).
            # Propose (1) downloading the attachment into Files for claim
            # records, (2) replying to Summit Roofing to approve the estimate,
            # and (3) creating an install-appointment reminder for the stated
            # date — gated on user acceptance before any write.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read the email from Summit Roofing (estimates@summitroofing.com) asking you "
                        "to download the attached roof photo for your claim records, reply to approve "
                        f"the repair estimate so they can order materials, and set a reminder to track "
                        f"the install appointment on {install_appointment_datetime}. I inspected the "
                        "attached photo via Files — the shingles show round dents, pocks, and small "
                        "impact bruises with minor granule loss, consistent with hail damage.\n\n"
                        "Would you like me to (1) download the attached photo into your Files for your "
                        "claim records, (2) reply to Summit Roofing approving the repair estimate, and "
                        f"(3) create a reminder to track the install appointment on "
                        f"{install_appointment_datetime}?"
                    )
                )
                .oracle()
                .depends_on(view_photo_event, delay_seconds=1)
            )

            # --- User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please save the photo, reply to approve the estimate, and create the install reminder."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- Oracle write (user-gated): download the attached hail-damage
            # photo into the Files Downloads directory so the user keeps a copy
            # for their own claim records, as explicitly requested in the
            # contractor's email ("Download the attached photo for your own
            # claim records"). Grounded in the contractor_email_id read above.
            download_photo_event = (
                email_app.download_attachments(
                    email_id=contractor_email_id,
                    folder_name="INBOX",
                    path_to_save="Downloads/",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- Oracle write (user-gated): reply to the contractor's email
            # approving the repair estimate so Summit Roofing can order
            # materials, as explicitly requested in the trigger email ("Reply
            # to this email to approve the repair estimate so we can order
            # materials"). Grounded in the contractor_email_id read above and
            # the visual confirmation of hail damage.
            reply_email_event = (
                email_app.reply_to_email(
                    email_id=contractor_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi Marcus,\n\n"
                        "I reviewed the inspection photo — the shingles show round dents and impact "
                        "bruising consistent with hail damage. I approve the repair estimate; please "
                        "go ahead and order materials and lock in the install appointment. I've also "
                        "saved a copy of the photo for my claim records.\n\n"
                        "Thanks,\n"
                        "John"
                    ),
                )
                .oracle()
                .depends_on(download_photo_event, delay_seconds=1)
            )

            # --- Oracle write (user-gated): create a reminder to track the
            # install appointment on the date stated in the contractor's email
            # body (install_appointment_datetime). Depends on the reply so the
            # writes are ordered, and transitively on the user's acceptance.
            add_reminder_event = (
                reminder_app.add_reminder(
                    title="Track Summit Roofing install appointment",
                    due_datetime=install_appointment_datetime,
                    description=(
                        "Track the Summit Roofing hail-damage roof repair install appointment "
                        f"(scheduled {install_appointment_datetime}). Confirm materials are ordered "
                        "and the crew is coming; reply to estimates@summitroofing.com if anything "
                        "slips."
                    ),
                )
                .oracle()
                .depends_on(reply_email_event, delay_seconds=1)
            )

        # Register ALL events so the runtime executes them.
        self.events: list[Event] = [
            contractor_email_event,
            read_contractor_email_event,
            view_photo_event,
            proposal_event,
            acceptance_event,
            download_photo_event,
            reply_email_event,
            add_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import EventType

            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            contractor_email_id = self.roof_email_id
            install_appointment_datetime = self.install_appointment_datetime

            # Check 1 — Proposal: the proactive agent offered help to the user
            # via PAREAgentUserInterface.send_message_to_user(...). We do NOT
            # keyword-match the proposal body and we do NOT validate acceptance.
            proposal_found = any(
                getattr(e, "action", None) is not None
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: agent completed ALL three promised side effects
            # after the proposal: (a) download_attachments for the contractor
            # email into Files, (b) reply_to_email on the contractor email
            # approving the estimate, and (c) add_reminder with due_datetime
            # set to the install appointment date from the email body. All
            # three writes must pass for task_completed to be True.
            download_done = any(
                getattr(e, "action", None) is not None
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "download_attachments"
                and e.action.args.get("email_id") == contractor_email_id
                for e in agent_entries
            )
            reply_done = any(
                getattr(e, "action", None) is not None
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "reply_to_email"
                and e.action.args.get("email_id") == contractor_email_id
                for e in agent_entries
            )
            reminder_done = any(
                getattr(e, "action", None) is not None
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and e.action.args.get("due_datetime") == install_appointment_datetime
                for e in agent_entries
            )
            task_completed = download_done and reply_done and reminder_done

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not download_done:
                    rationale = "task not completed: roof photo not downloaded via download_attachments"
                elif not reply_done:
                    rationale = "task not completed: approval reply not sent via reply_to_email"
                else:
                    rationale = (
                        "task not completed: install reminder not created with "
                        f"due_datetime={install_appointment_datetime}"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)
            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
