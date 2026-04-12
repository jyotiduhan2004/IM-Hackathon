"""Gmail API client for mailing list email fetching."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = structlog.get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass
class MessageStub:
    """Lightweight message reference from list operations."""

    id: str
    thread_id: str


@dataclass
class RawMessage:
    """Full Gmail API message payload."""

    id: str
    thread_id: str
    label_ids: list[str]
    headers: dict[str, str]
    body_plain: str
    body_html: str
    attachments: list[AttachmentRef]
    internal_date_ms: int

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.internal_date_ms / 1000)


@dataclass
class AttachmentRef:
    """Reference to an attachment, pre-download."""

    attachment_id: str
    filename: str
    mime_type: str
    size: int
    content_id: str | None = None  # for inline images

    @property
    def is_inline(self) -> bool:
        return bool(self.content_id)

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")


class GmailClient:
    """Gmail API wrapper for mailing list email fetching."""

    def __init__(
        self,
        credentials_path: str | Path = "credentials.json",
        token_path: str | Path = "token.json",
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self._service: Any | None = None

    def authenticate(self) -> None:
        """Run OAuth flow. Opens browser on first run, refreshes token after."""
        creds: Credentials | None = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("refreshing expired gmail token")
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail OAuth credentials not found at {self.credentials_path}. "
                        "Download OAuth 2.0 Client ID JSON from Google Cloud Console."
                    )
                logger.info("running oauth flow", credentials=str(self.credentials_path))
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            self.token_path.write_text(creds.to_json())
            logger.info("saved gmail token", path=str(self.token_path))

        self._service = build("gmail", "v1", credentials=creds)

    @property
    def service(self) -> Any:
        if self._service is None:
            self.authenticate()
        return self._service

    def list_messages(
        self,
        list_address: str = "",
        after: date | datetime | None = None,
        before: date | datetime | None = None,
        query: str = "",
        max_results: int = 500,
    ) -> list[MessageStub]:
        """List message IDs matching the filter.

        Args:
            list_address: Mailing list address to filter by (e.g., "list@company.com")
            after: Fetch emails sent after this date
            before: Fetch emails sent before this date
            query: Additional Gmail search query to append
            max_results: Maximum messages to return (paginates automatically)

        Returns:
            List of MessageStub with id and thread_id
        """
        query_parts: list[str] = []
        if list_address:
            query_parts.append(f"list:{list_address}")
        if after:
            query_parts.append(f"after:{after.strftime('%Y/%m/%d')}")
        if before:
            query_parts.append(f"before:{before.strftime('%Y/%m/%d')}")
        if query:
            query_parts.append(query)

        q = " ".join(query_parts)
        logger.info("listing messages", query=q, max_results=max_results)

        stubs: list[MessageStub] = []
        page_token: str | None = None

        while len(stubs) < max_results:
            batch_size = min(500, max_results - len(stubs))
            response = (
                self.service.users()
                .messages()
                .list(userId="me", q=q, pageToken=page_token, maxResults=batch_size)
                .execute()
            )

            messages = response.get("messages", [])
            for msg in messages:
                stubs.append(MessageStub(id=msg["id"], thread_id=msg["threadId"]))

            page_token = response.get("nextPageToken")
            if not page_token or not messages:
                break

        logger.info("listed messages", count=len(stubs))
        return stubs

    def get_message(self, message_id: str) -> RawMessage:
        """Fetch full message content including body and attachment metadata."""
        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as e:
            logger.error("failed to fetch message", id=message_id, error=str(e))
            raise

        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

        body_plain, body_html, attachments = self._walk_parts(payload)

        return RawMessage(
            id=msg["id"],
            thread_id=msg["threadId"],
            label_ids=msg.get("labelIds", []),
            headers=headers,
            body_plain=body_plain,
            body_html=body_html,
            attachments=attachments,
            internal_date_ms=int(msg.get("internalDate", "0")),
        )

    def _walk_parts(
        self, payload: dict[str, Any]
    ) -> tuple[str, str, list[AttachmentRef]]:
        """Recursively walk MIME parts to extract body and attachments."""
        body_plain = ""
        body_html = ""
        attachments: list[AttachmentRef] = []

        def walk(part: dict[str, Any]) -> None:
            nonlocal body_plain, body_html

            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})
            part_headers = {h["name"]: h["value"] for h in part.get("headers", [])}

            if part.get("parts"):
                for sub in part["parts"]:
                    walk(sub)
                return

            # Body content
            if mime_type == "text/plain" and not filename:
                data = body.get("data", "")
                if data:
                    body_plain = base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
                return

            if mime_type == "text/html" and not filename:
                data = body.get("data", "")
                if data:
                    body_html = base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
                return

            # Attachment
            if body.get("attachmentId"):
                content_id = part_headers.get("Content-ID", "").strip("<>")
                attachments.append(
                    AttachmentRef(
                        attachment_id=body["attachmentId"],
                        filename=filename or f"attachment-{body['attachmentId'][:8]}",
                        mime_type=mime_type,
                        size=int(body.get("size", 0)),
                        content_id=content_id or None,
                    )
                )

        walk(payload)
        return body_plain, body_html, attachments

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download a specific attachment's binary content."""
        att = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(att["data"])
