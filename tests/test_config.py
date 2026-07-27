import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_courier.config import (  # noqa: E402
    ConfigError,
    PeerCredentials,
    create_operator_token,
    read_peer_credentials,
    read_secret_file,
    write_peer_credentials,
)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_operator_token_is_private_and_not_overwritten(self) -> None:
        path = create_operator_token(self.root / "operator.token")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(read_secret_file(path)), 20)
        with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
            create_operator_token(path)

    def test_peer_credentials_round_trip(self) -> None:
        credentials = PeerCredentials(
            broker_url="http://127.0.0.1:8790/",
            peer_id=str(uuid4()),
            alias="reviewer",
            peer_token="t" * 32,
        )
        path = write_peer_credentials(self.root / "reviewer.peer.json", credentials)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(read_peer_credentials(path), credentials)

    @unittest.skipIf(os.name == "nt", "POSIX mode check")
    def test_permissive_credentials_are_rejected(self) -> None:
        path = self.root / "peer.json"
        path.write_text("x" * 32, encoding="utf-8")
        path.chmod(0o644)
        with self.assertRaisesRegex(ConfigError, "mode 0600"):
            read_secret_file(path)
