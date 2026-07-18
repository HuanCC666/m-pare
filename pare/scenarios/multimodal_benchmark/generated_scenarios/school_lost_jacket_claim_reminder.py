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
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "school_lost_jacket_claim_reminder"
)
JACKET_PHOTO_FILENAME = "IMG_2411.jpg"
JACKET_PHOTO_SANDBOX_PATH = "/IMG_2411.jpg"


@register_scenario("school_lost_jacket_claim_reminder")
class SchoolLostJacketClaimReminder(PAREScenario):
    """A school's lost-and-found emails the parent (device owner) a photo of a jacket found after the after-school program and asks them to confirm whether it's their child's, reply to claim it, and set a reminder to pick it up before the front office closes for the weekend.

The email from "Lincoln Elementary Lost & Found" arrives with an attached photo of the jacket (a local image asset seeded in Files and attached to the email). The photo is the only source for the jacket's appearance — a distinctive color and print pattern — so the assistant must view the image to describe it for the parent to recognize; the email body deliberately does not name the color or pattern. The email body explicitly asks the parent to reply to claim the jacket, to pick it up from the front office by end of day Friday, July 31, 2026, and to set a reminder so the jacket is not placed in the weekend donation bin.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the jacket photo via Files to observe its color and print pattern from the image, (3) proactively propose replying to the school to claim the jacket and creating a pickup reminder due on the morning of July 31, 2026 with the front-office location recorded in the description, and (4) after user acceptance, reply to the school's email confirming the claim and create the reminder using the due date and location taken from the email body.

This scenario exercises multimodal grounding on a photo of a physical object where vision supplies appearance/identity rather than printed text, cross-app coordination across Email + Files + Reminders, the combination of `reply_to_email` (outward claim to the school) with `add_reminder` (the parent's own pickup deadline), and a due date sourced from the email body while the image supplies only visual identity — all cued explicitly by the trigger email's request to both claim and track the pickup.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the lost-and-found jacket photo (the only visual source for the jacket's
        # appearance) into the sandbox filesystem so Step 3 can attach it to the incoming
        # school email via attachment_paths=["/IMG_2411.jpg"] and the agent can inspect it
        # via files.display(...). The email body deliberately omits color/pattern, so this
        # image is the sole grounding for the proactive claim + reminder proposal.
        jacket_image_path = Path(
            os.getenv(
                "PARE_LOST_JACKET_PHOTO_LOCAL_PATH",
                str(SCENARIO_ASSET_DIR / JACKET_PHOTO_FILENAME),
            )
        )
        if not jacket_image_path.exists():
            raise FileNotFoundError(
                f"Lost-and-found jacket photo not found: {jacket_image_path}. "
                f"Place {JACKET_PHOTO_FILENAME} under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(JACKET_PHOTO_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(jacket_image_path.read_bytes()))

        # Pre-existing baseline reminder: the parent's regular after-school pickup routine.
        # This is baseline state only; the pickup-deadline reminder for the lost jacket is
        # created by the agent in Step 3/4 after the trigger email arrives.
        self.after_school_pickup_reminder_id = self.reminder.add_reminder(
            title="After-school pickup at Lincoln Elementary",
            due_datetime="2025-11-18 15:15:00",
            description=(
                "Pick up Maya from the after-school program at the Lincoln Elementary "
                "front office (Room 101)."
            ),
        )

        # Identifiers referenced by later steps (trigger email is sent in Step 3, not here).
        self.lost_found_email_id = "lincoln_lost_found_jacket_email"
        self.jacket_photo_sandbox_path = JACKET_PHOTO_SANDBOX_PATH

        # TODO: Register all apps here in self.apps
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

        with EventRegisterer.capture_mode():
            # ENV: school lost-and-found emails the parent a photo of the found jacket and
            # explicitly asks them to reply to claim it, pick it up by end of day Friday,
            # July 31, 2026, and set a reminder so it is not placed in the weekend donation
            # bin. The body deliberately does NOT name the color/pattern — the attached
            # photo (sandbox path /IMG_2411.jpg) is the only visual source for the jacket's
            # appearance. Template-backed env event (send_email_to_user_with_id).
            lost_found_email_event = email_app.send_email_to_user_with_id(
                email_id=self.lost_found_email_id,
                sender="Lincoln Elementary Lost & Found <lostfound@lincolnesd.org>",
                subject="Jacket found after after-school program — please confirm and claim",
                content=(
                    "Hi Maya's family,\n\n"
                    "We found a jacket left behind after today's after-school program and "
                    "think it may be Maya's. I've attached a photo so you can confirm — "
                    "please look it over and reply to this email to claim it if it's hers.\n\n"
                    "Pickup: front office (Room 101), Lincoln Elementary.\n"
                    "Deadline: please pick it up by end of day Friday, July 31, 2026 — "
                    "anything still in lost-and-found after that goes into the weekend "
                    "donation bin.\n\n"
                    "It would help to set a reminder so it doesn't slip past the deadline.\n\n"
                    "Thank you,\nLincoln Elementary Lost & Found"
                ),
                attachment_paths=[self.jacket_photo_sandbox_path],
            ).delayed(5)

            # ORACLE read: agent opens the incoming school email (env cue: "I've attached a
            # photo so you can confirm") to read the body and expose the image attachment.
            read_email_event = (
                email_app.get_email_by_id(
                    email_id=self.lost_found_email_id, folder_name="INBOX"
                )
                .oracle()
                .depends_on(lost_found_email_event, delay_seconds=2)
            )

            # ORACLE visual inspection: agent displays the attached jacket photo via Files
            # to observe its color and print pattern from the image (the only visual source
            # for the jacket's appearance — the email body does not name them).
            view_jacket_photo_event = (
                files.display(path=self.jacket_photo_sandbox_path)
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            # ORACLE proposal: grounded in `lost_found_email_event` ("reply to this email
            # to claim it", "pick it up by end of day Friday, July 31, 2026", "set a
            # reminder") and the visual evidence from `view_jacket_photo_event` (teal-blue
            # windbreaker with small yellow duck print). Proposal cites the env cue facts
            # (claim reply + pickup deadline + reminder) and the observed appearance so the
            # parent can recognize the jacket.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Lincoln Elementary Lost & Found emailed about a jacket found "
                        "after the after-school program. I opened the attached photo — "
                        "it's a child-sized teal-blue windbreaker with an all-over print "
                        "of small yellow ducks, which may help you recognize it.\n\n"
                        "The email asks you to: (1) reply to claim the jacket, and "
                        "(2) pick it up from the front office (Room 101) by end of day "
                        "Friday, July 31, 2026, and suggests setting a reminder so it "
                        "isn't moved to the weekend donation bin.\n\n"
                        "Want me to reply to the school to claim it and create a pickup "
                        "reminder for the morning of July 31, 2026 with the front-office "
                        "location in the description?"
                    )
                )
                .oracle()
                .depends_on([read_email_event, view_jacket_photo_event], delay_seconds=1)
            )

            # USER accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please reply to claim it and set the pickup reminder."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # ORACLE write: reply to the school's lost-and-found email confirming the claim.
            # Grounded in `lost_found_email_event` ("reply to this email to claim it");
            # user-gated via `acceptance_event`. Uses the email_id revealed by the env cue.
            reply_claim_event = (
                email_app.reply_to_email(
                    email_id=self.lost_found_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi,\n\n"
                        "Thanks for holding onto it — that's Maya's jacket (the teal-blue "
                        "one with the yellow ducks). I'll claim it and pick it up from the "
                        "front office (Room 101) before end of day Friday, July 31, 2026.\n\n"
                        "Best,\nMaya's parent"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # ORACLE write: create the pickup reminder, with due date and location taken
            # from the email body ("end of day Friday, July 31, 2026", "front office,
            # Room 101"). User-gated via `acceptance_event`.
            create_pickup_reminder_event = (
                reminder_app.add_reminder(
                    title="Pick up Maya's lost jacket from Lincoln Elementary front office",
                    due_datetime="2026-07-31 08:30:00",
                    description=(
                        "Pick up Maya's lost jacket from the Lincoln Elementary front "
                        "office (Room 101) before end of day Friday, July 31, 2026 so it "
                        "is not moved to the weekend donation bin. Reply sent to the "
                        "lost-and-found email to claim it."
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            lost_found_email_event,
            read_email_event,
            view_jacket_photo_event,
            proposal_event,
            acceptance_event,
            reply_claim_event,
            create_pickup_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import EventType

            log_entries = env.event_log.list_view()

            # Check 1 — Proposal: agent offered proactive help via the user-facing
            # PAREAgentUserInterface.send_message_to_user(...) call.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and getattr(e.action, "class_name", None) == "PAREAgentUserInterface"
                and getattr(e.action, "function_name", None) == "send_message_to_user"
                for e in log_entries
            )

            # Check 2 — Task: agent completed BOTH promised writes (folded into one
            # boolean). (a) reply_to_email on the school lost-and-found email using the
            # email_id revealed by the env cue, and (b) add_reminder for the July 31, 2026
            # morning pickup. Structural identifiers (email_id, due_datetime) must match
            # the seeded/ground-truth values; free-form body text is not asserted.
            expected_email_id = self.lost_found_email_id
            expected_due_datetime = "2026-07-31 08:30:00"

            reply_done = False
            reminder_done = False
            for e in log_entries:
                if e.event_type != EventType.AGENT:
                    continue
                action = e.action
                class_name = getattr(action, "class_name", None)
                function_name = getattr(action, "function_name", None)
                args = action.args or {}

                if (
                    class_name == "StatefulEmailApp"
                    and function_name == "reply_to_email"
                    and args.get("email_id") == expected_email_id
                ):
                    reply_done = True

                if (
                    class_name == "StatefulReminderApp"
                    and function_name == "add_reminder"
                    and str(args.get("due_datetime", "")) == expected_due_datetime
                ):
                    reminder_done = True

            task_completed = reply_done and reminder_done

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not reply_done:
                    rationale = (
                        "task not completed: reply to school lost-and-found email "
                        f"{expected_email_id} not found"
                    )
                else:
                    rationale = (
                        "task not completed: pickup reminder not created with due "
                        f"datetime {expected_due_datetime}"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
