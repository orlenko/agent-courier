"""Command-line operator and peer interface."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Sequence

from .api import ApiError, CourierClient, OperatorClient
from .config import (
    ConfigError,
    create_operator_token,
    default_config_dir,
    default_state_dir,
    read_peer_credentials,
    read_secret_file,
    write_peer_credentials,
)
from .models import ModelValidationError
from .server import make_server
from .store import CourierStore


DEFAULT_PORT = 8790


def _peer_client(config_path: str) -> CourierClient:
    return CourierClient.from_credentials(read_peer_credentials(config_path))


def _add_peer_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        required=True,
        help="mode-0600 peer credential JSON created by `agent-courier join`",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-courier",
        description="Private, durable messaging between terminal agents",
    )
    parser.add_argument("--version", action="version", version="agent-courier 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    token = commands.add_parser(
        "create-token", help="create a private operator enrollment token"
    )
    token.add_argument("path")

    serve = commands.add_parser("serve", help="run the durable Courier hub")
    serve.add_argument("--listen", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument(
        "--database",
        default=str(default_state_dir() / "courier.db"),
    )
    serve.add_argument("--operator-token-file", required=True)
    serve.add_argument("--max-peers", type=int, default=1_000)
    serve.add_argument("--max-messages", type=int, default=100_000)
    serve.add_argument(
        "--retention-seconds", type=int, default=7 * 24 * 60 * 60
    )
    serve.add_argument("--tls-cert")
    serve.add_argument("--tls-key")
    serve.add_argument(
        "--allow-tailnet-http",
        action="store_true",
        help="assert that a non-loopback HTTP listener is Tailnet-only",
    )
    serve.add_argument("--verbose", action="store_true")

    join = commands.add_parser("join", help="enroll a peer and save its credential")
    join.add_argument("--broker", required=True)
    join.add_argument("--operator-token-file", required=True)
    join.add_argument("--alias", required=True)
    join.add_argument("--runtime", default="")
    join.add_argument("--description", default="")
    join.add_argument(
        "--output",
        help="credential destination (default: config directory/<alias>.peer.json)",
    )

    peers = commands.add_parser("peers", help="list registered peers")
    _add_peer_config(peers)
    peers.add_argument("--json", action="store_true")

    send = commands.add_parser("send", help="send a direct message")
    _add_peer_config(send)
    send.add_argument("--to", required=True, help="exact alias or peer UUID")
    send.add_argument("--reply-to", default="")
    send.add_argument(
        "--priority", choices=("low", "normal", "high"), default="normal"
    )
    send.add_argument(
        "--ttl-seconds", type=int, default=7 * 24 * 60 * 60
    )
    send.add_argument("--message-id", help="UUID idempotency key")
    send.add_argument(
        "body",
        nargs="?",
        help="message body; reads stdin when omitted",
    )

    claim = commands.add_parser(
        "claim", help="lease the next message without acknowledging it"
    )
    _add_peer_config(claim)
    claim.add_argument("--wait-seconds", type=int, default=0)
    claim.add_argument("--lease-seconds", type=int, default=120)
    claim.add_argument("--json", action="store_true")

    ack = commands.add_parser("ack", help="acknowledge a leased message")
    _add_peer_config(ack)
    ack.add_argument("--message-id", required=True)
    ack.add_argument("--lease-id", required=True)

    heartbeat = commands.add_parser(
        "heartbeat", help="refresh presence and print this peer"
    )
    _add_peer_config(heartbeat)
    heartbeat.add_argument("--json", action="store_true")

    status = commands.add_parser("status", help="show body-free hub status")
    _add_peer_config(status)
    status.add_argument("--json", action="store_true")

    return parser


def _command_create_token(args: argparse.Namespace) -> int:
    path = create_operator_token(args.path)
    print(f"Created private operator token: {path}")
    return 0


def _command_serve(args: argparse.Namespace) -> int:
    if args.port < 1 or args.port > 65535:
        raise ValueError("port must be between 1 and 65535")
    has_tls = bool(args.tls_cert or args.tls_key)
    loopback = args.listen in {"127.0.0.1", "::1", "localhost"}
    if not loopback and not has_tls and not args.allow_tailnet_http:
        raise ValueError(
            "refusing non-loopback plain HTTP; bind to loopback, configure TLS, "
            "or explicitly pass --allow-tailnet-http"
        )
    operator_token = read_secret_file(args.operator_token_file)
    store = CourierStore(
        args.database,
        max_peers=args.max_peers,
        max_messages=args.max_messages,
        retention_seconds=args.retention_seconds,
    )
    store.initialize()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = make_server(
        args.listen,
        args.port,
        store,
        operator_token,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
    )
    scheme = "https" if has_tls else "http"
    print(f"Agent Courier hub listening on {scheme}://{args.listen}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _command_join(args: argparse.Namespace) -> int:
    operator_token = read_secret_file(args.operator_token_file)
    credentials = OperatorClient(args.broker, operator_token).enroll(
        alias=args.alias,
        runtime=args.runtime,
        description=args.description,
    )
    output = (
        Path(args.output).expanduser()
        if args.output
        else default_config_dir() / f"{credentials.alias}.peer.json"
    )
    write_peer_credentials(output, credentials)
    print(f"Enrolled {credentials.alias} ({credentials.peer_id})")
    print(f"Saved private peer credential: {output}")
    return 0


def _command_peers(args: argparse.Namespace) -> int:
    peers = _peer_client(args.config).list_peers()
    if args.json:
        print(json.dumps({"peers": peers}, indent=2, ensure_ascii=False))
        return 0
    if not peers:
        print("No peers registered.")
        return 0
    for peer in peers:
        runtime = f" [{peer['runtime']}]" if peer.get("runtime") else ""
        description = f" — {peer['description']}" if peer.get("description") else ""
        print(
            f"{peer['alias']}{runtime} ({peer['peer_id']}) "
            f"{peer['presence']}{description}"
        )
    return 0


def _read_message_body(argument: str | None) -> str:
    if argument is not None:
        return argument
    if sys.stdin.isatty():
        raise ValueError("message body required as an argument or on stdin")
    body = sys.stdin.read()
    if not body.strip():
        raise ValueError("message body must not be empty")
    return body


def _command_send(args: argparse.Namespace) -> int:
    message, accepted = _peer_client(args.config).send_message(
        recipient=args.to,
        body=_read_message_body(args.body),
        reply_to=args.reply_to,
        priority=args.priority,
        ttl_seconds=args.ttl_seconds,
        message_id=args.message_id,
    )
    verb = "Queued" if accepted else "Already queued"
    print(
        f"{verb} {message.message_id} for "
        f"{message.recipient_alias} ({message.recipient_id})"
    )
    return 0


def _command_claim(args: argparse.Namespace) -> int:
    delivery = _peer_client(args.config).next_message(
        wait_seconds=args.wait_seconds,
        lease_seconds=args.lease_seconds,
    )
    if delivery is None:
        if args.json:
            print(json.dumps({"status": "empty"}))
        else:
            print("Inbox empty.")
        return 0
    if args.json:
        print(
            json.dumps(
                {"status": "claimed", **delivery.to_dict()},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    message = delivery.message
    print("[UNTRUSTED PEER MESSAGE]")
    print(f"from:       {message.sender_alias} ({message.sender_id})")
    print(f"message_id: {message.message_id}")
    print(f"lease_id:   {delivery.lease_id}")
    print(f"sent_at:    {message.to_dict()['sent_at']}")
    if message.reply_to:
        print(f"reply_to:   {message.reply_to}")
    print()
    print(message.body)
    print()
    print(
        "After processing, acknowledge with:\n"
        f"  agent-courier ack --config <peer-config> "
        f"--message-id {message.message_id} --lease-id {delivery.lease_id}"
    )
    return 0


def _command_ack(args: argparse.Namespace) -> int:
    _peer_client(args.config).acknowledge_ids(
        message_id=args.message_id,
        lease_id=args.lease_id,
    )
    print(f"Acknowledged {args.message_id}")
    return 0


def _command_heartbeat(args: argparse.Namespace) -> int:
    peer = _peer_client(args.config).heartbeat().to_dict()
    if args.json:
        print(json.dumps(peer, indent=2, ensure_ascii=False))
    else:
        print(f"{peer['alias']} ({peer['peer_id']}) {peer['presence']}")
    return 0


def _command_status(args: argparse.Namespace) -> int:
    status = _peer_client(args.config).status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"peers: {status['peers']}")
        messages = status.get("messages", {})
        if messages:
            for state, count in messages.items():
                print(f"messages.{state}: {count}")
        else:
            print("messages: 0")
    return 0


COMMANDS = {
    "create-token": _command_create_token,
    "serve": _command_serve,
    "join": _command_join,
    "peers": _command_peers,
    "send": _command_send,
    "claim": _command_claim,
    "ack": _command_ack,
    "heartbeat": _command_heartbeat,
    "status": _command_status,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (
        ApiError,
        ConfigError,
        ModelValidationError,
        OSError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
