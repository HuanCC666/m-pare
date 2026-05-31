"""Scenario: Agent replies in a group chat with per-person dinner cost from Album receipts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.apps.messaging_v2 import ConversationV2, MessageV2
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Action, CompletedEvent, EventRegisterer, EventType

from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
    StatefulAlbumApp,
    StatefulMessagingApp,
)
from pare.scenarios import PAREScenario
from pare.scenarios.multimodal_benchmark.lib.agent_image_view_log import log_has_agent_image_view
from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox
from pare.scenarios.utils.registry import register_scenario

_ASSETS_DIR = Path(__file__).parent / "assets" / "dinner_receipt_aa_suggestion"
_ORACLE_TOTAL = "64.20"
_WRONG_TOTAL = "86.40"
_PER_PERSON = "16.05"


class _ReceiptPhotoFixture(TypedDict):
    asset: str
    sandbox: str
    taken_time: str
    checked_receipt: bool
    env_key: str


# ``checked_receipt=True`` is the bill the user paid (handwritten check on image).
RECEIPT_PHOTO_FIXTURES: tuple[_ReceiptPhotoFixture, ...] = (
    {
        "asset": "dinner_receipt_a.jpg",
        "sandbox": "/photos/IMG_20251123_191500.jpg",
        "taken_time": "19:15:00",
        "checked_receipt": False,
        "env_key": "PARE_DINNER_RECEIPT_A_LOCAL_PATH",
    },
    {
        "asset": "dinner_receipt_b.jpg",
        "sandbox": "/photos/IMG_20251123_191800.jpg",
        "taken_time": "19:18:00",
        "checked_receipt": True,
        "env_key": "PARE_DINNER_RECEIPT_B_LOCAL_PATH",
    },
    {
        "asset": "dinner_table_snapshot.jpg",
        "sandbox": "/photos/IMG_20251123_192000.jpg",
        "taken_time": "19:20:00",
        "checked_receipt": False,
        "env_key": "PARE_DINNER_TABLE_SNAPSHOT_LOCAL_PATH",
    },
)


@register_scenario("dinner_receipt_aa_suggestion")
class DinnerReceiptAlbumEmailAaSuggestion(PAREScenario):
    """Agent tells the dinner group each person's share after reading receipt photos in Album.

    The user covered Thai Garden dinner for four people last night. Sam and two other
    friends are in a group chat titled "Thai Garden Dinner". Sam messages asking how much
    everyone owes back. Two receipt photos (and a table snapshot decoy) are already in
    the Camera Roll from that evening, with generic file names and no captions. Amounts
    and the paid checkmark are only on the images. The assistant must:
    1. Read Sam's group message.
    2. List tonight's album photos and visually inspect receipt shots.
    3. Identify the checked bill total and compute a four-way split.
    4. Propose replying in the group chat with the per-person amount.
    5. Send the group reply only after accept/reject acceptance.

    Place JPEGs under ``assets/dinner_receipt_aa_suggestion/`` (see ``RECEIPT_PHOTO_FIXTURES``).

    Constraints:
    - Proactive permission before messaging the group.
    - User responses are accept/reject only.
    - No AUI user-input trigger.
    - Sam's message must not state totals or which receipt is correct.
    """

    start_time = datetime(2025, 11, 23, 20, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Valid
    is_benchmark_ready = True

    def _jpeg_from_asset(self, env_key: str, asset_path: Path) -> bytes:
        override = os.getenv(env_key)
        path = Path(override) if override else asset_path
        if not path.exists():
            raise FileNotFoundError(f"Scenario image not found: {path}. Add {asset_path.name} under {_ASSETS_DIR}.")
        return jpeg_bytes_for_sandbox(path.read_bytes())

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        """Seed album photos, dinner group chat, and sandbox JPEGs."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")

        self.files = SandboxLocalFileSystem(name="Files")
        self.album = StatefulAlbumApp(name="Album")
        self.album.internal_fs = self.files

        self.messaging = StatefulMessagingApp(name="Messages")
        self.messaging.internal_fs = self.files

        self.files.mkdir("/photos", create_parents=True)

        env_dt = datetime.fromtimestamp(self.start_time, tz=UTC)
        self.scenario_day = env_dt.strftime("%Y-%m-%d")

        self.receipt_photo_ids: list[str] = []

        for entry in RECEIPT_PHOTO_FIXTURES:
            jpeg = self._jpeg_from_asset(entry["env_key"], _ASSETS_DIR / entry["asset"])
            with self.files.open(entry["sandbox"], "wb") as f:
                f.write(jpeg)

            taken_at = f"{self.scenario_day} {entry['taken_time']}"
            file_name = entry["sandbox"].rsplit("/", 1)[-1]
            photo_id = self.album.add_photo_with_time(
                file_path=entry["sandbox"],
                file_name=file_name,
                caption="",
                description="",
                tags=[],
                location=None,
                taken_at=taken_at,
            )
            if entry["asset"].endswith("_a.jpg") or entry["asset"].endswith("_b.jpg"):
                self.receipt_photo_ids.append(photo_id)

        self.messaging.add_users(["Sam Ortiz", "Riley Chen", "Jordan Lee"])
        self.sam_id = self.messaging.name_to_id["Sam Ortiz"]
        self.riley_id = self.messaging.name_to_id["Riley Chen"]
        self.jordan_id = self.messaging.name_to_id["Jordan Lee"]
        self.user_id = self.messaging.current_user_id

        dinner_conversation = ConversationV2(
            title="Thai Garden Dinner",
            participant_ids=[self.user_id, self.sam_id, self.riley_id, self.jordan_id],
            messages=[
                MessageV2(
                    sender_id=self.riley_id,
                    content="That curry was amazing — thanks for organizing!",
                    timestamp=self.start_time - 7200,
                ),
                MessageV2(
                    sender_id=self.jordan_id,
                    content="Agreed, great spot.",
                    timestamp=self.start_time - 6900,
                ),
            ],
        )
        dinner_conversation.update_last_updated(self.start_time - 6900)
        self.messaging.add_conversation(dinner_conversation)
        self.group_conversation_id = dinner_conversation.conversation_id

        self.apps = [self.agent_ui, self.system_app, self.files, self.album, self.messaging]

    def build_events_flow(self) -> None:
        """Oracle: group message → album today → view receipts → propose → group reply."""
        aui = self.get_typed_app(PAREAgentUserInterface)
        messaging_app = self.get_typed_app(StatefulMessagingApp, "Messages")
        album_app = self.get_typed_app(StatefulAlbumApp, "Album")

        receipt_a_id, receipt_b_id = self.receipt_photo_ids

        with EventRegisterer.capture_mode():
            sam_message_event = messaging_app.create_and_add_message(
                conversation_id=self.group_conversation_id,
                sender_id=self.sam_id,
                content=(
                    "Hey! Thanks again for covering dinner last night. "
                    "Did you take the receipt photos? Can you tell us how much each person should pay you back?\n\n"
                    "I don't remember the total."
                ),
            ).delayed(5)

            read_message_event = (
                messaging_app.read_conversation(
                    conversation_id=self.group_conversation_id,
                    offset=0,
                    limit=10,
                )
                .oracle()
                .depends_on(sam_message_event, delay_seconds=2)
            )

            list_today_event = (
                album_app.list_photos(
                    folder="Camera Roll",
                    offset=0,
                    limit=10,
                    taken_on=self.scenario_day,
                )
                .oracle()
                .depends_on(read_message_event, delay_seconds=2)
            )

            view_receipt_a_event = (
                album_app.view_photo(photo_id=receipt_a_id).oracle().depends_on(list_today_event, delay_seconds=1)
            )

            view_receipt_b_event = (
                album_app.view_photo(photo_id=receipt_b_id).oracle().depends_on(view_receipt_a_event, delay_seconds=1)
            )

            proposal_event = (
                aui.send_message_to_user(
                    content=(
                        "Sam asked in the Thai Garden Dinner group how much each of you owes. "
                        "In today's Camera Roll, one receipt shows $64.20 with a check mark (the bill you paid); "
                        "the other was $86.40 without a check. "
                        f"Split four ways, that's ${_PER_PERSON} per person. "
                        "Would you like me to reply in the group chat with that breakdown?"
                    )
                )
                .oracle()
                .depends_on(view_receipt_b_event, delay_seconds=2)
            )

            acceptance_event = (
                aui.accept_proposal(content="Yes, please message the group the per-person amount.")
                .oracle()
                .depends_on(proposal_event, delay_seconds=2)
            )

            reply_message_event = (
                messaging_app.send_message_to_group_conversation(
                    conversation_id=self.group_conversation_id,
                    content=(
                        f"Hi everyone — I checked the receipt photos from last night. "
                        f"The bill I paid was ${_ORACLE_TOTAL} total for four of us, "
                        f"so that's ${_PER_PERSON} each. Thanks!"
                    ),
                )
                .oracle()
                .depends_on(acceptance_event, delay_seconds=1)
            )

        self.events = [
            sam_message_event,
            read_message_event,
            list_today_event,
            view_receipt_a_event,
            view_receipt_b_event,
            proposal_event,
            acceptance_event,
            reply_message_event,
        ]

    @staticmethod
    def _reply_content(log_entries: list[CompletedEvent]) -> str:
        parts: list[str] = []
        for e in log_entries:
            if e.event_type != EventType.AGENT or not isinstance(e.action, Action):
                continue
            if e.action.class_name != "StatefulMessagingApp":
                continue
            if e.action.function_name not in ("send_message_to_group_conversation", "send_message"):
                continue
            parts.append(str(e.action.args.get("content", "")))
        return " ".join(parts).lower()

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        """Validate group message read, album vision on receipts, proposal, and correct reply amounts."""
        try:
            log_entries = env.event_log.list_view()
            allow_any = bool(getattr(env, "oracle_mode", False))
            receipt_paths = {RECEIPT_PHOTO_FIXTURES[0]["sandbox"], RECEIPT_PHOTO_FIXTURES[1]["sandbox"]}

            message_read = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "read_conversation"
                and str((e.action.args or {}).get("conversation_id", "")) == self.group_conversation_id
                for e in log_entries
            )

            album_browsed = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulAlbumApp"
                and e.action.function_name in ("list_photos", "search_photos")
                for e in log_entries
            )

            receipts_viewed = log_has_agent_image_view(
                log_entries,
                allow_any_event_type=allow_any,
                image_paths=receipt_paths,
                photo_ids=set(self.receipt_photo_ids),
                min_views=2,
            )

            proposal_found = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "PAREAgentUserInterface"
                and e.action.function_name == "send_message_to_user"
                for e in log_entries
            )

            reply_body = self._reply_content(log_entries)
            reply_sent = any(
                e.event_type == EventType.AGENT
                and isinstance(e.action, Action)
                and e.action.class_name == "StatefulMessagingApp"
                and e.action.function_name == "send_message_to_group_conversation"
                and str((e.action.args or {}).get("conversation_id", "")) == self.group_conversation_id
                for e in log_entries
            )
            correct_amounts = _ORACLE_TOTAL in reply_body or _PER_PERSON in reply_body
            wrong_amount = _WRONG_TOTAL in reply_body

            success = (
                message_read
                and album_browsed
                and receipts_viewed
                and proposal_found
                and reply_sent
                and correct_amounts
                and not wrong_amount
            )

            if not success:
                failed: list[str] = []
                if not message_read:
                    failed.append("agent did not read the Thai Garden Dinner group chat")
                if not album_browsed:
                    failed.append("agent did not list or search Album photos")
                if not receipts_viewed:
                    failed.append("agent did not visually inspect both dinner receipt photos in Album")
                if not proposal_found:
                    failed.append("agent did not proactively propose replying in the group with the split")
                if not reply_sent:
                    failed.append("agent did not send_message_to_group_conversation on the dinner thread")
                if wrong_amount:
                    failed.append(f"agent quoted the wrong receipt total ({_WRONG_TOTAL}) in the reply")
                if not correct_amounts:
                    failed.append(
                        f"agent reply did not include checked total {_ORACLE_TOTAL} or per-person {_PER_PERSON}"
                    )
                return ScenarioValidationResult(success=False, rationale="; ".join(failed))

            return ScenarioValidationResult(success=True)
        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)
