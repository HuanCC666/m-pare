"""Stateful email app combining Meta-ARE's email backend with PARE navigation."""

from __future__ import annotations

import os
from typing import Any

from are.simulation.agents.llm.types import MMObservation
from are.simulation.agents.multimodal import Attachment
from are.simulation.apps.email_client import Email, EmailClientV2, EmailFolderName
from are.simulation.tool_utils import OperationType, app_tool, env_tool
from are.simulation.types import CompletedEvent, EventType, disable_events, event_registered
from are.simulation.utils import type_check, uuid_hex

from pare.apps.core import StatefulApp
from pare.apps.email.states import ComposeDraft, ComposeEmail, EmailDetail, MailboxView

# When the model passes a *file* path as ``path_to_save``, Meta-ARE joins ``(path, attachment_name)``
# and tries to open a nested path like ``downloads/foo.jpg/bar.png`` which fails. Treat those as
# the parent directory instead.
_DOWNLOAD_FILE_EXTENSIONS: frozenset[str] = frozenset({
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "heic",
    "bmp",
    "pdf",
    "txt",
    "md",
    "doc",
    "docx",
    "xlsx",
    "pptx",
    "csv",
    "zip",
})

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


class StatefulEmailApp(StatefulApp, EmailClientV2):
    """Email client with navigation state management for user tool filtering."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise the email app with the inbox as the starting state."""
        super().__init__(*args, **kwargs)
        self.user_email = "john@pare.com"
        self.load_root_state()

    def handle_state_transition(self, event: CompletedEvent) -> None:
        """Update navigation state in response to completed tool events."""
        current_state = self.current_state
        function_name = event.function_name()

        if current_state is None or function_name is None:  # pragma: no cover - defensive
            return

        action = event.action
        args = action.resolved_args or action.args
        if isinstance(current_state, MailboxView):
            self._handle_mailbox_transition(current_state, function_name, args, event)
            return

        if isinstance(current_state, EmailDetail):
            self._handle_detail_transition(function_name, event)
            if function_name in {"delete", "move"} and self.navigation_stack:
                self.go_back()
            return

        if isinstance(current_state, ComposeEmail):
            self._handle_compose_transition(function_name)

    def _handle_mailbox_transition(
        self, current_state: MailboxView, function_name: str, args: dict[str, Any], event: CompletedEvent
    ) -> None:
        """Handle transitions triggered from the mailbox view."""
        if function_name in {"open_email_by_id", "open_email_by_index"}:
            folder = self._resolve_folder_from_args(args, current_state.folder)
            email_id = args.get("email_id")
            if email_id is None:
                email_id = self._email_id_from_event(event)
            if email_id is not None:
                self.set_current_state(EmailDetail(email_id=email_id, folder_name=folder))
            return

        if function_name == "switch_folder":
            folder = self._resolve_folder_from_args(args, current_state.folder)
            self.set_current_state(MailboxView(folder=folder))
            return

        if function_name == "start_compose":
            draft = self._compose_draft_from_event(event)
            self.set_current_state(ComposeEmail(draft=draft))

    def _handle_detail_transition(self, function_name: str, event: CompletedEvent) -> None:
        """Handle transitions triggered from the email detail view."""
        if function_name == "start_compose_reply":
            draft = self._compose_draft_from_event(event)
            self.set_current_state(ComposeEmail(draft=draft))

    def _handle_compose_transition(self, function_name: str) -> None:
        """Handle transitions triggered from the compose view."""
        if function_name in {"send_composed_email", "save_draft", "discard_draft"} and self.navigation_stack:
            self.go_back()

    @staticmethod
    def _resolve_folder_from_args(args: dict[str, Any], default_folder: str) -> str:
        folder = args.get("folder_name")
        if isinstance(folder, EmailFolderName):
            return folder.value
        if isinstance(folder, str):
            try:
                return EmailFolderName[folder.upper()].value
            except KeyError:
                return folder.upper()
        return default_folder

    @staticmethod
    def _mime_for_image_attachment(filename: str) -> str | None:
        if "." not in filename:
            return None
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in _IMAGE_ATTACHMENT_EXTENSIONS:
            return None
        return _MIME_BY_IMAGE_EXT.get(ext)

    def _email_to_mm_observation(self, email: Email) -> MMObservation:
        """Return email text plus raster attachments so vision models can read images."""
        attachments: list[Attachment] = []
        if email.attachments:
            for fname, b64_bytes in email.attachments.items():
                mime = self._mime_for_image_attachment(fname)
                if mime and b64_bytes:
                    attachments.append(Attachment(base64_data=b64_bytes, mime=mime, name=fname))
        return MMObservation(content=str(email), attachments=attachments)

    @staticmethod
    def _normalize_download_directory(path_to_save: str) -> str:
        p = (path_to_save or "Downloads/").strip().replace("\\", "/")
        if not p:
            return "Downloads"
        p = p.rstrip("/")
        if not p:
            return "Downloads"
        bn = os.path.basename(p)
        if "." in bn:
            ext = bn.rsplit(".", 1)[-1].lower()
            if ext in _DOWNLOAD_FILE_EXTENSIONS:
                parent = os.path.dirname(p)
                return parent if parent else "."
        return p

    @staticmethod
    def _email_id_from_event(event: CompletedEvent) -> str | None:
        metadata_value = event.metadata.return_value if event.metadata else None
        if isinstance(metadata_value, Email):
            return metadata_value.email_id
        if isinstance(metadata_value, dict):
            return metadata_value.get("email_id")
        if isinstance(metadata_value, MMObservation):
            action = event.action
            if action is None:
                return None
            args = action.resolved_args or action.args
            if not isinstance(args, dict):
                return None
            args_ns = {k: v for k, v in args.items() if k != "self"}
            eid = args_ns.get("email_id")
            if isinstance(eid, str):
                return eid
            idx = args_ns.get("idx")
            if idx is None:
                idx = args_ns.get("index")
            folder_raw = args_ns.get("folder_name", EmailFolderName.INBOX.value)
            app = action.app
            if isinstance(idx, int) and app is not None:
                try:
                    resolved = EmailClientV2.get_email_by_index(app, idx, folder_raw)
                except (ValueError, KeyError, TypeError):
                    return None
                return resolved.email_id
        return None

    @staticmethod
    def _compose_draft_from_event(event: CompletedEvent) -> ComposeDraft | None:
        metadata_value = event.metadata.return_value if event.metadata else None
        if not isinstance(metadata_value, dict):
            return None
        draft_data = metadata_value.get("draft")
        if isinstance(draft_data, ComposeDraft):
            return draft_data
        if isinstance(draft_data, dict):
            return ComposeDraft(
                recipients=draft_data.get("recipients", []),
                cc=draft_data.get("cc", []),
                subject=draft_data.get("subject", ""),
                body=draft_data.get("body", ""),
                attachments=draft_data.get("attachments", []),
                reply_to=draft_data.get("reply_to"),
                reply_to_folder=draft_data.get("folder_name"),
                default_recipients=list(draft_data.get("recipients", [])),
                default_subject=draft_data.get("subject", ""),
            )
        return None

    def send_reply_from_draft(self, draft: ComposeDraft) -> str:
        """Send a reply using the draft metadata, preserving user edits."""
        if not draft.reply_to:
            raise ValueError("Draft does not reference a reply target")

        attachments = draft.attachments or []
        folder_name = draft.reply_to_folder or EmailFolderName.INBOX.value
        recipients = draft.recipients or draft.default_recipients
        subject = draft.subject or draft.default_subject or ""
        cc = draft.cc

        return self._send_reply_email(
            email_id=draft.reply_to,
            folder_name=folder_name,
            content=draft.body,
            attachment_paths=attachments,
            recipients=recipients,
            subject=subject,
            cc=cc,
            fallback_recipients=draft.default_recipients,
            fallback_subject=draft.default_subject,
        )

    def _send_reply_email(
        self,
        *,
        email_id: str,
        folder_name: str,
        content: str,
        attachment_paths: list[str],
        recipients: list[str],
        subject: str,
        cc: list[str],
        fallback_recipients: list[str],
        fallback_subject: str | None,
    ) -> str:
        folder_enum = EmailFolderName[folder_name.upper()] if isinstance(folder_name, str) else folder_name
        if folder_enum not in self.folders:
            raise ValueError(f"Folder {folder_name} not found")

        replying_to_email = self.folders[folder_enum].get_email_by_id(email_id)

        def get_default_recipient(email: Email) -> str:
            while email.sender == self.user_email and email.parent_id:
                email_found = False
                for folder in self.folders:
                    try:
                        email = self.folders[folder].get_email_by_id(email.parent_id)
                        email_found = True
                        break
                    except (KeyError, ValueError):
                        continue
                if not email_found:
                    raise ValueError(f"Email with id {email.parent_id} not found")
            return email.sender

        resolved_recipients = list(recipients) if recipients else list(fallback_recipients)
        if not resolved_recipients:
            resolved_recipients = [get_default_recipient(replying_to_email)]

        resolved_subject = subject or fallback_subject or f"Re: {replying_to_email.subject}"
        resolved_cc = list(cc) if cc else []

        email = Email(
            email_id=uuid_hex(self.rng),
            sender=self.user_email,
            recipients=resolved_recipients,
            subject=resolved_subject,
            content=content,
            timestamp=self.time_manager.time(),
            cc=resolved_cc,
            parent_id=replying_to_email.email_id,
        )

        for path in attachment_paths:
            self.add_attachment(email=email, attachment_path=path)

        with disable_events():
            self.add_email(email=email, folder_name=EmailFolderName.SENT)

        return email.email_id

    @env_tool()
    @event_registered(operation_type=OperationType.WRITE, event_type=EventType.ENV)
    def send_email_to_user_with_id(
        self,
        email_id: str,
        sender: str,
        subject: str = "",
        content: str = "",
        attachment_paths: list[str] | None = None,
    ) -> str:
        """Create an incoming email with a specified ID (optionally with attachments) and add it to user's INBOX.

        This is a PARE-specific environment tool that allows scenarios to reference
        the email_id in subsequent events (e.g., for replying to the email).

        Args:
            email_id: The ID to assign to this email.
            sender: The sender of the email.
            subject: The subject of the email.
            content: The content of the email.
            attachment_paths: Optional list of attachment paths to add to the email.
                NOTE: Attachments are read from `self.internal_fs` (SandboxLocalFileSystem / VirtualFileSystem)
                and stored as base64 bytes on the email. This is safe to use in scenario event flows (post-init),
                but DO NOT add such emails during PAREScenario init state serialization.

        Returns:
            The email_id that was provided.
        """
        if attachment_paths is None:
            attachment_paths = []

        email = Email(
            email_id=email_id,
            sender=sender,
            recipients=[self.user_email],
            subject=subject,
            content=content,
            timestamp=self.time_manager.time(),
            is_read=False,
        )
        for path in attachment_paths:
            self.add_attachment(email=email, attachment_path=path)

        self.folders[EmailFolderName.INBOX].add_email(email)
        return email_id

    def fetch_email_record(self, email_id: str, folder_name: str = EmailFolderName.INBOX.value) -> Email:
        """Return the underlying :class:`Email` object (for navigation / compose, not for LLM tool output)."""
        return EmailClientV2.get_email_by_id(self, email_id, folder_name)

    def fetch_email_by_index(self, idx: int, folder_name: str = EmailFolderName.INBOX.value) -> Email:
        """Return the underlying :class:`Email` by list index (for navigation, not for LLM tool output)."""
        return EmailClientV2.get_email_by_index(self, idx, folder_name)

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_email_by_id(self, email_id: str, folder_name: str = EmailFolderName.INBOX.value) -> MMObservation:
        """Read an email and expose image attachments to multimodal agents."""
        email = EmailClientV2.get_email_by_id(self, email_id, folder_name)
        return self._email_to_mm_observation(email)

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_email_by_index(self, idx: int, folder_name: str = EmailFolderName.INBOX.value) -> MMObservation:
        """Read an email by index and expose image attachments to multimodal agents."""
        email = EmailClientV2.get_email_by_index(self, idx, folder_name)
        return self._email_to_mm_observation(email)

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def download_attachments(
        self,
        email_id: str,
        folder_name: str = EmailFolderName.INBOX.value,
        path_to_save: str = "Downloads/",
    ) -> list[str]:
        """Download attachments, normalizing ``path_to_save`` to a directory when it looks like a file path."""
        target_dir = self._normalize_download_directory(path_to_save)
        if self.internal_fs is not None:
            self.internal_fs.makedirs(target_dir, exist_ok=True)
        return EmailClientV2.download_attachments(self, email_id, folder_name, target_dir)

    def create_root_state(self) -> MailboxView:
        """Return the mailbox view rooted in the inbox."""
        return MailboxView(folder=EmailFolderName.INBOX.value)
