"""Versioned, bounded public models for peers and messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Mapping
from uuid import UUID


SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 32 * 1024
MAX_BODY_CHARACTERS = 8_000
MAX_MESSAGE_LIFETIME = timedelta(days=30)
ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


class ModelValidationError(ValueError):
    """Raised when public data violates the wire contract."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ModelValidationError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ModelValidationError(f"{field_name} must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelValidationError(f"{field_name} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelValidationError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def bounded_string(
    value: object,
    field_name: str,
    maximum: int,
    *,
    required: bool = False,
) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ModelValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ModelValidationError(f"{field_name} must not be empty")
    if len(normalized) > maximum:
        raise ModelValidationError(
            f"{field_name} must not exceed {maximum} characters"
        )
    if "\x00" in normalized:
        raise ModelValidationError(f"{field_name} must not contain NUL")
    return normalized


def validate_uuid(value: object, field_name: str) -> str:
    normalized = bounded_string(value, field_name, 64, required=True)
    try:
        parsed = UUID(normalized)
    except ValueError as exc:
        raise ModelValidationError(f"{field_name} must be a UUID") from exc
    return str(parsed)


def validate_alias(value: object) -> str:
    alias = bounded_string(value, "alias", 32, required=True)
    if not ALIAS_PATTERN.fullmatch(alias):
        raise ModelValidationError(
            "alias must start with a letter and contain only letters, digits, "
            "underscores, or hyphens"
        )
    return alias


@dataclass(frozen=True, slots=True)
class Peer:
    peer_id: str
    alias: str
    runtime: str
    description: str
    registered_at: datetime
    last_seen_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "peer_id", validate_uuid(self.peer_id, "peer_id"))
        object.__setattr__(self, "alias", validate_alias(self.alias))
        object.__setattr__(
            self, "runtime", bounded_string(self.runtime, "runtime", 64)
        )
        object.__setattr__(
            self,
            "description",
            bounded_string(self.description, "description", 300),
        )
        if self.registered_at.tzinfo is None or self.last_seen_at.tzinfo is None:
            raise ModelValidationError("peer timestamps must include a timezone")
        object.__setattr__(
            self, "registered_at", self.registered_at.astimezone(timezone.utc)
        )
        object.__setattr__(
            self, "last_seen_at", self.last_seen_at.astimezone(timezone.utc)
        )

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = (now or utc_now()).astimezone(timezone.utc)
        age = max(0.0, (current - self.last_seen_at).total_seconds())
        return {
            "peer_id": self.peer_id,
            "alias": self.alias,
            "runtime": self.runtime,
            "description": self.description,
            "registered_at": format_timestamp(self.registered_at),
            "last_seen_at": format_timestamp(self.last_seen_at),
            "presence": "recent" if age <= 300 else "offline",
        }


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    sender_id: str
    sender_alias: str
    recipient_id: str
    recipient_alias: str
    body: str
    sent_at: datetime
    expires_at: datetime
    reply_to: str = ""
    priority: str = "normal"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported schema_version {self.schema_version}"
            )
        object.__setattr__(
            self,
            "message_id",
            validate_uuid(self.message_id, "message_id"),
        )
        object.__setattr__(
            self, "sender_id", validate_uuid(self.sender_id, "sender_id")
        )
        object.__setattr__(
            self, "recipient_id", validate_uuid(self.recipient_id, "recipient_id")
        )
        object.__setattr__(
            self, "sender_alias", validate_alias(self.sender_alias)
        )
        object.__setattr__(
            self, "recipient_alias", validate_alias(self.recipient_alias)
        )
        object.__setattr__(
            self,
            "body",
            bounded_string(
                self.body, "body", MAX_BODY_CHARACTERS, required=True
            ),
        )
        reply_to = bounded_string(self.reply_to, "reply_to", 64)
        if reply_to:
            reply_to = validate_uuid(reply_to, "reply_to")
        object.__setattr__(self, "reply_to", reply_to)
        priority = bounded_string(
            self.priority, "priority", 16, required=True
        ).lower()
        if priority not in {"low", "normal", "high"}:
            raise ModelValidationError("priority must be low, normal, or high")
        object.__setattr__(self, "priority", priority)

        if self.sent_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ModelValidationError("message timestamps must include a timezone")
        sent = self.sent_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        if expires <= sent:
            raise ModelValidationError("expires_at must be later than sent_at")
        if expires - sent > MAX_MESSAGE_LIFETIME:
            raise ModelValidationError("message lifetime must not exceed 30 days")
        object.__setattr__(self, "sent_at", sent)
        object.__setattr__(self, "expires_at", expires)

        if len(self.to_json().encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ModelValidationError(
                f"encoded message must not exceed {MAX_REQUEST_BYTES} bytes"
            )

    @classmethod
    def from_dict(cls, value: object) -> "Message":
        if not isinstance(value, Mapping):
            raise ModelValidationError("message must be a JSON object")
        version = value.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ModelValidationError("schema_version must be an integer")
        try:
            return cls(
                schema_version=version,
                message_id=value.get("message_id", ""),  # type: ignore[arg-type]
                sender_id=value.get("sender_id", ""),  # type: ignore[arg-type]
                sender_alias=value.get("sender_alias", ""),  # type: ignore[arg-type]
                recipient_id=value.get("recipient_id", ""),  # type: ignore[arg-type]
                recipient_alias=value.get("recipient_alias", ""),  # type: ignore[arg-type]
                body=value.get("body", ""),  # type: ignore[arg-type]
                sent_at=parse_timestamp(value.get("sent_at"), "sent_at"),
                expires_at=parse_timestamp(value.get("expires_at"), "expires_at"),
                reply_to=value.get("reply_to", ""),  # type: ignore[arg-type]
                priority=value.get("priority", "normal"),  # type: ignore[arg-type]
            )
        except TypeError as exc:
            raise ModelValidationError("message contains an invalid value") from exc

    @classmethod
    def from_json(cls, value: str | bytes) -> "Message":
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelValidationError("message is not valid JSON") from exc
        return cls.from_dict(decoded)

    def stable_dict(self) -> dict[str, Any]:
        """Fields used for idempotency; aliases are informational snapshots."""
        return {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "body": self.body,
            "sent_at": format_timestamp(self.sent_at),
            "expires_at": format_timestamp(self.expires_at),
            "reply_to": self.reply_to,
            "priority": self.priority,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.stable_dict(),
            "sender_alias": self.sender_alias,
            "recipient_alias": self.recipient_alias,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
