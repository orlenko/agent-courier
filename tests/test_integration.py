from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_courier.api import ApiError, CourierClient, OperatorClient  # noqa: E402
from agent_courier.server import make_server  # noqa: E402
from agent_courier.store import CourierStore  # noqa: E402


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.store = CourierStore(Path(self.temp.name) / "courier.db")
        self.store.initialize()
        self.operator_token = "operator-test-token-" + ("x" * 32)
        self.server = make_server(
            "127.0.0.1", 0, self.store, self.operator_token
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        operator = OperatorClient(self.url, self.operator_token, timeout=2)
        self.alice_credentials = operator.enroll(alias="alice", runtime="Codex")
        self.bob_credentials = operator.enroll(alias="bob", runtime="Claude")
        self.alice = CourierClient.from_credentials(
            self.alice_credentials, timeout=2
        )
        self.bob = CourierClient.from_credentials(self.bob_credentials, timeout=2)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_cross_peer_delivery_and_ack(self) -> None:
        peers = self.alice.list_peers()
        self.assertEqual({peer["alias"] for peer in peers}, {"alice", "bob"})

        sent, accepted = self.alice.send_message(
            recipient="bob", body="Synthetic integration note."
        )
        self.assertTrue(accepted)
        self.assertEqual(sent.sender_id, self.alice_credentials.peer_id)
        self.assertEqual(sent.recipient_id, self.bob_credentials.peer_id)

        self.assertIsNone(self.alice.next_message(wait_seconds=0))
        delivery = self.bob.next_message(wait_seconds=0)
        self.assertIsNotNone(delivery)
        assert delivery is not None
        self.assertEqual(delivery.message.message_id, sent.message_id)

        with self.assertRaises(ApiError) as wrong_recipient:
            self.alice.acknowledge(delivery)
        self.assertEqual(wrong_recipient.exception.status, 409)

        self.bob.acknowledge(delivery)
        self.assertIsNone(self.bob.next_message(wait_seconds=0))
        self.assertEqual(self.bob.status()["messages"]["acknowledged"], 1)

    def test_bad_peer_token_is_rejected(self) -> None:
        invalid = CourierClient(
            self.url,
            "invalid-peer-token-" + ("x" * 32),
            peer_id=self.alice_credentials.peer_id,
            alias="alice",
            timeout=2,
        )
        with self.assertRaises(ApiError) as caught:
            invalid.list_peers()
        self.assertEqual(caught.exception.status, 401)

    def test_operator_token_cannot_act_as_peer(self) -> None:
        invalid = CourierClient(
            self.url,
            self.operator_token,
            peer_id=self.alice_credentials.peer_id,
            alias="alice",
            timeout=2,
        )
        with self.assertRaises(ApiError) as caught:
            invalid.status()
        self.assertEqual(caught.exception.status, 401)
