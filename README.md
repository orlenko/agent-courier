# Agent Courier

Agent Courier is a private, durable message courier for Claude Code, Codex, and
other terminal agents running across multiple machines.

It exists so a human does not have to copy and paste notes between agents.
Agents get stable identities, discoverable peers, direct messages, replies,
offline delivery, and explicit acknowledgements. Courier transports text; it
does not run agents, delegate work, or grant one agent authority over another.

> **Project status:** early alpha. The requirements and security boundaries are
> documented. The first implementation slice is a dependency-free Python hub
> and CLI client with authenticated peer identities, SQLite-backed delivery,
> leases, acknowledgements, and long polling. Provider hooks and managed
> background bridges are the next slice.

## Intended topology

```text
Ubuntu agents ──┐
                ├── outbound HTTP over Tailscale ──> always-on Courier hub
Mac agents ─────┘                                      │
                                                      SQLite
Mac agents <──── outbound long poll over Tailscale ────┘
```

The roaming laptop never needs an inbound port. If it sleeps or changes
networks, messages remain queued on the hub and are delivered after it
reconnects.

## Product boundaries

- **Agent Courier:** agent-to-agent messaging.
- **Agent Announcer:** agent-to-human notifications.
- **Agent Rationale (`rat`):** historical reasoning, commit rationale, and
  durable whiteboards.

Courier borrows lessons from the original local `rat a2a` prototype, but it is
an independent product with its own identity, protocol, storage, and security
model.

## Safety model

- Message bodies are untrusted peer content, never system or developer
  instructions.
- The server derives the sender from a per-peer credential; clients cannot
  choose an arbitrary `from` identity.
- Credentials and private deployment details belong in mode-`0600` files, not
  repositories or command history.
- Requests are bounded and authenticated.
- Plain HTTP on a non-loopback listener requires an explicit assertion that the
  address is protected by Tailscale. Other deployments should use TLS.
- A failed or unreachable Courier service must not block Claude Code or Codex.

See [requirements](docs/requirements.md), [architecture](docs/architecture.md),
the [security model](docs/security.md), and the public
[message schema](schemas/message-v1.schema.json) for the complete contract.
The alternatives considered are recorded in [prior art](docs/prior-art.md).

## Local quickstart

Install from the checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Create the hub's operator credential and start a loopback server:

```sh
mkdir -p ~/.config/agent-courier
agent-courier create-token ~/.config/agent-courier/operator.token

agent-courier serve \
  --operator-token-file ~/.config/agent-courier/operator.token
```

Enroll two synthetic peers:

```sh
agent-courier join \
  --broker http://127.0.0.1:8790 \
  --operator-token-file ~/.config/agent-courier/operator.token \
  --alias frontend \
  --runtime Codex

agent-courier join \
  --broker http://127.0.0.1:8790 \
  --operator-token-file ~/.config/agent-courier/operator.token \
  --alias backend \
  --runtime Claude
```

Send, claim, and acknowledge a message:

```sh
agent-courier send \
  --config ~/.config/agent-courier/frontend.peer.json \
  --to backend \
  "Please check the API schema."

agent-courier claim \
  --config ~/.config/agent-courier/backend.peer.json

agent-courier ack \
  --config ~/.config/agent-courier/backend.peer.json \
  --message-id <message-id> \
  --lease-id <lease-id>
```

Claiming does not imply acknowledgement. If the recipient crashes or never
acknowledges, the lease expires and the hub makes the message available again.

For a two-machine Tailnet setup, see the
[deployment guide](docs/deployment.md).

## Development

Requirements:

- Python 3.10 or newer
- no required third-party runtime dependencies

Run the tests:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Current implementation and handoff notes live in [docs/status.md](docs/status.md).

Agent Courier is available under the [MIT License](LICENSE).
