# Contributing

Agent Courier is early alpha. Small, focused changes with tests and updated
documentation are welcome.

## Public-repository hygiene

Use synthetic aliases, messages, addresses, paths, and credentials in source,
tests, screenshots, and documentation. Never commit material copied from real
agent conversations or private network configuration.

## Verification

Run:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Security-sensitive changes should include failure-path tests. Network and hook
failures must not cause message spoofing, message loss without an observable
state, or interruption of the host agent.
