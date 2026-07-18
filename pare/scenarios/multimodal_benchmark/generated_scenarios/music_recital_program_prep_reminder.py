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
    Path(__file__).resolve().parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "music_recital_program_prep_reminder"
)
RECITAL_PROGRAM_PHOTO_FILENAME = "IMG_2419.jpg"
RECITAL_PROGRAM_PHOTO_SANDBOX_PATH = "/IMG_2419.jpg"


@register_scenario("music_recital_program_prep_reminder")
class MusicRecitalProgramPrepReminder(PAREScenario):
    """A private music teacher emails the parent (the device owner) a photo of a handwritten recital program announcing the student's upcoming performance slot, and asks the parent to reply confirming the student will perform and to set a reminder to pack the instrument and sheet music the evening before the recital.

The teacher's email arrives with an attached photo of the recital program (a local image asset seeded in Files and attached to the email). The program image shows the recital date, the student's named performance slot time, and the piece they will perform — all as printed/handwritten text that can only be read by viewing the image; the email body explicitly asks the parent to reply to the teacher's message confirming the student will perform, and to set a reminder for the evening before the recital to pack the instrument and sheet music for the performance slot shown on the program.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the recital-program photo via Files to read the recital date, performance slot time, and piece name from the image, (3) proactively propose replying to the teacher to confirm attendance and creating an evening-before prep reminder, and (4) after user acceptance, reply to the teacher's email confirming the student will perform and create the reminder with `due_datetime` set to the evening before the recital date read from the program and a description that records the performance slot time and piece name taken from the image.

This scenario exercises multimodal grounding on a handwritten/photo-like recital program (the image is the sole source for the recital date, slot time, and piece name), cross-app coordination across Email + Files + Reminders, the novel combination of `reply_to_email` (outward confirmation to the teacher) with `add_reminder` (the parent's own evening-before prep), and a visually-derived due date paired with a visually-grounded reminder description — all cued explicitly by the trigger email's request to both confirm attendance and prep ahead of the slot.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email + Reminders apps. The Email app's internal_fs is wired to the Files
        # sandbox so that Step 3 can attach the recital-program photo by sandbox path.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the recital-program photo (resolved local asset) into the Files sandbox.
        # The image is the sole source for the recital date, performance slot time, and
        # piece name; Step 3 attaches it to the trigger email and exposes it via Files.
        local_program_path = Path(
            __import__("os").getenv(
                "PARE_RECITAL_PROGRAM_PHOTO_LOCAL_PATH",
                str(SCENARIO_ASSET_DIR / RECITAL_PROGRAM_PHOTO_FILENAME),
            )
        )
        if not local_program_path.exists():
            raise FileNotFoundError(
                f"Recital program photo not found: {local_program_path}. "
                f"Place {RECITAL_PROGRAM_PHOTO_FILENAME} under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(RECITAL_PROGRAM_PHOTO_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_program_path.read_bytes()))

        # Stable handles that Step 3 (events flow) will reference when injecting the
        # trigger email and verifying the visually-derived reminder due date.
        # Recital date read from the program image: Saturday, July 25, 2026.
        # Evening-before prep reminder due date: 2026-07-24 19:00:00 UTC.
        self.recital_email_id = "recital_program_teacher_email"
        self.recital_program_photo_sandbox_path = RECITAL_PROGRAM_PHOTO_SANDBOX_PATH
        self.expected_prep_reminder_due_datetime = "2026-07-24 19:00:00"

        # Baseline Reminders state is intentionally empty; the prep reminder is created
        # by the agent during the run after the user accepts the proposal.

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

        # Pre-computed constants grounded in the recital-program photo asset the agent
        # inspects at runtime (the image is the sole source for these values).
        teacher_sender = "ms.park@hillsidemusic.com"
        prep_reminder_due_datetime = self.expected_prep_reminder_due_datetime  # evening before recital
        prep_reminder_title = "Pack instrument and sheet music for Maya's recital"
        prep_reminder_description = (
            "Maya Chen's Spring Student Recital is Saturday, July 25, 2026 at 2:45 PM "
            "playing Minuet in G (J.S. Bach) at Hillside Music Hall. Pack her instrument "
            "and sheet music tonight so she is ready for her 2:45 PM slot."
        )

        with EventRegisterer.capture_mode():
            # ENV: Maya's music teacher emails the recital-program photo and asks the parent
            # to (a) reply confirming Maya will perform and (b) set an evening-before prep reminder.
            # Notification template "send_email_to_user_with_id" exists for both user/agent streams.
            recital_email_event = email_app.send_email_to_user_with_id(
                email_id=self.recital_email_id,
                sender=teacher_sender,
                subject="Spring Student Recital — Maya's program (please confirm + set prep reminder)",
                content=(
                    "Hi,\n\n"
                    "Maya's Spring Student Recital is coming up. I've attached a photo of the "
                    "recital program showing her performance slot, the piece she'll play, and the "
                    "venue. Please reply to this email to confirm Maya will perform, and set a "
                    "reminder for the evening before the recital to pack her instrument and sheet "
                    "music for the performance slot shown on the program.\n\n"
                    "Thanks,\nMs. Park"
                ),
                attachment_paths=[self.recital_program_photo_sandbox_path],
            ).delayed(5)

            # ORACLE: read the incoming teacher email to surface its attachment and the
            # "reply to confirm" + "set a reminder for the evening before" request.
            # Motivated by `recital_email_event` subject/body.
            read_email_event = (
                email_app.get_email_by_id(email_id=self.recital_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(recital_email_event, delay_seconds=2)
            )

            # ORACLE: inspect the attached recital-program photo to read the recital date,
            # performance slot time, and piece name (the image is the sole source for these).
            # Motivated by `read_email_event` exposing the attachment and the email body saying
            # "performance slot shown on the program".
            view_program_event = (
                files.display(path=self.recital_program_photo_sandbox_path)
                .oracle()
                .depends_on(read_email_event, delay_seconds=1)
            )

            # ORACLE proposal: cite the teacher's email request (`recital_email_event`:
            # "reply to this email to confirm" + "set a reminder for the evening before the recital")
            # and the recital date / 2:45 PM slot / piece read from the program via `view_program_event`.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Ms. Park emailed Maya's Spring Student Recital program and asked you to "
                        "reply confirming Maya will perform and to set an evening-before prep reminder. "
                        "I read the attached program photo: the recital is Saturday, July 25, 2026, "
                        "Maya's slot is 2:45 PM playing Minuet in G (J.S. Bach) at Hillside Music Hall. "
                        "Want me to reply to Ms. Park confirming Maya will perform, and create a reminder "
                        "for the evening of July 24 to pack her instrument and sheet music for the 2:45 PM slot?"
                    )
                )
                .oracle()
                .depends_on(view_program_event, delay_seconds=2)
            )

            # USER accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please reply to Ms. Park and create the evening-before prep reminder."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # ORACLE write: reply to the teacher's email confirming Maya will perform.
            # Grounded in `recital_email_event` ("reply to this email to confirm") and the
            # visually-read slot from `view_program_event`; user-gated via `acceptance_event`.
            reply_email_event = (
                email_app.reply_to_email(
                    email_id=self.recital_email_id,
                    folder_name="INBOX",
                    content=(
                        "Hi Ms. Park,\n\n"
                        "Thanks for the program. Confirming Maya will perform at her 2:45 PM slot "
                        "on Saturday, July 25, 2026, playing Minuet in G (J.S. Bach). I've also set "
                        "a reminder for the evening before to pack her instrument and sheet music.\n\n"
                        "Best,\nJohn"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # ORACLE write: create the evening-before prep reminder with due_datetime and description
            # derived from the recital date / slot / piece read via `view_program_event`.
            # User-gated via `acceptance_event`.
            add_prep_reminder_event = (
                reminder_app.add_reminder(
                    title=prep_reminder_title,
                    due_datetime=prep_reminder_due_datetime,
                    description=prep_reminder_description,
                    repetition_unit=None,
                    repetition_value=None,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=2)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            recital_email_event,
            read_email_event,
            view_program_event,
            proposal_event,
            acceptance_event,
            reply_email_event,
            add_prep_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            log_entries = env.event_log.list_view()

            # Check 1 — Proposal: the proactive agent offered help to the user via
            # PAREAgentUserInterface.send_message_to_user(...). We do not keyword-match
            # the proposal body and do not validate proposal acceptance.
            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            # Check 2 — Task: the agent completed BOTH promised user-gated writes:
            #   (a) reply_to_email on the teacher's recital email (correct email_id), and
            #   (b) add_reminder with the visually-derived evening-before prep due_datetime.
            # Both writes must appear as AGENT events; folded into a single task_completed
            # boolean so partial completion fails the task check.
            reply_to_teacher_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "reply_to_email"
                and e.action.args.get("email_id") == self.recital_email_id
                for e in log_entries
            )

            prep_reminder_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and str(e.action.args.get("due_datetime", "")).strip()
                == self.expected_prep_reminder_due_datetime
                for e in log_entries
            )

            task_completed = reply_to_teacher_found and prep_reminder_found

            success = proposal_found and task_completed

            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not reply_to_teacher_found:
                    rationale = (
                        "task not completed: agent did not reply to the teacher's "
                        f"recital email (email_id={self.recital_email_id})"
                    )
                else:
                    rationale = (
                        "task not completed: agent did not create the evening-before "
                        f"prep reminder with due_datetime="
                        f"{self.expected_prep_reminder_due_datetime}"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
