"""Private token and peer-credential file handling."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

from .models import ModelValidationError, validate_alias, validate_uuid


class ConfigError(RuntimeError):
    pass


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base).expanduser() if base else Path.home() / ".config"


def state_home() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base).expanduser() if base else Path.home() / ".local" / "state"


def default_config_dir() -> Path:
    return config_home() / "agent-courier"


def default_state_dir() -> Path:
    return state_home() / "agent-courier"


def _check_private_file(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ConfigError(f"refusing symlink credential file: {path}")
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise ConfigError(f"credential file does not exist: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"credential path is not a regular file: {path}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ConfigError(f"credential file is not owned by the current user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigError(f"credential file must have mode 0600: {path}")
    return info


def read_secret_file(path: str | Path) -> str:
    source = Path(path).expanduser()
    info = _check_private_file(source)
    if info.st_size < 20 or info.st_size > 4096:
        raise ConfigError(f"credential file has an invalid size: {source}")
    token = source.read_text(encoding="utf-8").strip()
    if len(token) < 20 or len(token) > 512 or any(ch.isspace() for ch in token):
        raise ConfigError(f"credential file contains an invalid token: {source}")
    return token


def _exclusive_private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ConfigError(f"refusing to overwrite existing credential file: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def create_operator_token(path: str | Path) -> Path:
    destination = Path(path).expanduser()
    _exclusive_private_write(destination, secrets.token_urlsafe(48) + "\n")
    return destination


@dataclass(frozen=True, slots=True)
class PeerCredentials:
    broker_url: str
    peer_id: str
    alias: str
    peer_token: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError(
                f"unsupported credential schema_version {self.schema_version}"
            )
        broker = self.broker_url.rstrip("/")
        if not broker.startswith(("http://", "https://")):
            raise ConfigError("broker_url must begin with http:// or https://")
        if len(broker) > 2048:
            raise ConfigError("broker_url is too long")
        object.__setattr__(self, "broker_url", broker)
        try:
            object.__setattr__(
                self, "peer_id", validate_uuid(self.peer_id, "peer_id")
            )
            object.__setattr__(self, "alias", validate_alias(self.alias))
        except ModelValidationError as exc:
            raise ConfigError(str(exc)) from exc
        if (
            not isinstance(self.peer_token, str)
            or len(self.peer_token) < 20
            or len(self.peer_token) > 512
            or any(ch.isspace() for ch in self.peer_token)
        ):
            raise ConfigError("peer_token is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "broker_url": self.broker_url,
            "peer_id": self.peer_id,
            "alias": self.alias,
            "peer_token": self.peer_token,
        }


def write_peer_credentials(
    path: str | Path, credentials: PeerCredentials
) -> Path:
    destination = Path(path).expanduser()
    payload = json.dumps(
        credentials.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    _exclusive_private_write(destination, payload + "\n")
    return destination


def read_peer_credentials(path: str | Path) -> PeerCredentials:
    source = Path(path).expanduser()
    info = _check_private_file(source)
    if info.st_size < 20 or info.st_size > 16 * 1024:
        raise ConfigError(f"credential file has an invalid size: {source}")
    try:
        decoded = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"credential file is not valid JSON: {source}") from exc
    if not isinstance(decoded, dict):
        raise ConfigError(f"credential file must contain a JSON object: {source}")
    return PeerCredentials(
        schema_version=decoded.get("schema_version", 1),
        broker_url=decoded.get("broker_url", ""),
        peer_id=decoded.get("peer_id", ""),
        alias=decoded.get("alias", ""),
        peer_token=decoded.get("peer_token", ""),
    )
