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
SCENARIO_ASSET_DIR = (
    Path(__file__).parent.parent
    / "multimodal_benchmark"
    / "assets"
    / "image_assets"
    / "worn_tire_road_trip_booking"
)
WORN_TIRE_PHOTO_FILENAME = "IMG_2413.jpg"
WORN_TIRE_PHOTO_SANDBOX_PATH = "/IMG_2413.jpg"
HILLSIDE_TIRE_EMAIL_ID = "email-hillside-auto-service-worn-tire-photo"


@register_scenario("worn_tire_road_trip_booking")
class WornTireRoadTripBooking(PAREScenario):
    """A mechanic emails the user a photo of one of their car's front tires taken during a routine oil change, showing tread worn down to the wear bars with visible cord exposure, and asks them to reply to confirm a tire-replacement booking before any upcoming long drive. The email body (from "Hillside Auto Service") states that two front tires need replacing, quotes a total price, offers an availability slot tomorrow morning, and explicitly notes that driving on cords is unsafe for long trips and asks the user to confirm whether they have upcoming travel so the shop can prioritize the job. The attached photo (a local image asset seeded in Files and attached to the email) is the only source for the actual tread condition — the email says "worn" but does not describe the cord exposure — so the assistant must view the image to recognize the severity that justifies urgent replacement.

A pre-existing reminder titled "Weekend road trip to Tahoe" is already in the Reminders app, dated this coming Saturday. The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the tire photo via Files to observe the bald tread and exposed cords, (3) list upcoming reminders to discover the Tahoe road trip (motivated by the mechanic's explicit question about upcoming travel), (4) proactively propose replying to Hillside Auto Service to confirm the tire-replacement booking for tomorrow morning — noting the upcoming road trip as the reason for urgency — and creating a reminder to drop off the car for the tire appointment before the weekend trip, and (5) after user acceptance, reply to the mechanic's email confirming the booking and create the reminder with `due_datetime` set to tomorrow morning and a description referencing the road trip.

This scenario exercises multimodal grounding on a photo of a physical part where vision supplies condition/severity (cord exposure) that the email text deliberately understates, cross-app coordination across Email + Files + Reminders, the use of `list_upcoming_reminders` to discover an existing commitment that motivates urgency (rather than scanning all reminders), and the combination of `reply_to_email` (outward booking confirmation to the shop) with `add_reminder` (the owner's own drop-off tracking) — all cued explicitly by the mechanic's request to confirm the booking and disclose upcoming travel.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email app: connect its internal_fs to the sandbox so attachments can be
        # read from Files. The triggering Hillside Auto Service email (with the
        # worn-tire photo attached) is delivered as an early non-oracle environment
        # event in build_events_flow() (Step 3), not seeded silently here.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files

        # Reminders app: seed only pre-existing baseline state here. The
        # "Weekend road trip to Tahoe" reminder predates the mechanic's email and
        # is the upcoming commitment the agent must discover via list_upcoming
        # reminders / get_all_reminders to justify urgent tire replacement before
        # the weekend trip. The drop-off reminder is created at runtime by the
        # agent after user acceptance.
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the worn-tire photo into the sandbox Files so that Step 3 can attach
        # it to the trigger email and the agent can display() it. The photo is the
        # only source for the actual tread condition (bald tread with exposed cord)
        # that justifies urgent replacement before the upcoming road trip.
        local_tire_path = SCENARIO_ASSET_DIR / WORN_TIRE_PHOTO_FILENAME
        if not local_tire_path.exists():
            raise FileNotFoundError(
                f"Worn tire photo not found: {local_tire_path}. "
                f"Place {WORN_TIRE_PHOTO_FILENAME} under {SCENARIO_ASSET_DIR}."
            )
        with self.files.open(WORN_TIRE_PHOTO_SANDBOX_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_tire_path.read_bytes()))

        # Pre-existing reminder from the user's own plans (before start_time).
        # start_time is 2025-11-18 09:00 UTC (Tuesday); "this coming Saturday" is
        # 2025-11-22. The agent must discover this reminder to recognize the
        # upcoming long drive that makes the worn tires urgent.
        self.tahoe_road_trip_reminder_id = self.reminder.add_reminder(
            title="Weekend road trip to Tahoe",
            due_datetime="2025-11-22 08:00:00",
            description=(
                "Drive up to Lake Tahoe for the weekend with friends. "
                "Leave early Saturday morning, return Sunday evening."
            ),
            repetition_unit=None,
            repetition_value=1,
        )

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

        # Tomorrow morning drop-off slot referenced by the mechanic's email and the
        # drop-off reminder created after acceptance (start_time = 2025-11-18 09:00 UTC).
        hillside_dropoff_reminder_due = "2025-11-19 08:30:00"

        with EventRegisterer.capture_mode():
            # --- NON-ORACLE ENVIRONMENT EVENT ---
            # Hillside Auto Service emails the user a photo of the worn front tire
            # taken during today's oil change, quotes a price, offers a tomorrow-
            # morning slot, and explicitly asks the user to confirm the booking and
            # disclose any upcoming travel so the shop can prioritize the job.
            hillside_email_event = email_app.send_email_to_user_with_id(
                email_id=HILLSIDE_TIRE_EMAIL_ID,
                sender="service@hillside-auto.com",
                subject="Front tire replacement - photo attached, please confirm booking",
                content=(
                    "Hi,\n\n"
                    "During your oil change today we inspected your tires. Both front "
                    "tires are worn and need to be replaced. Total for two tires "
                    "installed is $420.\n\n"
                    "We have an availability slot tomorrow morning (Nov 19) at 9 AM. "
                    "Driving on worn tires is unsafe for long trips, so please reply "
                    "to confirm the booking for that slot and let us know whether you "
                    "have any upcoming travel so we can prioritize the job.\n\n"
                    "A photo of one of the front tires is attached so you can see the "
                    "condition. Please reply to confirm.\n\n"
                    "Thanks,\nHillside Auto Service"
                ),
                attachment_paths=[WORN_TIRE_PHOTO_SANDBOX_PATH],
            ).delayed(6)

            # --- ORACLE / AGENT EVENTS ---
            # Motivation: the Hillside email ("Front tire replacement - photo attached,
            # please confirm booking") tells the agent an email with a tire photo is in
            # the inbox; read it to retrieve the body + attachment.
            read_hillside_email_event = (
                email_app.get_email_by_id(
                    email_id=HILLSIDE_TIRE_EMAIL_ID, folder_name="INBOX"
                )
                .oracle()
                .depends_on(hillside_email_event, delay_seconds=2)
            )

            # Motivation: the email says "A photo of one of the front tires is attached
            # so you can see the condition" but only says "worn" in the body; the actual
            # tread condition (bald with exposed cord) is only visible in the image, so
            # display it via Files before forming any severity-grounded proposal.
            view_tire_photo_event = (
                files.display(path=WORN_TIRE_PHOTO_SANDBOX_PATH)
                .oracle()
                .depends_on(read_hillside_email_event, delay_seconds=1)
            )

            # Motivation: the Hillside email explicitly asks "let us know whether you
            # have any upcoming travel" - list upcoming reminders to check for planned
            # long drives that would make the worn tires urgent.
            list_upcoming_reminders_event = (
                reminder_app.current_state.list_upcoming_reminders()
                .oracle()
                .depends_on(view_tire_photo_event, delay_seconds=1)
            )

            # Motivation: grounded by (a) the Hillside email content ("Both front tires
            # are worn", "Driving on worn tires is unsafe for long trips", "reply to
            # confirm the booking ... tomorrow morning (Nov 19) at 9 AM", "let us know
            # whether you have any upcoming travel") and (b) the image inspection
            # (bald tread with exposed cord) and (c) list_upcoming_reminders revealing
            # the "Weekend road trip to Tahoe" reminder this Saturday.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Hillside Auto Service emailed about your front tires and "
                        "attached a photo. I read the email and inspected the image: "
                        "the tread is bald with exposed cord, which the shop notes is "
                        "unsafe for long trips. I also listed your upcoming reminders "
                        "and found 'Weekend road trip to Tahoe' this Saturday.\n\n"
                        "Hillside offered a tomorrow-morning slot (Nov 19 at 9 AM, "
                        "$420 for both front tires) and asked you to reply to confirm "
                        "the booking and disclose any upcoming travel. Want me to "
                        "reply to Hillside confirming the tomorrow-morning booking "
                        "(noting the Tahoe trip as the reason for urgency) and create "
                        "a reminder to drop off the car tomorrow morning?"
                    )
                )
                .oracle()
                .depends_on(list_upcoming_reminders_event, delay_seconds=1)
            )

            # User accepts the proposal.
            acceptance_event = (
                aui.accept_proposal(
                    content=(
                        "Yes, please reply to Hillside to confirm the tomorrow-morning "
                        "booking and create the drop-off reminder."
                    )
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=1)
            )

            # Motivation: user accepted the proposal; reply to the Hillside email
            # (email_id from the env trigger) confirming the tomorrow-morning slot and
            # disclosing the upcoming Tahoe road trip as the reason for urgency.
            reply_to_hillside_event = (
                email_app.reply_to_email(
                    email_id=HILLSIDE_TIRE_EMAIL_ID,
                    folder_name="INBOX",
                    content=(
                        "Hi Hillside,\n\n"
                        "I'd like to confirm the tire-replacement booking for "
                        "tomorrow morning (Nov 19 at 9 AM) for both front tires "
                        "($420). I do have an upcoming long drive this weekend "
                        "(road trip to Tahoe on Saturday), so prioritizing the "
                        "replacement would be much appreciated.\n\n"
                        "Thanks,\nJohn"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # Motivation: user accepted the proposal; create a drop-off reminder for
            # tomorrow morning (before the 9 AM booking) referencing the Tahoe trip as
            # the reason for urgency. due_datetime grounded in the Hillside email's
            # "tomorrow morning (Nov 19) at 9 AM" slot.
            create_dropoff_reminder_event = (
                reminder_app.add_reminder(
                    title="Drop off car at Hillside Auto for tire replacement",
                    due_datetime=hillside_dropoff_reminder_due,
                    description=(
                        "Drop off the car at Hillside Auto Service for replacement of "
                        "both front tires (booking confirmed for Nov 19 at 9 AM, "
                        "$420). Needed before the weekend road trip to Tahoe on "
                        "Saturday Nov 22 - driving on the worn tires with exposed cord "
                        "is unsafe for the long trip."
                    ),
                    repetition_unit=None,
                    repetition_value=1,
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            hillside_email_event,
            read_hillside_email_event,
            view_tire_photo_event,
            list_upcoming_reminders_event,
            proposal_event,
            acceptance_event,
            reply_to_hillside_event,
            create_dropoff_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import Action, EventType

            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # Expected drop-off reminder due_datetime grounded in the Hillside email's
            # "tomorrow morning (Nov 19) at 9 AM" slot (start_time = 2025-11-18 09:00 UTC).
            expected_dropoff_due = "2025-11-19 08:30:00"

            # Check 1 — Proposal: the proactive agent offered help to the user via
            # PAREAgentUserInterface.send_message_to_user (no text matching; acceptance
            # is not validated).
            proposal_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # Check 2 — Task: agent completed both promised side effects correctly
            # after the proposal: (a) replied to the Hillside Auto Service email
            # (reply_to_email on StatefulEmailApp with the seeded email_id), and
            # (b) created the drop-off reminder (add_reminder on StatefulReminderApp
            # with due_datetime matching the tomorrow-morning slot). Both writes must
            # pass for task_completed to be True.
            reply_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name == "reply_to_email"
                and e.action.args.get("email_id") == HILLSIDE_TIRE_EMAIL_ID
                for e in agent_entries
            )

            def _normalized_due(value: Any) -> str:
                if value is None:
                    return ""
                text = value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value)
                return text.strip()

            reminder_found = any(
                isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name == "add_reminder"
                and _normalized_due(e.action.args.get("due_datetime")) == expected_dropoff_due
                for e in agent_entries
            )

            task_completed = reply_found and reminder_found

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not reply_found:
                    rationale = "task not completed: reply to Hillside email not sent"
                else:
                    rationale = (
                        "task not completed: drop-off reminder not created with "
                        f"due_datetime {expected_dropoff_due}"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)

            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
