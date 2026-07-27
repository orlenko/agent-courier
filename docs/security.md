# Security model

## Trust assumptions

The intended first deployment is one operator's machines connected by
Tailscale. The hub, local operating-system account, and Tailnet administration
are trusted. Coding agents and all message bodies are less trusted.

Courier is not a multi-user authorization system in the MVP.

## Assets

- operator enrollment token;
- per-peer bearer tokens;
- peer directory metadata;
- queued message bodies and reply relationships;
- SQLite database and backups;
- local inbox files added by the future bridge.

## Main threats and mitigations

### Sender spoofing

Clients never provide the authoritative sender ID. The hub maps a bearer-token
digest to one peer and fills the sender field itself.

### Prompt injection through peer messages

Authentication proves which peer submitted bytes; it does not make those bytes
instructions. Adapters must delimit and label messages as untrusted peer
content. A message cannot override user, developer, or system instructions.

### Credential disclosure

Tokens are returned only at creation, stored in mode-`0600` files, compared
using constant-time operations where applicable, and omitted from logs and
status output. The hub stores peer-token hashes, not plaintext tokens.

### Network interception

Loopback HTTP is allowed. Non-loopback HTTP requires an explicit
`--allow-tailnet-http` assertion and should bind specifically to the host's
Tailnet address. Public or ordinary LAN exposure requires TLS.

### Replay and duplicate delivery

Client-supplied message IDs provide idempotent submission. Delivery is
intentionally at least once; consumers deduplicate by message ID and
acknowledgement requires a current random lease ID.

### Denial of service

The server bounds request bytes, field lengths, message TTL, long-poll wait,
lease duration, peer count, queued-message count, and retained history. SQLite
uses a busy timeout, and request threads have finite work.

### Compromised peer

A peer credential can send messages and read only its own inbox. It cannot send
as another peer or claim another inbox. In the alpha it can read the peer
directory. Per-peer revocation and token rotation are required before beta.

### Malicious local files

Credential file ownership and mode are checked before use. Future local inbox
writes must use private directories, bounded filenames derived from validated
IDs, same-directory staging, and atomic rename.

## Explicit limitations

- The operator token can enroll arbitrary peers and must not be made available
  to ordinary agent turns after setup.
- There is no per-recipient allowlist or contact handshake yet.
- Message bodies are stored unencrypted in SQLite; disk encryption and host
  access controls protect data at rest.
- Traffic metadata remains visible to the hub.
- Tailscale network membership is not a substitute for application
  authentication; Courier requires both.
