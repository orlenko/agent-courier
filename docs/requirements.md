# Requirements

Status: implementation contract; see `status.md` for current progress.

## Problem

A person running Claude Code, Codex, or similar terminal agents on several
machines should not have to copy and paste short coordination notes between
them. Agents need a private way to discover one another and exchange durable
messages over a LAN or Tailnet.

## Functional requirements

### Identity and discovery

- Each participating agent has an opaque stable peer ID and a human-friendly
  alias.
- Filesystem paths and hostnames are not network identities.
- Aliases are unique among active peers; exact peer IDs remain authoritative.
- A peer advertises bounded, optional runtime and description fields.
- The peer directory reports recent activity without promising that an idle
  model process is currently executing.

### Messaging

- The MVP supports direct text messages and replies.
- Messages have globally unique IDs, sender and recipient IDs, timestamps,
  expiry, priority, and optional `reply_to`.
- The hub derives the sender from its credential; a caller cannot spoof
  another peer.
- Recipient resolution accepts an exact alias or peer ID and rejects ambiguous
  or missing targets.
- Duplicate submission of the same message ID and content is idempotent.
- Reusing a message ID with different content is rejected.
- Broadcasts, attachments, arbitrary remote commands, and automatic delegation
  are out of scope for the MVP.

### Delivery

- The always-on hub durably retains messages while recipients are offline.
- Recipients connect outbound and may long-poll; no inbound laptop port is
  required.
- Delivery is at least once. Leases and acknowledgements prevent silent loss;
  consumers deduplicate by message ID.
- Expired leases become available again.
- Acknowledgement requires the current lease ID.
- Messages expire after a bounded TTL and retention is bounded.
- Network unavailability never blocks or crashes the host coding agent.

### Agent integration

- A provider-neutral CLI is the source of truth.
- Claude Code and Codex adapters may use hooks, MCP tools, or managed launchers,
  but core messaging must not depend on one provider.
- Prompt integration labels message bodies as untrusted peer content.
- The durable inbox is authoritative; best-effort live notifications are only
  hints.
- Stock Codex may receive mail on its next prompt or explicit check. Optional
  wake adapters must be separate and opt in because they create model turns.

### Operator experience

- The hub runs on macOS or Linux with SQLite and no external service.
- The CLI exposes health, peer directory, send, claim, acknowledge, and status
  operations with machine-readable JSON modes.
- Diagnostics distinguish unavailable service, authentication failure, stale
  leases, expired messages, and invalid local credential files.
- Configuration examples contain placeholders only.

## Security and privacy requirements

- Every non-health request is authenticated.
- Enrollment uses an operator credential; normal operations use per-peer
  credentials with least-privilege routing.
- Only credential hashes are stored by the hub.
- Peer credentials are stored in mode-`0600` files.
- Plain HTTP may bind beyond loopback only with explicit operator confirmation
  that a private encrypted network such as Tailscale protects the listener.
- Public or ordinary LAN deployment requires TLS.
- Request sizes, field lengths, TTLs, waits, and leases are bounded.
- Logs and status output omit credentials and message bodies by default.
- Message content is untrusted input and must never be interpreted as authority
  merely because it came through Courier.

## Reliability requirements

- SQLite writes use transactions, foreign keys, a busy timeout, and WAL mode.
- Mutating operations are idempotent where retry is expected.
- Leases recover automatically after crashes.
- Server restart does not lose accepted messages or peer registrations.
- A failed audit or convenience notification cannot convert a failed delivery
  into a reported success.
- Tests use isolated temporary state and never read or write live credentials.

## Non-goals

- General-purpose chat for humans.
- Capturing commit rationale or session transcripts.
- Human notification delivery such as speech, email, or banners.
- Hosting or orchestrating model processes.
- A trust or authorization hierarchy between message senders and recipients.
- Compatibility with Google's task-oriented Agent2Agent protocol in the MVP.
