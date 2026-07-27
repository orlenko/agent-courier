# Architecture

## Boundary

Courier is a store-and-forward mailbox, not an orchestrator. Its authority ends
after authenticating a sender, resolving a recipient, durably storing an
envelope, and delivering it with an explicit acknowledgement lifecycle.

Agent Rationale remains responsible for historical reasoning artifacts. Agent
Announcer remains responsible for agent-to-human notifications.

## Components

```text
provider adapter or agent
          │
          ▼
   agent-courier CLI
          │ HTTPS or Tailnet HTTP
          ▼
 Courier hub + SQLite
          │ long poll + lease
          ▼
   recipient client
          │
          ▼
 durable local inbox / provider hook
```

### Hub

The hub is an HTTP service intended for the always-on machine. It owns:

- peer registration and credential hashes;
- alias resolution and presence timestamps;
- message validation, idempotency, and bounded retention;
- delivery leases and acknowledgements;
- health and body-free operator status.

The hub does not inspect repositories, start agents, or connect to the laptop.

### Client

The CLI authenticates with a mode-`0600` peer configuration. The initial slice
talks directly to the hub for directory, send, claim, and acknowledgement
operations.

The next slice adds a background bridge. It will long-poll the hub, atomically
write leased messages into a local inbox, then acknowledge the hub only after
the write succeeds. Provider hooks will read only the local inbox, keeping
prompt submission fast and fail-soft.

## Identity

`peer_id` is a random UUID and is the durable routing key. `alias` is a mutable
human-facing label. Runtime, description, and activity timestamps are
informational.

This deliberately avoids the local prototype's path-derived identities:

- absolute paths leak private machine layout;
- the same checkout may live at different paths on different machines;
- renaming or moving a checkout must not change the network address;
- a network sender must not need filesystem access to resolve a recipient.

The MVP treats one registered client configuration as one peer. Provider
adapters can later decide whether a peer represents a session, worktree, or
long-lived role.

## Authentication

Enrollment uses an operator token. The hub returns a random peer token once and
stores only its SHA-256 digest. Normal requests authenticate with the peer
token. The server derives `sender_id` and inbox ownership from that credential.

The initial operator token is shared administration authority. Scoped,
single-use enrollment tokens and rotation are planned before beta.

## Message lifecycle

1. The sender supplies a recipient alias or ID, body, optional reply ID,
   priority, TTL, and optionally an idempotency ID.
2. The hub derives the sender from authentication and resolves the recipient.
3. A transaction inserts the canonical envelope as `pending`.
4. A recipient long-poll atomically changes the oldest eligible message to
   `leased`, assigns a random lease ID, and returns it.
5. Successful durable receipt is acknowledged using both message and lease ID.
6. A crashed consumer leaves a lease behind; after expiry the hub returns the
   message to `pending`.
7. Expired messages are never leased and are retained only for the configured
   audit window.

The network guarantee is at least once. Message IDs make duplicates safe to
detect.

## Lessons retained from the local prototype

- Atomic envelopes and opaque IDs.
- Exact-alias-first recipient resolution with ambiguity errors.
- Explicit reply links.
- Atomic claims rather than “read means handled.”
- Crash recovery for abandoned claims.
- Fail-soft prompt hooks and an authoritative durable inbox.
- Small body-free sent/handled traces for diagnostics.

## Lessons not carried forward

- Repository paths as identity.
- Filesystem watchers as the only delivery trigger.
- PID files as proof that a daemon is alive.
- Treating best-effort desktop notifications as delivery.
- Embedding network messaging into an unrelated rationale product.
- Stale help text that describes a retired transport; status documentation is a
  required release artifact.

## Why HTTP and SQLite

HTTP works through Tailscale without an inbound laptop port and is available in
the Python standard library. SQLite provides transactional leases, durable
offline queues, bounded operational complexity, and straightforward backups.

MCP can be an adapter later. It is not the wire protocol because the CLI and
provider hooks already need a transport-independent mailbox API, and stock
Codex does not expose generic MCP notifications as model-visible pushes.
