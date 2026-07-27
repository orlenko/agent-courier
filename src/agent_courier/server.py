"""Authenticated threaded HTTP hub."""

from __future__ import annotations

import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import ssl
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .models import MAX_REQUEST_BYTES, ModelValidationError, parse_timestamp
from .store import (
    AliasConflict,
    AuthenticationFailed,
    CourierStore,
    LeaseConflict,
    MessageConflict,
    ReplyConflict,
    StoreFull,
    UnknownPeer,
)


LOG = logging.getLogger("agent_courier.server")


class CourierHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        store: CourierStore,
        operator_token: str,
    ) -> None:
        super().__init__(server_address, CourierHandler)
        self.store = store
        self.operator_token = operator_token


class CourierHandler(BaseHTTPRequestHandler):
    server: CourierHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, value: Any = None) -> None:
        if status == HTTPStatus.NO_CONTENT:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = json.dumps(
            {} if value is None else value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _bearer(self) -> str | None:
        value = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not value.startswith(prefix):
            self.close_connection = True
            self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
            return None
        token = value[len(prefix) :]
        if not token:
            self.close_connection = True
            self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
            return None
        return token

    def _operator_authorized(self) -> bool:
        token = self._bearer()
        if token is None:
            return False
        if hmac.compare_digest(token, self.server.operator_token):
            return True
        self.close_connection = True
        self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
        return False

    def _peer(self):
        token = self._bearer()
        if token is None:
            return None
        try:
            return self.server.store.authenticate(token)
        except AuthenticationFailed:
            self.close_connection = True
            self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
            return None

    def _read_json(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._error(HTTPStatus.LENGTH_REQUIRED, "valid Content-Length required")
            return None
        if length < 1:
            self._error(HTTPStatus.BAD_REQUEST, "request body required")
            return None
        if length > MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            return None
        try:
            decoded = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "request body is not valid JSON")
            return None
        if not isinstance(decoded, dict):
            self._error(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
            return None
        return decoded

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        peer = self._peer()
        if peer is None:
            return
        if parsed.path == "/v1/peers":
            now = None
            self._send_json(
                HTTPStatus.OK,
                {"peers": [item.to_dict(now=now) for item in self.server.store.list_peers()]},
            )
            return
        if parsed.path == "/v1/messages/next":
            self._handle_next(peer.peer_id, parse_qs(parsed.query))
            return
        if parsed.path == "/v1/status":
            self._send_json(HTTPStatus.OK, self.server.store.status())
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _handle_next(
        self, peer_id: str, query: dict[str, list[str]]
    ) -> None:
        try:
            wait_seconds = int((query.get("wait_seconds") or ["20"])[0])
            lease_seconds = int((query.get("lease_seconds") or ["60"])[0])
        except ValueError:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "wait_seconds and lease_seconds must be integers",
            )
            return
        if wait_seconds < 0 or wait_seconds > 25:
            self._error(HTTPStatus.BAD_REQUEST, "wait_seconds must be between 0 and 25")
            return
        deadline = time.monotonic() + wait_seconds
        try:
            while True:
                delivery = self.server.store.lease_next(
                    peer_id, lease_seconds=lease_seconds
                )
                if delivery is not None:
                    self._send_json(HTTPStatus.OK, delivery.to_dict())
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._send_json(HTTPStatus.NO_CONTENT)
                    return
                time.sleep(min(0.25, remaining))
        except (UnknownPeer, ModelValidationError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/v1/peers/enroll":
            if self._operator_authorized():
                self._handle_enroll()
            return

        peer = self._peer()
        if peer is None:
            return
        if parsed.path == "/v1/messages":
            self._handle_send(peer.peer_id)
            return
        if parsed.path == "/v1/messages/ack":
            self._handle_ack(peer.peer_id)
            return
        if parsed.path == "/v1/heartbeat":
            self._send_json(HTTPStatus.OK, {"peer": peer.to_dict()})
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _handle_enroll(self) -> None:
        body = self._read_json()
        if body is None:
            return
        try:
            enrollment = self.server.store.register_peer(
                alias=body.get("alias", ""),  # type: ignore[arg-type]
                runtime=body.get("runtime", ""),  # type: ignore[arg-type]
                description=body.get("description", ""),  # type: ignore[arg-type]
            )
        except ModelValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except AliasConflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
            return
        except StoreFull as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        self._send_json(
            HTTPStatus.CREATED,
            {
                "peer": enrollment.peer.to_dict(),
                "peer_token": enrollment.peer_token,
            },
        )

    def _handle_send(self, sender_id: str) -> None:
        body = self._read_json()
        if body is None:
            return
        try:
            message, accepted = self.server.store.send_message(
                sender_id=sender_id,
                recipient=body.get("to", ""),  # type: ignore[arg-type]
                body=body.get("body", ""),  # type: ignore[arg-type]
                message_id=body.get("message_id", ""),  # type: ignore[arg-type]
                sent_at=parse_timestamp(body.get("sent_at"), "sent_at"),
                expires_at=parse_timestamp(body.get("expires_at"), "expires_at"),
                reply_to=body.get("reply_to", ""),  # type: ignore[arg-type]
                priority=body.get("priority", "normal"),  # type: ignore[arg-type]
            )
        except (ModelValidationError, ReplyConflict) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except UnknownPeer as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except MessageConflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
            return
        except StoreFull as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        self._send_json(
            HTTPStatus.CREATED if accepted else HTTPStatus.OK,
            {"accepted": accepted, "message": message.to_dict()},
        )

    def _handle_ack(self, recipient_id: str) -> None:
        body = self._read_json()
        if body is None:
            return
        try:
            self.server.store.acknowledge(
                recipient_id=recipient_id,
                message_id=body.get("message_id", ""),  # type: ignore[arg-type]
                lease_id=body.get("lease_id", ""),  # type: ignore[arg-type]
            )
        except ModelValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except LeaseConflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
            return
        self._send_json(HTTPStatus.OK, {"acknowledged": True})


def make_server(
    listen: str,
    port: int,
    store: CourierStore,
    operator_token: str,
    *,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> CourierHTTPServer:
    server = CourierHTTPServer((listen, port), store, operator_token)
    if bool(tls_cert) != bool(tls_key):
        server.server_close()
        raise ValueError("both TLS certificate and key are required")
    if tls_cert and tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
