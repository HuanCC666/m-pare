from __future__ import annotations

from typing import TYPE_CHECKING, Any

from are.simulation.agents.llm.types import MMObservation
from are.simulation.agents.multimodal import Attachment
from are.simulation.apps.messaging_v2 import FileMessageV2, MessageV2, MessagingAppV2
from are.simulation.tool_utils import OperationType, app_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils import type_check, uuid_hex

from pare.apps.core import StatefulApp
from pare.apps.messaging.states import ConversationList, ConversationOpened

if TYPE_CHECKING:
    from are.simulation.types import CompletedEvent

_IMAGE_ATTACHMENT_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp"})

_MIME_BY_IMAGE_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "bmp": "image/bmp",
}


class StatefulMessagingApp(StatefulApp, MessagingAppV2):
    """Messaging app with navigation state management.

    // RL NOTE: This implements a simple 2-state MDP for messaging:
    // States: ConversationList, ConversationOpened
    // Transitions: open_conversation, go_back
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the stateful messaging app.

        Args:
            *args: Variable length argument list passed to parent classes.
            **kwargs: Arbitrary keyword arguments passed to parent classes.
        """
        super().__init__(*args, **kwargs)
        self.current_user_id = uuid_hex(self.rng)
        self.current_user_name = "John Doe"
        # Register current user in id/name mappings
        self.id_to_name[self.current_user_id] = self.current_user_name
        self.name_to_id[self.current_user_name] = self.current_user_id
        # Set initial state to conversation list
        self.load_root_state()

    def add_users(self, user_names: list[str]) -> None:
        """Add users to the internal name/id maps.

        Args:
            user_names: User display names to ensure exist in the mapping.
        """
        for user_name in user_names:
            if user_name not in self.name_to_id:
                user_id = uuid_hex(self.rng)
                self.name_to_id[user_name] = user_id
                self.id_to_name[user_id] = user_name

    def add_contacts(self, contacts: list[tuple[str, str]]) -> None:
        """Add contacts (name, phone) to the internal name/id maps.

        Args:
            contacts: Pairs of (user_name, phone).
        """
        for user_name, phone in contacts:
            if user_name not in self.name_to_id:
                self.name_to_id[user_name] = phone
                self.id_to_name[phone] = user_name

    def handle_state_transition(self, event: CompletedEvent) -> None:
        """Handle state transitions based on tool events.

        // RL NOTE: This implements T(s,a) -> s' for the messaging MDP.

        Args:
            event: Completed event from tool execution
        """
        current_state = self.current_state
        function_name = event.function_name()

        if current_state is None or function_name is None:
            return

        # Transition: ConversationList -> ConversationOpened
        if isinstance(current_state, ConversationList) and function_name in {"open_conversation", "read_conversation"}:
            args = event.action.resolved_args or event.action.args
            conversation_id = args.get("conversation_id")
            if conversation_id:
                self.set_current_state(ConversationOpened(conversation_id))

        # go_back transitions are handled automatically by StatefulApp.go_back()

    def create_root_state(self) -> ConversationList:
        """Return the conversation list root state."""
        return ConversationList()

    @staticmethod
    def _mime_for_image_attachment(filename: str | None) -> str | None:
        if not filename or "." not in filename:
            return None
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in _IMAGE_ATTACHMENT_EXTENSIONS:
            return None
        return _MIME_BY_IMAGE_EXT.get(ext)

    def _messages_to_mm_observation(self, messages: list[MessageV2], metadata: dict[str, Any]) -> MMObservation:
        """Expose conversation text plus image attachments for multimodal agents."""
        attachments: list[Attachment] = []
        for message in messages:
            if not isinstance(message, FileMessageV2):
                continue
            mime = self._mime_for_image_attachment(message.attachment_name)
            if mime and message.attachment:
                attachments.append(
                    Attachment(
                        base64_data=message.attachment,
                        mime=mime,
                        name=message.attachment_name or "attachment",
                    )
                )
        content_payload = {
            "messages": [str(message) for message in messages],
            "metadata": metadata,
        }
        return MMObservation(content=str(content_payload), attachments=attachments)

    @type_check
    @env_tool()
    @event_registered(operation_type=OperationType.WRITE, event_type=EventType.ENV)
    def create_and_add_message(
        self,
        conversation_id: str,
        sender_id: str,
        content: str,
        attachment_path: str | None = None,
    ) -> None:
        """Inject an incoming message, optionally with an attachment."""
        if conversation_id not in self.conversations:
            raise ValueError(f"Conversation with id {conversation_id} not found")
        if sender_id not in self.conversations[conversation_id].participant_ids:
            raise ValueError(f"Sender {sender_id} not in conversation")
        if attachment_path is None:
            message = MessageV2(
                message_id=uuid_hex(self.rng),
                sender_id=sender_id,
                content=content,
                timestamp=self.time_manager.time(),
            )
        else:
            message = self._create_message(sender_id=sender_id, content=content, attachment_path=attachment_path)
        self.conversations[conversation_id].messages.append(message)
        self.conversations[conversation_id].update_last_updated(message.timestamp)

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_conversation(
        self,
        conversation_id: str,
        offset: int = 0,
        limit: int = 10,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> MMObservation:
        """Read a conversation and expose image attachments to multimodal agents."""
        if conversation_id not in self.conversations:
            raise ValueError(f"Conversation with id {conversation_id} not found")
        if offset < 0:
            raise ValueError("Offset must be positive")

        conversation = self.conversations[conversation_id]
        messages = conversation.get_messages_in_date_range(min_date, max_date)
        if offset > len(messages):
            raise ValueError("Offset is larger than the number of messages")

        messages.sort(key=lambda x: x.timestamp, reverse=True)
        conversation_length = len(messages)
        start_index = offset
        end_index = min(len(messages), offset + limit)
        window_messages = messages[start_index:end_index]
        metadata = {
            "message_range": (start_index, end_index),
            "conversation_length": conversation_length,
            "conversation_id": conversation_id,
            "conversation_title": conversation.title,
        }
        return self._messages_to_mm_observation(window_messages, metadata)
