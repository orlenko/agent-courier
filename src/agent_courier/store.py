"""SQLite-backed peer directory and durable message delivery."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any
from uuid import uuid4

from .models import (
    Message,
    ModelValidationError,
    Peer,
    bounded_string,
    format_timestamp,
    parse_timestamp,
    utc_now,
    validate_uuid,
)


class StoreError(RuntimeError):
    pass


class AuthenticationFailed(StoreError):
    pass


class AliasConflict(StoreError):
    pass


class UnknownPeer(StoreError):
    pass


class MessageConflict(StoreError):
    pass


class ReplyConflict(StoreError):
    pass


class LeaseConflict(StoreError):
    pass


class StoreFull(StoreError):
    pass


@dataclass(frozen=True, slots=True)
class Enrollment:
    peer: Peer
    peer_token: str


@dataclass(frozen=True, slots=True)
class LeasedMessage:
    message: Message
    lease_id: str
    lease_until: str
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message.to_dict(),
            "lease_id": self.lease_id,
            "lease_until": self.lease_until,
            "attempt": self.attempt,
        }


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def message_digest(message: Message) -> str:
    payload = json.dumps(
        message.stable_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CourierStore:
    """Owns coordinator durability; every operation uses its own connection."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_peers: int = 1_000,
        max_messages: int = 100_000,
        retention_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.database = Path(database)
        if max_peers < 1 or max_peers > 100_000:
            raise ValueError("max_peers must be between 1 and 100000")
        if max_messages < 1:
            raise ValueError("max_messages must be at least 1")
        if retention_seconds < 0 or retention_seconds > 90 * 24 * 60 * 60:
            raise ValueError("retention_seconds must be between 0 and 7776000")
        self.max_peers = max_peers
        self.max_messages = max_messages
        self.retention_seconds = retention_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS peers (
                    peer_id TEXT PRIMARY KEY,
                    alias TEXT NOT NULL,
                    alias_key TEXT NOT NULL UNIQUE,
                    runtime TEXT NOT NULL,
                    description TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    registered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    retired_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    sender_id TEXT NOT NULL REFERENCES peers(peer_id),
                    recipient_id TEXT NOT NULL REFERENCES peers(peer_id),
                    sent_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'leased', 'acknowledged', 'expired')
                    ),
                    received_at TEXT NOT NULL,
                    lease_id TEXT,
                    lease_until TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    acknowledged_at TEXT
                );

                CREATE INDEX IF NOT EXISTS messages_ready_idx
                    ON messages(recipient_id, status, sent_at);
                CREATE INDEX IF NOT EXISTS messages_expiry_idx
                    ON messages(expires_at);
                CREATE INDEX IF NOT EXISTS messages_sender_idx
                    ON messages(sender_id, sent_at);
                PRAGMA user_version = 1;
                """
            )
            connection.commit()

    @staticmethod
    def _peer_from_row(row: sqlite3.Row) -> Peer:
        return Peer(
            peer_id=row["peer_id"],
            alias=row["alias"],
            runtime=row["runtime"],
            description=row["description"],
            registered_at=parse_timestamp(row["registered_at"], "registered_at"),
            last_seen_at=parse_timestamp(row["last_seen_at"], "last_seen_at"),
        )

    def register_peer(
        self, *, alias: str, runtime: str = "", description: str = ""
    ) -> Enrollment:
        now = utc_now()
        peer = Peer(
            peer_id=str(uuid4()),
            alias=alias,
            runtime=runtime,
            description=description,
            registered_at=now,
            last_seen_at=now,
        )
        token = secrets.token_urlsafe(32)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM peers WHERE retired_at IS NULL"
                    ).fetchone()[0]
                )
                if count >= self.max_peers:
                    raise StoreFull(f"peer limit reached ({self.max_peers})")
                connection.execute(
                    """
                    INSERT INTO peers (
                        peer_id, alias, alias_key, runtime, description,
                        token_hash, registered_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        peer.peer_id,
                        peer.alias,
                        peer.alias.casefold(),
                        peer.runtime,
                        peer.description,
                        token_digest(token),
                        format_timestamp(peer.registered_at),
                        format_timestamp(peer.last_seen_at),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                if "alias_key" in str(exc):
                    raise AliasConflict(f'alias "{peer.alias}" is already registered') from exc
                raise
            except BaseException:
                connection.rollback()
                raise
        return Enrollment(peer=peer, peer_token=token)

    def authenticate(self, token: str, *, touch: bool = True) -> Peer:
        if not isinstance(token, str) or len(token) < 20 or len(token) > 512:
            raise AuthenticationFailed("invalid peer credential")
        digest = token_digest(token)
        with closing(self._connect()) as connection:
            try:
                if touch:
                    connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM peers
                    WHERE token_hash = ? AND retired_at IS NULL
                    """,
                    (digest,),
                ).fetchone()
                if row is None:
                    if touch:
                        connection.rollback()
                    raise AuthenticationFailed("invalid peer credential")
                if touch:
                    now = format_timestamp(utc_now())
                    connection.execute(
                        "UPDATE peers SET last_seen_at = ? WHERE peer_id = ?",
                        (now, row["peer_id"]),
                    )
                    connection.commit()
                    row = connection.execute(
                        "SELECT * FROM peers WHERE peer_id = ?", (row["peer_id"],)
                    ).fetchone()
                return self._peer_from_row(row)
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def list_peers(self) -> list[Peer]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM peers
                WHERE retired_at IS NULL
                ORDER BY alias_key, peer_id
                """
            ).fetchall()
        return [self._peer_from_row(row) for row in rows]

    @staticmethod
    def _active_peer(
        connection: sqlite3.Connection, peer_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM peers WHERE peer_id = ? AND retired_at IS NULL",
            (peer_id,),
        ).fetchone()
        if row is None:
            raise UnknownPeer("peer is not registered")
        return row

    @staticmethod
    def _resolve_recipient(
        connection: sqlite3.Connection, query: str
    ) -> sqlite3.Row:
        normalized = bounded_string(query, "recipient", 64, required=True)
        row = connection.execute(
            """
            SELECT * FROM peers
            WHERE retired_at IS NULL
              AND (peer_id = ? OR alias_key = ?)
            """,
            (normalized, normalized.casefold()),
        ).fetchone()
        if row is None:
            raise UnknownPeer(f'no active peer matches "{normalized}"')
        return row

    def _maintain(self, connection: sqlite3.Connection, now: datetime) -> None:
        now_text = format_timestamp(now)
        connection.execute(
            """
            UPDATE messages
            SET status = 'expired', lease_id = NULL, lease_until = NULL
            WHERE status IN ('pending', 'leased') AND expires_at <= ?
            """,
            (now_text,),
        )
        connection.execute(
            """
            UPDATE messages
            SET status = 'pending', lease_id = NULL, lease_until = NULL
            WHERE status = 'leased'
              AND lease_until <= ?
              AND expires_at > ?
            """,
            (now_text, now_text),
        )
        cutoff = format_timestamp(now - timedelta(seconds=self.retention_seconds))
        connection.execute(
            """
            DELETE FROM messages
            WHERE status IN ('acknowledged', 'expired')
              AND received_at <= ?
            """,
            (cutoff,),
        )

    def send_message(
        self,
        *,
        sender_id: str,
        recipient: str,
        body: str,
        message_id: str,
        sent_at: datetime,
        expires_at: datetime,
        reply_to: str = "",
        priority: str = "normal",
    ) -> tuple[Message, bool]:
        sender_id = validate_uuid(sender_id, "sender_id")
        now = utc_now()
        if sent_at > now + timedelta(minutes=5):
            raise ModelValidationError("sent_at must not be more than five minutes ahead")
        if expires_at <= now:
            raise ModelValidationError("message is already expired")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                sender_row = self._active_peer(connection, sender_id)
                recipient_row = self._resolve_recipient(connection, recipient)
                message = Message(
                    message_id=message_id,
                    sender_id=sender_id,
                    sender_alias=sender_row["alias"],
                    recipient_id=recipient_row["peer_id"],
                    recipient_alias=recipient_row["alias"],
                    body=body,
                    sent_at=sent_at,
                    expires_at=expires_at,
                    reply_to=reply_to,
                    priority=priority,
                )
                digest = message_digest(message)
                existing = connection.execute(
                    """
                    SELECT payload, request_sha256
                    FROM messages WHERE message_id = ?
                    """,
                    (message.message_id,),
                ).fetchone()
                if existing is not None:
                    if existing["request_sha256"] != digest:
                        raise MessageConflict(
                            "message_id already exists with different content"
                        )
                    connection.rollback()
                    return Message.from_json(existing["payload"]), False

                if message.reply_to:
                    prior = connection.execute(
                        """
                        SELECT sender_id, recipient_id
                        FROM messages WHERE message_id = ?
                        """,
                        (message.reply_to,),
                    ).fetchone()
                    if prior is None:
                        raise ReplyConflict("reply_to message does not exist")
                    participants = {prior["sender_id"], prior["recipient_id"]}
                    if {message.sender_id, message.recipient_id} != participants:
                        raise ReplyConflict(
                            "reply_to message belongs to a different conversation"
                        )

                self._maintain(connection, now)
                count = int(
                    connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                )
                if count >= self.max_messages:
                    raise StoreFull(f"message limit reached ({self.max_messages})")
                connection.execute(
                    """
                    INSERT INTO messages (
                        message_id, payload, request_sha256, sender_id,
                        recipient_id, sent_at, expires_at, priority,
                        status, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        message.message_id,
                        message.to_json(),
                        digest,
                        message.sender_id,
                        message.recipient_id,
                        format_timestamp(message.sent_at),
                        format_timestamp(message.expires_at),
                        message.priority,
                        format_timestamp(now),
                    ),
                )
                connection.commit()
                return message, True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def lease_next(
        self, recipient_id: str, *, lease_seconds: int = 60
    ) -> LeasedMessage | None:
        recipient_id = validate_uuid(recipient_id, "recipient_id")
        if lease_seconds < 5 or lease_seconds > 300:
            raise ValueError("lease_seconds must be between 5 and 300")
        now = utc_now()
        now_text = format_timestamp(now)
        lease_until = format_timestamp(now + timedelta(seconds=lease_seconds))
        lease_id = str(uuid4())

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._active_peer(connection, recipient_id)
                self._maintain(connection, now)
                row = connection.execute(
                    """
                    SELECT message_id, payload, attempts
                    FROM messages
                    WHERE recipient_id = ?
                      AND status = 'pending'
                      AND expires_at > ?
                    ORDER BY
                      CASE priority
                        WHEN 'high' THEN 0
                        WHEN 'normal' THEN 1
                        ELSE 2
                      END,
                      sent_at,
                      message_id
                    LIMIT 1
                    """,
                    (recipient_id, now_text),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                changed = connection.execute(
                    """
                    UPDATE messages
                    SET status = 'leased', lease_id = ?, lease_until = ?,
                        attempts = attempts + 1
                    WHERE message_id = ? AND recipient_id = ?
                      AND status = 'pending'
                    """,
                    (lease_id, lease_until, row["message_id"], recipient_id),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    return None
                connection.commit()
                return LeasedMessage(
                    message=Message.from_json(row["payload"]),
                    lease_id=lease_id,
                    lease_until=lease_until,
                    attempt=int(row["attempts"]) + 1,
                )
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def acknowledge(
        self, *, recipient_id: str, message_id: str, lease_id: str
    ) -> None:
        recipient_id = validate_uuid(recipient_id, "recipient_id")
        message_id = validate_uuid(message_id, "message_id")
        lease_id = validate_uuid(lease_id, "lease_id")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT status, lease_id
                    FROM messages
                    WHERE message_id = ? AND recipient_id = ?
                    """,
                    (message_id, recipient_id),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != "leased"
                    or row["lease_id"] != lease_id
                ):
                    raise LeaseConflict("message lease is missing, stale, or invalid")
                connection.execute(
                    """
                    UPDATE messages
                    SET status = 'acknowledged', acknowledged_at = ?,
                        lease_id = NULL, lease_until = NULL
                    WHERE message_id = ? AND recipient_id = ?
                    """,
                    (format_timestamp(utc_now()), message_id, recipient_id),
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def status(self) -> dict[str, Any]:
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._maintain(connection, now)
            connection.commit()
            peer_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM peers WHERE retired_at IS NULL"
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM messages GROUP BY status ORDER BY status
                """
            ).fetchall()
        return {
            "peers": peer_count,
            "messages": {row["status"]: int(row["count"]) for row in rows},
        }

    def message_payload(self, message_id: str) -> dict[str, Any] | None:
        """Body-bearing read helper for tests; routine status never calls it."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None
