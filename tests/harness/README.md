# Acceptance harness

Helpers for acceptance tests that run real CLI processes and browsers:

- `process.py` — process groups, captured output, graceful interrupt, tree
  termination and abrupt crash simulation;
- `connect_proxy.py` — local programmable HTTP CONNECT proxy: `tunnel` relays
  traffic, `proxy_auth` answers `407`;
- `artifact_workspace.py` — per-scenario workspaces inside the test session:
  successful ones are removed, failed or explicitly requested ones are retained.

Added together with the runtime (stages 3–4, see `roadmaps/plan.md`): the CLI
run controller, the SQLite runtime probe that waits on authoritative state
instead of sleeps, config overlays for scenario workspaces and shared terminal
integrity assertions.

Harness modules stay site-neutral. Site-specific selectors and expected data
belong in `tests/scrapers` and `tests/fixtures`.
