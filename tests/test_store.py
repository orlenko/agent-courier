from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_courier.models import format_timestamp, utc_now  # noqa: E402
from agent_courier.store import (  # noqa: E402
    AliasConflict,
    AuthenticationFailed,
    CourierStore,
    LeaseConflict,
    MessageConflict,
    ReplyConflict,
)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "courier.db"
        self.store = CourierStore(self.database)
        self.store.initialize()
        self.alice = self.store.register_peer(alias="alice", runtime="Codex")
        self.bob = self.store.register_peer(alias="bob", runtime="Claude")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def send(self, **changes):
        now = changes.pop("sent_at", utc_now())
        values = {
            "sender_id": self.alice.peer.peer_id,
            "recipient": "bob",
            "body": "Please review the migration.",
            "message_id": str(uuid4()),
            "sent_at": now,
            "expires_at": now + timedelta(days=1),
        }
        values.update(changes)
        return self.store.send_message(**values)

    def test_aliases_are_case_insensitively_unique(self) -> None:
        with self.assertRaises(AliasConflict):
            self.store.register_peer(alias="ALICE")

    def test_peer_authentication_uses_issued_token(self) -> None:
        peer = self.store.authenticate(self.alice.peer_token)
        self.assertEqual(peer.peer_id, self.alice.peer.peer_id)
        with self.assertRaises(AuthenticationFailed):
            self.store.authenticate("wrong-token-" + ("x" * 30))

    def test_send_lease_ack_lifecycle(self) -> None:
        message, accepted = self.send(priority="high")
        self.assertTrue(accepted)
        self.assertEqual(message.sender_alias, "alice")
        delivery = self.store.lease_next(self.bob.peer.peer_id)
        self.assertIsNotNone(delivery)
        assert delivery is not None
        self.assertEqual(delivery.message.message_id, message.message_id)
        with self.assertRaises(LeaseConflict):
            self.store.acknowledge(
                recipient_id=self.alice.peer.peer_id,
                message_id=message.message_id,
                lease_id=delivery.lease_id,
            )
        self.store.acknowledge(
            recipient_id=self.bob.peer.peer_id,
            message_id=message.message_id,
            lease_id=delivery.lease_id,
        )
        self.assertIsNone(self.store.lease_next(self.bob.peer.peer_id))
        self.assertEqual(self.store.status()["messages"]["acknowledged"], 1)

    def test_expired_lease_returns_to_pending(self) -> None:
        message, _ = self.send()
        delivery = self.store.lease_next(self.bob.peer.peer_id, lease_seconds=5)
        self.assertIsNotNone(delivery)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE messages SET lease_until = ? WHERE message_id = ?",
                (
                    format_timestamp(utc_now() - timedelta(seconds=1)),
                    message.message_id,
                ),
            )
        redelivery = self.store.lease_next(self.bob.peer.peer_id)
        self.assertIsNotNone(redelivery)
        assert redelivery is not None
        self.assertEqual(redelivery.message.message_id, message.message_id)
        self.assertEqual(redelivery.attempt, 2)

    def test_retry_with_same_id_and_content_is_idempotent(self) -> None:
        now = utc_now()
        message_id = str(uuid4())
        first, accepted = self.send(message_id=message_id, sent_at=now)
        second, accepted_again = self.send(message_id=message_id, sent_at=now)
        self.assertTrue(accepted)
        self.assertFalse(accepted_again)
        self.assertEqual(first, second)
        with self.assertRaises(MessageConflict):
            self.send(
                message_id=message_id,
                sent_at=now,
                body="Different content",
            )

    def test_reply_must_stay_in_same_conversation(self) -> None:
        original, _ = self.send()
        now = utc_now()
        reply, accepted = self.store.send_message(
            sender_id=self.bob.peer.peer_id,
            recipient="alice",
            body="Reviewed.",
            message_id=str(uuid4()),
            sent_at=now,
            expires_at=now + timedelta(days=1),
            reply_to=original.message_id,
        )
        self.assertTrue(accepted)
        self.assertEqual(reply.reply_to, original.message_id)

        carol = self.store.register_peer(alias="carol")
        with self.assertRaises(ReplyConflict):
            self.store.send_message(
                sender_id=carol.peer.peer_id,
                recipient="alice",
                body="Unrelated.",
                message_id=str(uuid4()),
                sent_at=now,
                expires_at=now + timedelta(days=1),
                reply_to=original.message_id,
            )

    def test_status_does_not_include_message_bodies(self) -> None:
        self.send(body="sensitive synthetic body")
        self.assertNotIn("sensitive", str(self.store.status()))
