"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.email_client import Email, EmailFolderName
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
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "field_trip_slip_forward_reminder"
)
PERMISSION_SLIP_IMAGE_FILENAME = "IMG_2418.jpg"
PERMISSION_SLIP_SANDBOX_PATH = "/IMG_2418.jpg"


@register_scenario("field_trip_slip_forward_reminder")
class FieldTripSlipForwardReminder(PAREScenario):
    """A co-parent emails the user a photo of a printed school field-trip permission slip and asks them to forward the form to the child's grandparent and set a reminder to return the signed form by the deadline printed on the slip.

The co-parent's email arrives with an attached photo of the printed permission slip (a local image asset seeded in Files and attached to the email). The slip image shows the field-trip date, destination, and the "return signed form by" deadline as printed text that can only be read by viewing the image; the email body explicitly asks the user to forward this email to the grandparent at a stated email address so they're aware of the trip, and to set a reminder to return the signed form by the deadline shown on the slip.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the permission-slip photo via Files to read the return deadline from the image, (3) proactively propose forwarding the email to the grandparent's address (taken from the email body) and creating a return-deadline reminder using the date read from the slip, and (4) after user acceptance, forward the email to the grandparent and create the reminder with due_datetime set to the deadline read from the slip.

This scenario exercises multimodal grounding on a photo-like printed permission slip, cross-app coordination across Email + Files + Reminders, the novel combination of `forward_email` (sharing the form with a family member whose address is explicitly stated in the trigger) with `add_reminder` (tracking the user's own return deadline), and a visually-grounded due date — all cued explicitly by the trigger email's request to both share the form and track the return.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email + Reminders apps for cross-app forward + reminder flow.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Visual asset: photo of the printed field-trip permission slip.
        # Loaded from the resolved local asset manifest and written into the sandbox FS
        # so Step 3 can attach it to the trigger email and the agent can display() it.
        local_slip_path = SCENARIO_ASSET_DIR / PERMISSION_SLIP_IMAGE_FILENAME
        if not local_slip_path.exists():
            raise FileNotFoundError(
                f"Permission slip image not found: {local_slip_path}. "
                f"Place {PERMISSION_SLIP_IMAGE_FILENAME} under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(PERMISSION_SLIP_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_slip_path.read_bytes()))

        # Facts grounded in the permission-slip image (printed text the agent must read).
        # These are stored as scenario attributes for reuse in Step 3 (email body + reminder
        # due datetime) and Step 4 validation. They are NOT observable by the agent from
        # comments alone — Step 3 delivers them via the trigger email body and the slip image.
        self.permission_slip_sandbox_path = PERMISSION_SLIP_SANDBOX_PATH
        self.field_trip_destination = "Riverbend Nature Center"
        self.field_trip_date_text = "Friday, July 24, 2026"
        self.field_trip_return_deadline_text = "Monday, July 20, 2026"
        # Reminder due datetime derived from the slip's "Return signed form by" deadline.
        self.reminder_due_datetime = "2026-07-20 09:00:00"

        # Addresses used by the trigger email body (Step 3) and the forward_email action.
        self.co_parent_email = "jordan.harper@example.com"
        self.grandparent_email = "helen.harper@example.com"
        # Stable email id so Step 3 can forward the exact trigger email.
        self.permission_slip_email_id = "field_trip_permission_slip_email"

        # Pre-existing baseline data: a pre-start-time email from the co-parent so the
        # sender is a known correspondent. The actual trigger email with the slip photo
        # is delivered as an environment event in Step 3, not seeded here.
        baseline_timestamp = datetime(2025, 11, 17, 16, 30, 0, tzinfo=UTC).timestamp()
        baseline_email = Email(
            email_id="field_trip_intro_email",
            sender=self.co_parent_email,
            recipients=["john@pare.com"],
            subject="Field trip coming up for Maya",
            content=(
                "Hi,\n\n"
                "Maya's class is going on a field trip next summer. I'll send over the "
                "permission slip as soon as the teacher hands it out so we can get it "
                "signed and back in time.\n\nThanks,\nJordan"
            ),
            timestamp=baseline_timestamp,
            is_read=True,
        )
        self.email.folders[EmailFolderName.INBOX].add_email(baseline_email)

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
            # ENV event: co-parent sends the trigger email with the permission-slip photo
            # attached. Body explicitly asks the user to forward to the grandparent's
            # stated address and set a reminder for the deadline PRINTED ON THE SLIP
            # (so the deadline date itself must be read from the image, not this body).
            permission_slip_email_event = email_app.send_email_to_user_with_id(
                email_id=self.permission_slip_email_id,
                sender=self.co_parent_email,
                subject="Maya's field trip permission slip - please forward to Mom + reminder",
                content=(
                    "Hi,\n\n"
                    "Maya's teacher handed out the field trip permission slip today; "
                    "photo of the printed form is attached. The slip shows the trip date, "
                    "destination, and a 'Return signed form by' deadline line printed on it.\n\n"
                    "Could you do two things?\n"
                    "1. Forward this email to Mom at helen.harper@example.com so she's aware "
                    "of the trip.\n"
                    "2. Set a reminder to send the signed form back by the deadline printed "
                    "on the slip.\n\n"
                    "Thanks,\nJordan"
                ),
                attachment_paths=[self.permission_slip_sandbox_path],
            ).delayed(5)

            # Oracle READ: agent reads the incoming trigger email so the body (forward-to
            # address + reminder request) and the image attachment become agent-visible.
            # Grounded by permission_slip_email_event ("Forward this email to Mom at
            # helen.harper@example.com" / "Set a reminder ... by the deadline printed on the slip").
            read_email_event = (
                email_app.get_email_by_id(
                    email_id=self.permission_slip_email_id, folder_name="INBOX"
                )
                .oracle()
                .depends_on(permission_slip_email_event, delay_seconds=2)
            )

            # Oracle READ (visual inspection): agent displays the attached slip photo via
            # Files to read the "Return signed form by: Monday, July 20, 2026" line printed
            # on the slip. The deadline date is NOT in the email body, so it must come from
            # this image inspection step.
            view_slip_event = (
                files.display(path=self.permission_slip_sandbox_path)
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            # Oracle PROPOSAL: agent proposes forwarding the email to the grandparent's
            # address (taken from the trigger email body) and creating a return-deadline
            # reminder using the date read from the slip image (Monday, July 20, 2026).
            # Grounded by permission_slip_email_event ("Forward this email to Mom at
            # helen.harper@example.com" / "Set a reminder ... by the deadline printed on the
            # slip") and view_slip_event (deadline line on the slip).
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "I read Jordan's email and inspected the attached permission slip "
                        "photo. The slip's 'Return signed form by' line reads Monday, July 20, "
                        "2026. Jordan asked to (1) forward this email to "
                        "helen.harper@example.com so Mom is aware of the trip, and (2) set a "
                        "reminder to return the signed form by that deadline. Shall I forward "
                        "the email to helen.harper@example.com and create a reminder due "
                        "2026-07-20 09:00:00 to return the signed Maya field trip form?"
                    )
                )
                .oracle()
                .depends_on(view_slip_event, delay_seconds=1)
            )

            # User accepts the proposal so the write actions below are user-gated.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please forward to Mom and set that reminder."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # Oracle WRITE: forward the trigger email to the grandparent's address stated
            # in the trigger email body. Grounded by permission_slip_email_event
            # ("helen.harper@example.com") and gated by acceptance_event.
            forward_email_event = (
                email_app.forward_email(
                    email_id=self.permission_slip_email_id,
                    recipients=[self.grandparent_email],
                    folder_name="INBOX",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # Oracle WRITE: create the return-deadline reminder with due_datetime set to
            # the deadline read from the slip image (2026-07-20 09:00:00). Grounded by
            # view_slip_event and the trigger email's "Set a reminder ... by the deadline
            # printed on the slip"; gated by acceptance_event.
            add_reminder_event = (
                reminder_app.add_reminder(
                    title="Return signed Maya field trip permission slip",
                    due_datetime=self.reminder_due_datetime,
                    description=(
                        "Sign and return Maya's field trip permission slip to "
                        "Riverbend Nature Center (trip Friday, July 24, 2026). "
                        "Deadline printed on the slip: Monday, July 20, 2026."
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        # Register ALL events in self.events
        self.events: list[Event] = [
            permission_slip_email_event,
            read_email_event,
            view_slip_event,
            proposal_event,
            acceptance_event,
            forward_email_event,
            add_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import Action, EventType

            log_entries = env.event_log.list_view()

            # Only AGENT events are eligible for validation checks.
            agent_events = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Check 1 — Proposal: agent offered a proactive proposal to the user
            # via PAREAgentUserInterface.send_message_to_user(...). Content is not
            # matched; presence of the structural call is sufficient.
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_events
            )

            # Check 2 — Task: agent completed BOTH promised writes after acceptance:
            #   (a) forwarded the trigger email (permission_slip_email_id) to the
            #       grandparent's address stated in the trigger email body
            #       (helen.harper@example.com), and
            #   (b) created a reminder whose due_datetime matches the deadline read
            #       from the slip image (2026-07-20 09:00:00).
            # Both writes must succeed for task_completed to be True (folded into a
            # single check). Structural identifiers only; no body/message matching.
            forward_found = False
            reminder_found = False
            for e in agent_events:
                if not isinstance(e.action, Action):
                    continue
                args = e.action.args or {}
                if (
                    e.action.class_name == "StatefulEmailApp"
                    and e.action.function_name == "forward_email"
                    and args.get("email_id") == self.permission_slip_email_id
                    and self.grandparent_email in (args.get("recipients") or [])
                ):
                    forward_found = True
                elif (
                    e.action.class_name == "StatefulReminderApp"
                    and e.action.function_name == "add_reminder"
                    and str(args.get("due_datetime", "")).strip()
                    == self.reminder_due_datetime
                ):
                    reminder_found = True

            task_completed = forward_found and reminder_found

            success = proposal_found and task_completed

            if success:
                return ScenarioValidationResult(success=True)

            missing: list[str] = []
            if not proposal_found:
                missing.append("no proactive proposal found")
            if not task_completed:
                if not forward_found and not reminder_found:
                    missing.append(
                        "task not completed: email not forwarded and reminder not created"
                    )
                elif not forward_found:
                    missing.append(
                        f"task not completed: email not forwarded to {self.grandparent_email}"
                    )
                elif not reminder_found:
                    missing.append(
                        f"task not completed: reminder not created with due_datetime {self.reminder_due_datetime}"
                    )
            rationale = "; ".join(missing)
            return ScenarioValidationResult(success=False, rationale=rationale)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
