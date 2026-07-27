# Implementation status

Last updated: 2026-07-27

## Working now

- Product boundary, requirements, architecture, and security model.
- Public Python package scaffold with no required runtime dependencies.
- Versioned, size-bounded peer and message contracts.
- SQLite peer registry, idempotent messages, expiry, leases, redelivery, and
  acknowledgements.
- Operator-token enrollment that returns a per-peer credential; the hub stores
  only peer-token hashes.
- Authenticated HTTP hub with health, peer directory, send, long-poll claim,
  acknowledgement, heartbeat, and body-free status endpoints.
- CLI token creation, enrollment, directory, send, claim, acknowledgement,
  heartbeat, status, and Tailnet-safe listener guard.
- Unit and live HTTP integration tests.
- Local and two-machine deployment documentation.

## Next slices

1. Background bridge with an atomic, private local inbox.
2. Claude Code and Codex prompt hooks that surface inbox summaries without
   network access in the hook path.
3. Managed systemd and launchd services.
4. Per-peer revocation, rotation, and single-use enrollment credentials.
5. Optional provider-specific live hints and opt-in wake adapters.
6. Human operator inbox and message inspection without exposing bodies in
   routine status.

## Known alpha limitations

- The first slice requires explicit claim and acknowledgement commands.
- The operator enrollment token is reusable.
- Peers cannot yet rotate or revoke credentials through the CLI.
- There are no contact policies, channels, attachments, or broadcasts.
- Message bodies are stored unencrypted at rest.
- There is no packaged release or CI workflow.

## Handoff entry points

- Product contract: `docs/requirements.md`
- Runtime design: `docs/architecture.md`
- Threat model: `docs/security.md`
- Deployment guide: `docs/deployment.md`
- Alternatives and prototype lessons: `docs/prior-art.md`
- Wire models: `src/agent_courier/models.py`
- SQLite lifecycle: `src/agent_courier/store.py`
- HTTP boundary: `src/agent_courier/server.py`
- CLI: `src/agent_courier/cli.py`
- End-to-end test: `tests/test_integration.py`
