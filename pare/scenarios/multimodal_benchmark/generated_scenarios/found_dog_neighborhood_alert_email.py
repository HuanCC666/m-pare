"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

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


@register_scenario("found_dog_neighborhood_alert_email")
class FoundDogNeighborhoodAlertEmail(PAREScenario):
    """Agent composes a neighborhood lost-pet alert email with a found-dog photo and sets a follow-up reminder before the county-shelter deadline.

A neighbor ("Jen R.") emails the user a photo of a friendly stray dog she found in her yard this morning, with no collar. The attached photo (a local image asset seeded in Files and attached to the email) is the only source for the dog's appearance — color, markings, and approximate breed — so the assistant must view the image to describe the dog in the alert; the email body deliberately does not name those visual details. The email body explicitly asks the user to send the photo to the neighborhood alert mailing list at neighborhood-alerts@example.org so a nearby owner might recognize the dog, and to set a reminder to follow up with Jen before she takes the dog to the county shelter tomorrow at 6pm.

The assistant must: (1) read the incoming email with the image attachment, (2) display/inspect the dog photo via Files to observe the dog's appearance, (3) proactively propose composing a fresh alert email to the neighborhood list with the photo attached and a description of the dog taken from the image, plus creating a follow-up reminder due before the shelter deadline, and (4) after user acceptance, compose and send the alert email (start_compose → set_recipients / set_subject / set_body → attach_file → send_composed_email) and create the reminder with due_datetime set to before the shelter deadline stated in the email body.

This scenario exercises multimodal grounding on a photo of an animal where vision supplies appearance/identity rather than printed text, cross-app coordination across Email + Files + Reminders, the novel use of the email compose flow (start_compose with set_recipients/set_subject/set_body/attach_file/send_composed_email) rather than reply/forward to share a photo with a fresh recipient and a personalized description, and a follow-up reminder whose due date is sourced from the email body — all cued explicitly by the neighbor's request to share the photo and track the shelter deadline.."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # Email + Reminders apps. Email shares the sandbox filesystem so that the
        # found-dog photo can be attached to the outgoing alert email.
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        # Load the found-dog photo (local manifest-backed asset) into the sandbox
        # filesystem at /IMG_2414.jpg. This is the only source for the dog's
        # appearance; the triggering email body deliberately omits visual details.
        # The asset lives under the multimodal_benchmark assets directory.
        local_dog_image_path = (
            Path(__file__).parent.parent
            / "multimodal_benchmark"
            / "assets"
            / "image_assets"
            / "found_dog_neighborhood_alert_email"
            / "IMG_2414.jpg"
        )
        if not local_dog_image_path.exists():
            raise FileNotFoundError(
                f"Found-dog photo not found: {local_dog_image_path}. "
                f"Place IMG_2414.jpg under {local_dog_image_path.parent}."
            )
        with self.files.open("/IMG_2414.jpg", "wb") as f:
            f.write(jpeg_bytes_for_sandbox(local_dog_image_path.read_bytes()))

        # Email id reused by Step 3 when the neighbor's message arrives as an
        # environment event (not seeded here, since it is the runtime trigger).
        self.dog_email_id = "found_dog_neighborhood_email"

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

        with EventRegisterer.capture_mode():
            # --- Environment event: Jen's neighbor email arrives with the found-dog photo attached.
            # The email body deliberately omits the dog's visual appearance (color/markings/breed),
            # so the agent must inspect the attached image to describe the dog in the alert.
            # It explicitly asks the user to forward the photo to neighborhood-alerts@example.org
            # and to set a follow-up reminder before the county shelter deadline (tomorrow 6pm).
            found_dog_email_event = email_app.send_email_to_user_with_id(
                email_id=self.dog_email_id,
                sender="jen.r@example.com",
                subject="Found a friendly stray dog in my yard this morning",
                content=(
                    "Hi,\n\n"
                    "I found a friendly stray dog in my yard this morning with no collar. "
                    "I've attached a photo so you can see what it looks like.\n\n"
                    "Could you please forward that photo to the neighborhood alert mailing list at "
                    "neighborhood-alerts@example.org so a nearby owner might recognize it?\n\n"
                    "Also, please set a reminder to follow up with me before I take the dog to the "
                    "county shelter tomorrow at 6pm.\n\n"
                    "Thanks so much,\nJen R."
                ),
                attachment_paths=["/IMG_2414.jpg"],
            ).delayed(5)

            # --- Oracle: agent reads Jen's incoming email, which surfaces the photo attachment
            # to the multimodal observer. Motivated by the env email's subject/body
            # ("Found a friendly stray dog... attached a photo").
            read_found_dog_email_event = (
                email_app.get_email_by_id(email_id=self.dog_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(found_dog_email_event, delay_seconds=2)
            )

            # --- Oracle: agent displays/inspects the attached dog photo via Files. Motivated by
            # read_found_dog_email_event (attachment present) and the env request to "forward that
            # photo" — the agent must see the dog to describe it in the alert.
            view_dog_photo_event = (
                files.display(path="/IMG_2414.jpg")
                .oracle()
                .depends_on(read_found_dog_email_event, delay_seconds=1)
            )

            # --- Oracle: agent proactively proposes composing a fresh neighborhood alert email
            # (with the photo attached and a description of the dog taken from the image) plus
            # a follow-up reminder due before the 6pm shelter deadline. Motivated by
            # found_dog_email_event ("forward that photo to neighborhood-alerts@example.org" and
            # "set a reminder to follow up with me before I take the dog to the county shelter
            # tomorrow at 6pm") and the visual evidence from view_dog_photo_event.
            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Jen emailed about a friendly stray dog she found this morning with no collar, "
                        "and asked you to forward her photo to neighborhood-alerts@example.org so a "
                        "nearby owner might recognize it. I inspected the attached photo and can see "
                        "the dog's appearance (tan-and-white coat, white face blaze, white chest, one "
                        "floppy ear and one semi-erect ear, no collar). Would you like me to compose a "
                        "fresh neighborhood alert email to neighborhood-alerts@example.org with the "
                        "photo attached and a description of the dog, and also set a reminder to "
                        "follow up with Jen before she takes the dog to the county shelter tomorrow "
                        "at 6pm?"
                    )
                )
                .oracle()
                .depends_on(view_dog_photo_event, delay_seconds=2)
            )

            # --- Oracle: user accepts the proposal. Motivated by the proposal_event offering the
            # alert email + reminder plan.
            acceptance_event = (
                aui.accept_proposal(
                    content="Yes, please send the neighborhood alert email and set the follow-up reminder."
                )
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            # --- Oracle: agent composes and sends the neighborhood alert email with the found-dog
            # photo attached and a description grounded in the inspected image. Motivated by
            # acceptance_event (user approved) and the env cue "neighborhood-alerts@example.org".
            # The recipient, subject, body, and attachment are all grounded in prior evidence
            # (env email content + view_dog_photo_event visual observations).
            send_alert_email_event = (
                email_app.send_email(
                    recipients=["neighborhood-alerts@example.org"],
                    subject="Found dog in the neighborhood — please help find its owner",
                    content=(
                        "Hi neighbors,\n\n"
                        "A friendly stray dog was found in a yard this morning with no collar. "
                        "It's a medium-sized dog with a short tan-and-white coat, a distinctive "
                        "white blaze on its face, a white chest, one floppy ear and one semi-erect "
                        "ear. A photo is attached — please take a look and share if anyone recognizes "
                        "the dog or knows a nearby owner who is missing it.\n\n"
                        "Thank you."
                    ),
                    attachment_paths=["/IMG_2414.jpg"],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

            # --- Oracle: agent creates the follow-up reminder due before the shelter deadline
            # (tomorrow 6pm = 2025-11-19 18:00 UTC). Motivated by acceptance_event and the env
            # cue "set a reminder to follow up with me before I take the dog to the county
            # shelter tomorrow at 6pm". Due datetime is sourced from that env-stated deadline.
            create_followup_reminder_event = (
                reminder_app.add_reminder(
                    title="Follow up with Jen about the found dog",
                    description=(
                        "Check whether anyone has claimed the found dog before Jen takes it to the "
                        "county shelter today at 6pm."
                    ),
                    due_datetime="2025-11-19 17:00:00",
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        # Register ALL events here in self.events
        self.events: list[Event] = [
            found_dog_email_event,
            read_found_dog_email_event,
            view_dog_photo_event,
            proposal_event,
            acceptance_event,
            send_alert_email_event,
            create_followup_reminder_event,
        ]

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate proposal offered and task completed (exactly two checks)."""
        try:
            from are.simulation.types import EventType

            log_entries = env.event_log.list_view()
            agent_entries = [e for e in log_entries if e.event_type == EventType.AGENT]

            # --- Check 1: Proposal ---
            # Agent offered a proactive proposal to the user via the agent UI.
            proposal_found = any(
                e.action is not None
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in agent_entries
            )

            # --- Check 2: Task ---
            # Agent completed BOTH promised side effects:
            #   (a) sent the neighborhood alert email to neighborhood-alerts@example.org
            #       with the found-dog photo attached, and
            #   (b) created a follow-up reminder due before the 6pm shelter deadline
            #       (sourced from the env email as 2025-11-19 17:00:00).
            alert_email_sent = False
            followup_reminder_created = False

            for e in agent_entries:
                action = e.action
                if action is None:
                    continue
                if (
                    action.class_name == "StatefulEmailApp"
                    and action.function_name == "send_email"
                ):
                    args = action.resolved_args or action.args
                    recipients = args.get("recipients") or []
                    attachment_paths = args.get("attachment_paths") or []
                    if (
                        "neighborhood-alerts@example.org" in recipients
                        and "/IMG_2414.jpg" in attachment_paths
                    ):
                        alert_email_sent = True
                elif (
                    action.class_name == "StatefulReminderApp"
                    and action.function_name == "add_reminder"
                ):
                    args = action.resolved_args or action.args
                    due_datetime = str(args.get("due_datetime", ""))
                    # Reminder must be due before the 6pm shelter deadline
                    # (env cue: "tomorrow at 6pm" = 2025-11-19 18:00 UTC).
                    if due_datetime.startswith("2025-11-19 17:00"):
                        followup_reminder_created = True

            task_completed = alert_email_sent and followup_reminder_created

            success = proposal_found and task_completed
            if not success:
                if not proposal_found:
                    rationale = "no proactive proposal found"
                elif not alert_email_sent:
                    rationale = (
                        "task not completed: neighborhood alert email not sent to "
                        "neighborhood-alerts@example.org with the found-dog photo attached"
                    )
                else:
                    rationale = (
                        "task not completed: follow-up reminder not created with "
                        "due_datetime before the 2025-11-19 18:00 shelter deadline"
                    )
                return ScenarioValidationResult(success=False, rationale=rationale)
            return ScenarioValidationResult(success=True)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""
