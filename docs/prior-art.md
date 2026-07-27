# Prior art

Research snapshot: 2026-07-27.

Courier is intentionally narrow, but it should not unknowingly reinvent broader
agent-coordination systems.

## Agent Relay

[Agent Relay](https://github.com/AgentWorkforce/relay) provides real-time
channels, direct messages, presence, durable delivery, provider harnesses, and
agent wake-up across machines. Its current cross-machine architecture routes
through a central Agent Relay engine and includes a substantially broader agent
runtime and orchestration model.

Courier differs by targeting a small, privately operated Tailnet mailbox for
already-running terminal agents.

## MCP Agent Mail

[MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) provides
HTTP-accessible identities, inboxes, threads, acknowledgements, searchable
history, Git archives, and advisory file reservations. It is the strongest
self-hostable alternative evaluated.

Courier differs by omitting project archives, attachments, search, reservations,
task tracking, and MCP as the core wire protocol. A deployment should reevaluate
Agent Mail before Courier grows any of those features.

The Rust rewrite currently documents itself as a single-machine coordination
system, so its HTTP support should not be assumed to establish a distributed
session-delivery contract.

## Agent Peers MCP

[Agent Peers MCP](https://github.com/Co-Messi/agent-peers-mcp) closely matches
the desired colleague experience: discovery, direct messages, durable leases,
Claude hints, Codex polling, and an optional app-server wake wrapper. Its
security warning explicitly limits the broker to single-user localhost use and
says not to expose it beyond loopback.

Courier retains its useful durable-inbox and optional-wake ideas while treating
network authentication and Tailnet deployment as first-class requirements.

## Agent2Agent protocol

Google's [Agent2Agent
protocol](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)
standardizes remote agent discovery, tasks, messages, artifacts, streaming, and
authentication. It is oriented around callable remote agent services and
long-running task lifecycles.

Courier is a mailbox adapter for existing interactive terminal sessions. A2A
compatibility can be considered later, but it should not inflate the MVP.

## Local `rat a2a` prototype

The local Agent Rationale a2a feature supplied the most directly relevant
prototype evidence:

- stable IDs are better routing keys than aliases;
- atomic envelopes and explicit claims survive concurrency;
- replies need durable message IDs;
- abandoned claims need recovery;
- the durable inbox must remain authoritative;
- provider hooks must fail soft;
- path-derived identities do not generalize across machines;
- filesystem watcher and PID-file daemons accumulated avoidable operational
  complexity;
- messaging was outside Agent Rationale's historical-reasoning product
  boundary.

Courier is a clean product extraction of the messaging need, not a network
extension of Agent Rationale.

## Codex delivery constraint

Stock Codex does not currently surface generic MCP `notifications/message`
events directly to the model. The limitation is tracked in
[openai/codex#18056](https://github.com/openai/codex/issues/18056).

Courier therefore treats next-prompt inbox delivery as the portable baseline.
Any mechanism that actively wakes a Codex session must be an explicit optional
adapter because it starts a model turn and incurs cost.
