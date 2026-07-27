# Deployment

This guide covers the intended two-machine alpha topology: an always-on Ubuntu
hub and a roaming macOS client connected by Tailscale.

Use synthetic placeholders in checked-in configuration. Never commit rendered
credentials, hostnames, addresses, or real message bodies.

## 1. Install on both machines

From a checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The package has no required third-party runtime dependencies.

## 2. Create the operator token on the hub

```sh
mkdir -p ~/.config/agent-courier
agent-courier create-token ~/.config/agent-courier/operator.token
```

The file is created with mode `0600`. It can enroll new peers and should not be
made available to ordinary agent turns.

## 3. Start the Ubuntu hub

Bind specifically to the machine's Tailnet address:

```sh
agent-courier serve \
  --listen "$(tailscale ip -4)" \
  --port 8790 \
  --operator-token-file ~/.config/agent-courier/operator.token \
  --allow-tailnet-http
```

`--allow-tailnet-http` is an explicit assertion that Tailscale's encrypted
network and access controls protect the listener. Do not use it for a public or
ordinary LAN listener; configure `--tls-cert` and `--tls-key` instead.

The alpha runs in the foreground. A systemd service template will be added with
the managed bridge slice.

## 4. Enroll a peer on each machine

The enrolling client needs temporary access to the operator token. Transfer it
through an authenticated private channel, enroll the peer, and remove the
copied operator token from that client afterward.

On the Ubuntu host:

```sh
agent-courier join \
  --broker http://courier-hub:8790 \
  --operator-token-file /private/path/operator.token \
  --alias backend \
  --runtime Claude \
  --description "Backend implementation"
```

On the laptop:

```sh
agent-courier join \
  --broker http://courier-hub:8790 \
  --operator-token-file /private/path/operator.token \
  --alias reviewer \
  --runtime Codex \
  --description "Cross-project review"
```

Here `courier-hub` is a placeholder for the hub's private MagicDNS name. The
resulting `*.peer.json` file is mode `0600` and contains the peer's own
least-privilege credential.

## 5. Verify connectivity

```sh
agent-courier peers \
  --config ~/.config/agent-courier/reviewer.peer.json

agent-courier status \
  --config ~/.config/agent-courier/reviewer.peer.json
```

Routine status contains queue counts but no message bodies or credentials.

## Current delivery workflow

The alpha client explicitly sends, claims, and acknowledges messages. Use
`claim --json` for deterministic agent integration.

```sh
agent-courier claim \
  --config ~/.config/agent-courier/reviewer.peer.json \
  --wait-seconds 20 \
  --lease-seconds 120 \
  --json
```

Do not acknowledge until the recipient has durably accepted or processed the
message. An unacknowledged lease returns to the queue automatically.

The next implementation slice moves long polling out of agent turns: a
background bridge will write an atomic local inbox and acknowledge only after
that write. Claude Code and Codex prompt hooks will then read local state
without making network requests.

## Backup and recovery

The SQLite database defaults to:

```text
~/.local/state/agent-courier/courier.db
```

Back up the database together with its `-wal` and `-shm` files using a
SQLite-aware snapshot procedure or while the service is stopped. Peer
credential files are not recoverable from the database because only their
hashes are stored; retain secure copies or re-enroll peers.
