"""Scenario: Agent sends a wrong-item exchange email using Album photos after delivery notice."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, EventRegisterer, EventType

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulAlbumApp,
    StatefulEmailApp,
)
from pare.apps.reminder import StatefulReminderApp
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS = Path(__file__).parent / "assets" / "delivery_wrong_item_suggestion"
_LABEL_PATH = "/photos/shipping_label.jpg"
_CONTENTS_PATH = "/photos/package_contents.jpg"
_ORDER_ID = "order_88421"
_ORDERED_NAME = "Wireless Earbuds Pro"
_SUPPORT_EMAIL = "support@audiodepot.com"
_WRONG_KEYWORD = "storage"


@register_scenario("delivery_wrong_item_suggestion")
class DeliveryLabelWrongItemNoteSuggestion(PAREScenario):
    """Agent emails support about a wrong-item delivery using Album photos.

    The user left a reminder to send an exchange email. Delivery photos (shipping label
    and box contents) are already in Camera Roll with generic file names and no captions.
    A store delivery confirmation email arrives with the after-sales address and a note
    that product issues must include supporting photos. The assistant must:
    1. Read the delivery confirmation email and the exchange reminder.
    2. Inspect both delivery photos in Album.
    3. Propose emailing support with the photos attached.
    4. Send only after accept/reject acceptance.

    Constraints:
    - Proactive permission before sending email.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Reminder text and delivery email must not state what was actually inside the box.
    """

    start_time = datetime(2025, 11, 21, 14, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    DEFAULT_LABEL_IMAGE = _ASSETS / "shipping_label.jpg"
    DEFAULT_CONTENTS_IMAGE = _ASSETS / "package_contents.jpg"

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed reminder, album photos, sandbox JPEGs, and email app."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files
        self.email = StatefulEmailApp(name="Email")
        self.email.internal_fs = self.files
        self.reminder = StatefulReminderApp(name="Reminders")

        label_path = Path(os.getenv("PARE_SHIPPING_LABEL_LOCAL_PATH", str(self.DEFAULT_LABEL_IMAGE)))
        contents_path = Path(os.getenv("PARE_PACKAGE_CONTENTS_LOCAL_PATH", str(self.DEFAULT_CONTENTS_IMAGE)))
        if not label_path.exists() or not contents_path.exists():
            raise FileNotFoundError(
                f"Delivery photos missing. Place shipping_label.jpg and package_contents.jpg under {_ASSETS}."
            )

        self.files.mkdir("/photos", create_parents=True)
        with self.files.open(_LABEL_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(label_path.read_bytes()))
        with self.files.open(_CONTENTS_PATH, "wb") as f:
            f.write(jpeg_bytes_for_sandbox(contents_path.read_bytes()))

        env_dt = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.scenario_day = env_dt.strftime("%Y-%m-%d")

        self.label_photo_id = self.album.add_photo_with_time(
            file_path=_LABEL_PATH,
            file_name="shipping_label.jpg",
            caption="",
            description="",
            tags=[],
            location=None,
            taken_at=f"{self.scenario_day} 13:45:00",
        )
        self.contents_photo_id = self.album.add_photo_with_time(
            file_path=_CONTENTS_PATH,
            file_name="package_contents.jpg",
            caption="",
            description="",
            tags=[],
            location=None,
            taken_at=f"{self.scenario_day} 13:46:00",
        )
        self.delivery_photo_ids = [self.label_photo_id, self.contents_photo_id]

        self.exchange_reminder_id = self.reminder.add_reminder(
            title="Send exchange email",
            due_datetime="2025-11-21 13:55:00",
            description="Remember to email support about the wrong delivery item.",
        )

        self.delivery_email_id = "audiodepot_delivery_confirmation"
        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.email, self.reminder]

    def build_events_flow(self) -> None:
        """Oracle: delivery email → reminder + album → propose → exchange email with photos."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        email_app = self.get_typed_app(StatefulEmailApp, "Email")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")
        reminder_app = self.get_typed_app(StatefulReminderApp, "Reminders")

        label_id, contents_id = self.delivery_photo_ids

        with EventRegisterer.capture_mode():
            delivery_email_event = email_app.send_email_to_user_with_id(
                email_id=self.delivery_email_id,
                sender="orders@audiodepot.com",
                subject=f"Your AudioDepot order {self._order_suffix()} has been delivered",
                content=(
                    f"Hi,\n\n"
                    f"Your order {_ORDER_ID} has been delivered.\n\n"
                    f"If anything is wrong with your items, contact us at {_SUPPORT_EMAIL}. "
                    f"Please include your order number and attach clear photos when reporting product issues.\n\n"
                    f"Thank you for shopping with AudioDepot."
                ),
            ).delayed(7)

            read_delivery_email_event = (
                email_app.get_email_by_id(email_id=self.delivery_email_id, folder_name="INBOX")
                .oracle()
                .depends_on(delivery_email_event, delay_seconds=2)
            )

            list_reminders_event = (
                reminder_app.current_state.list_all_reminders()
                .oracle()
                .depends_on(read_delivery_email_event, delay_seconds=1)
            )

            list_photos_event = (
                album_app.list_photos(
                    folder="Camera Roll",
                    offset=0,
                    limit=10,
                    taken_on=self.scenario_day,
                )
                .oracle()
                .depends_on(list_reminders_event, delay_seconds=1)
            )

            view_label_event = (
                album_app.view_photo(photo_id=label_id).oracle().depends_on(list_photos_event, delay_seconds=1)
            )

            view_contents_event = (
                album_app.view_photo(photo_id=contents_id).oracle().depends_on(view_label_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        f"Your reminder says to send an exchange email, and AudioDepot confirmed delivery of order "
                        f"{self._order_suffix()}. The label photo matches {_ORDERED_NAME}, but the box photo shows "
                        "a gray fabric storage bin. "
                        f"Would you like me to email {_SUPPORT_EMAIL} with both delivery photos attached?"
                    )
                )
                .oracle()
                .depends_on(view_contents_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, send the exchange email with the photos.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            send_exchange_email_event = (
                email_app.send_email(
                    recipients=[_SUPPORT_EMAIL],
                    subject=f"Wrong item delivered — order {_ORDER_ID}",
                    content=(
                        f"Hello,\n\n"
                        f"I received order {_ORDER_ID} for {_ORDERED_NAME}, but the package contained "
                        f"a gray fabric storage bin instead. I have attached photos of the shipping label "
                        f"and the contents.\n\n"
                        f"Please help arrange an exchange.\n\n"
                        f"Thank you."
                    ),
                    attachment_paths=[_LABEL_PATH, _CONTENTS_PATH],
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            delivery_email_event,
            read_delivery_email_event,
            list_reminders_event,
            list_photos_event,
            view_label_event,
            view_contents_event,
            proposal_event,
            acceptance_event,
            send_exchange_email_event,
        ]

    @staticmethod
    def _order_suffix() -> str:
        return _ORDER_ID[-5:]

    @staticmethod
    def _exchange_email_valid(args: dict[str, object]) -> bool:
        blob = str(args).lower()
        return _SUPPORT_EMAIL in blob and _ORDER_ID.lower() in blob

    @staticmethod
    def _exchange_body_valid(args: dict[str, object]) -> bool:
        blob = str(args.get("content", "")).lower()
        return _WRONG_KEYWORD in blob and "earbud" in blob

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:  # noqa: C901
        """Validate delivery email, reminder, album vision, proposal, and support email with photos."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))

            delivery_email_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulEmailApp"
                and e.action.function_name in ("get_email_by_id", "download_attachments")
                and str((e.action.args or {}).get("email_id", "")) == self.delivery_email_id
                for e in log_entries
            )

            reminder_checked = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulReminderApp"
                and e.action.function_name
                # Proactive agent reads reminders via Reminders app_tools (not the UI state user_tools).
                in ("get_all_reminders", "get_due_reminders", "get_reminder_with_id")
                for e in log_entries
            )

            dual_view = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_paths=[_LABEL_PATH, _CONTENTS_PATH],
                photo_ids=set(self.delivery_photo_ids),
                min_views=2,
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            exchange_email_found = False
            attachments_ok = False
            body_ok = False
            for e in log_entries:
                if e.event_type != EventType.AGENT or not isinstance(e.action, Action):
                    continue
                if e.action.class_name != "StatefulEmailApp":
                    continue
                if e.action.function_name not in ("send_email", "send_composed_email", "save_draft"):
                    continue
                args = e.action.args or {}
                if not self._exchange_email_valid(args):
                    continue
                exchange_email_found = True
                attachment_paths = args.get("attachment_paths", [])
                has_label = any(_LABEL_PATH in str(p) or "shipping_label" in str(p) for p in attachment_paths)
                has_contents = any(_CONTENTS_PATH in str(p) or "package_contents" in str(p) for p in attachment_paths)
                attachments_ok = has_label and has_contents
                body_ok = self._exchange_body_valid(args)
                break

            success = proposal_found and exchange_email_found and attachments_ok and body_ok

            advisory_failures: list[str] = []
            if not delivery_email_read:
                advisory_failures.append("agent did not read the delivery confirmation email")
            if not reminder_checked:
                advisory_failures.append("agent did not check reminders about sending the exchange email")
            if not dual_view:
                advisory_failures.append(
                    f"agent did not visually inspect both {_LABEL_PATH} and {_CONTENTS_PATH} in Album"
                )

            if not success:
                failed: list[str] = []
                if not proposal_found:
                    failed.append("agent did not proactively propose emailing support with delivery photos")
                if not exchange_email_found:
                    failed.append(f"agent did not send an email to {_SUPPORT_EMAIL} about the wrong delivery")
                elif not attachments_ok:
                    failed.append(
                        "agent email to support did not attach both shipping label and package contents photos"
                    )
                elif not body_ok:
                    failed.append("agent support email did not describe earbuds vs storage bin in the message body")
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            if advisory_failures:
                return ScenarioValidationResult(
                    success=True,
                    rationale="advisory (not scored): " + "; ".join(advisory_failures),
                )

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
