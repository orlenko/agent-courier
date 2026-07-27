from datetime import timedelta
from pathlib import Path
import sys
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_courier.models import (  # noqa: E402
    MAX_BODY_CHARACTERS,
    Message,
    ModelValidationError,
    Peer,
    utc_now,
)


class PeerTests(unittest.TestCase):
    def test_peer_round_trip_shape_has_presence(self) -> None:
        now = utc_now()
        peer = Peer(
            peer_id=str(uuid4()),
            alias="backend-codex",
            runtime="Codex",
            description="Reviews API changes",
            registered_at=now,
            last_seen_at=now,
        )
        self.assertEqual(peer.to_dict(now=now)["presence"], "recent")

    def test_alias_is_deliberately_constrained(self) -> None:
        now = utc_now()
        with self.assertRaisesRegex(ModelValidationError, "alias must start"):
            Peer(
                peer_id=str(uuid4()),
                alias="../private",
                runtime="",
                description="",
                registered_at=now,
                last_seen_at=now,
            )


class MessageTests(unittest.TestCase):
    def make_message(self, **changes) -> Message:
        now = utc_now()
        values = {
            "message_id": str(uuid4()),
            "sender_id": str(uuid4()),
            "sender_alias": "frontend",
            "recipient_id": str(uuid4()),
            "recipient_alias": "backend",
            "body": "Please check the schema.",
            "sent_at": now,
            "expires_at": now + timedelta(days=1),
        }
        values.update(changes)
        return Message(**values)

    def test_round_trip_preserves_unicode(self) -> None:
        message = self.make_message(body="Проверь схему, пожалуйста.")
        self.assertEqual(Message.from_json(message.to_json()), message)

    def test_body_is_bounded(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "body must not exceed"):
            self.make_message(body="x" * (MAX_BODY_CHARACTERS + 1))

    def test_lifetime_is_bounded(self) -> None:
        now = utc_now()
        with self.assertRaisesRegex(ModelValidationError, "30 days"):
            self.make_message(sent_at=now, expires_at=now + timedelta(days=31))

    def test_reply_id_must_be_uuid(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "reply_to must be a UUID"):
            self.make_message(reply_to="message-123")
