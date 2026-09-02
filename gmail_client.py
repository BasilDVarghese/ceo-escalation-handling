"""Gmail API integration: OAuth, fetching candidate escalation emails, and sending."""

from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import CONFIG

# gmail.modify covers reading/labeling; gmail.send is needed separately for dispatch.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Retry only genuinely transient failures — rate limiting and server-side errors — not 4xx
# client errors like a bad request or missing permission, which retrying can't fix.
_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}


def _is_transient_gmail_error(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in _TRANSIENT_HTTP_STATUSES
    return isinstance(exc, (ConnectionError, TimeoutError))


_retry_gmail_call = retry(
    retry=retry_if_exception(_is_transient_gmail_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


_service: Resource | None = None


def get_gmail_service(force_refresh: bool = False) -> Resource:
    """Return a cached, authenticated Gmail service, building/refreshing it as needed.

    Cached at module level so repeated calls (e.g. from separate graph nodes within
    the same process) don't re-read token.json or rebuild the API client each time.
    """
    global _service
    if _service is not None and not force_refresh:
        return _service

    creds: Credentials | None = None
    token_path = CONFIG.gmail_token_path

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CONFIG.gmail_credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    _service = build("gmail", "v1", credentials=creds)
    return _service


def _decode_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload for the first text/plain part and decode it."""

    def _b64decode(data: str) -> str:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")

    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _b64decode(payload["body"]["data"])

    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text

    # Fall back to whatever body data is present (e.g. a top-level html-only message).
    if payload.get("body", {}).get("data"):
        return _b64decode(payload["body"]["data"])

    return ""


@_retry_gmail_call
def fetch_new_escalation_emails(service: Resource) -> list[dict[str, Any]]:
    """List + fetch messages matching CONFIG.gmail_query, decoded into plain dicts."""
    results = service.users().messages().list(userId="me", q=CONFIG.gmail_query).execute()
    message_stubs = results.get("messages", [])

    emails: list[dict[str, Any]] = []
    for stub in message_stubs:
        message = (
            service.users().messages().get(userId="me", id=stub["id"], format="full").execute()
        )
        headers = {h["name"]: h["value"] for h in message["payload"].get("headers", [])}
        emails.append(
            {
                "gmail_message_id": message["id"],
                "gmail_thread_id": message.get("threadId", ""),
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "raw_body": _decode_body(message["payload"]),
                "received_at": headers.get("Date", ""),
            }
        )
    return emails


def _get_or_create_label_id(service: Resource, label_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(userId="me", body={"name": label_name, "labelListVisibility": "labelShow"})
        .execute()
    )
    return created["id"]


@_retry_gmail_call
def mark_processed(service: Resource, message_id: str) -> None:
    """Remove UNREAD, add the processed label, so the poller doesn't refetch it."""
    processed_label_id = _get_or_create_label_id(service, CONFIG.gmail_processed_label)
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"], "addLabelIds": [processed_label_id]},
    ).execute()


@_retry_gmail_call  # note: a timeout after the send actually succeeded server-side could in
# principle cause a duplicate send on retry — the Gmail API has no send idempotency key. An
# accepted trade-off at this tool's internal, human-approved-before-send scale.
def send_email(service: Resource, to: str, subject: str, body: str, thread_id: str | None = None) -> str:
    message = MIMEText(body)
    message["to"] = to
    message["from"] = CONFIG.sender_email
    message["subject"] = subject
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    send_body: dict[str, Any] = {"raw": encoded}
    if thread_id:
        send_body["threadId"] = thread_id

    sent = service.users().messages().send(userId="me", body=send_body).execute()
    return sent["id"]
