"""Authenticated JSON clients for Courier's operator and peer APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .config import PeerCredentials
from .models import Message, Peer, format_timestamp, parse_timestamp, utc_now
from .store import LeasedMessage


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _JSONClient:
    def __init__(self, broker_url: str, token: str, *, timeout: float = 10) -> None:
        self.broker_url = broker_url.rstrip("/")
        if not self.broker_url.startswith(("http://", "https://")):
            raise ValueError("broker URL must begin with http:// or https://")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "agent-courier/0.1",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.broker_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
                decoded = json.loads(raw) if raw else {}
                return response.status, decoded
        except HTTPError as exc:
            try:
                decoded_error = json.loads(exc.read())
                message = decoded_error.get("error", str(exc))
            except (json.JSONDecodeError, AttributeError):
                message = str(exc)
            raise ApiError(message, status=exc.code) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise ApiError(f"Courier hub unavailable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError("Courier hub returned invalid JSON") from exc


class OperatorClient(_JSONClient):
    def enroll(
        self, *, alias: str, runtime: str = "", description: str = ""
    ) -> PeerCredentials:
        status, response = self._request(
            "POST",
            "/v1/peers/enroll",
            {
                "alias": alias,
                "runtime": runtime,
                "description": description,
            },
        )
        if status != 201:
            raise ApiError(f"unexpected enrollment response: HTTP {status}", status=status)
        peer = response["peer"]
        return PeerCredentials(
            broker_url=self.broker_url,
            peer_id=peer["peer_id"],
            alias=peer["alias"],
            peer_token=response["peer_token"],
        )


class CourierClient(_JSONClient):
    def __init__(
        self,
        broker_url: str,
        token: str,
        *,
        peer_id: str,
        alias: str,
        timeout: float = 10,
    ) -> None:
        super().__init__(broker_url, token, timeout=timeout)
        self.peer_id = peer_id
        self.alias = alias

    @classmethod
    def from_credentials(
        cls, credentials: PeerCredentials, *, timeout: float = 10
    ) -> "CourierClient":
        return cls(
            credentials.broker_url,
            credentials.peer_token,
            peer_id=credentials.peer_id,
            alias=credentials.alias,
            timeout=timeout,
        )

    def list_peers(self) -> list[dict[str, Any]]:
        status, response = self._request("GET", "/v1/peers")
        if status != 200:
            raise ApiError(f"unexpected peers response: HTTP {status}", status=status)
        peers = response.get("peers")
        if not isinstance(peers, list):
            raise ApiError("Courier hub returned an invalid peer directory")
        return peers

    def send_message(
        self,
        *,
        recipient: str,
        body: str,
        reply_to: str = "",
        priority: str = "normal",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        message_id: str | None = None,
        sent_at: datetime | None = None,
    ) -> tuple[Message, bool]:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise ValueError("ttl_seconds must be an integer")
        if ttl_seconds < 1 or ttl_seconds > 30 * 24 * 60 * 60:
            raise ValueError("ttl_seconds must be between 1 and 2592000")
        created = (sent_at or utc_now()).astimezone(timezone.utc)
        payload = {
            "message_id": message_id or str(uuid4()),
            "to": recipient,
            "body": body,
            "sent_at": format_timestamp(created),
            "expires_at": format_timestamp(
                created + timedelta(seconds=ttl_seconds)
            ),
            "reply_to": reply_to,
            "priority": priority,
        }
        status, response = self._request("POST", "/v1/messages", payload)
        if status not in {200, 201}:
            raise ApiError(f"unexpected send response: HTTP {status}", status=status)
        return Message.from_dict(response["message"]), bool(response["accepted"])

    def next_message(
        self, *, wait_seconds: int = 20, lease_seconds: int = 60
    ) -> LeasedMessage | None:
        query = urlencode(
            {
                "wait_seconds": wait_seconds,
                "lease_seconds": lease_seconds,
            }
        )
        status, response = self._request(
            "GET",
            f"/v1/messages/next?{query}",
            timeout=max(self.timeout, wait_seconds + 5),
        )
        if status == 204 or not response:
            return None
        if status != 200:
            raise ApiError(f"unexpected claim response: HTTP {status}", status=status)
        return LeasedMessage(
            message=Message.from_dict(response["message"]),
            lease_id=response["lease_id"],
            lease_until=response["lease_until"],
            attempt=int(response["attempt"]),
        )

    def acknowledge(self, delivery: LeasedMessage) -> None:
        status, _ = self._request(
            "POST",
            "/v1/messages/ack",
            {
                "message_id": delivery.message.message_id,
                "lease_id": delivery.lease_id,
            },
        )
        if status != 200:
            raise ApiError(f"unexpected ack response: HTTP {status}", status=status)

    def acknowledge_ids(self, *, message_id: str, lease_id: str) -> None:
        status, _ = self._request(
            "POST",
            "/v1/messages/ack",
            {"message_id": message_id, "lease_id": lease_id},
        )
        if status != 200:
            raise ApiError(f"unexpected ack response: HTTP {status}", status=status)

    def heartbeat(self) -> Peer:
        status, response = self._request("POST", "/v1/heartbeat", {})
        if status != 200:
            raise ApiError(
                f"unexpected heartbeat response: HTTP {status}", status=status
            )
        peer = response["peer"]
        return Peer(
            peer_id=peer["peer_id"],
            alias=peer["alias"],
            runtime=peer.get("runtime", ""),
            description=peer.get("description", ""),
            registered_at=parse_timestamp(peer["registered_at"], "registered_at"),
            last_seen_at=parse_timestamp(peer["last_seen_at"], "last_seen_at"),
        )

    def status(self) -> dict[str, Any]:
        status, response = self._request("GET", "/v1/status")
        if status != 200:
            raise ApiError(f"unexpected status response: HTTP {status}", status=status)
        return response
