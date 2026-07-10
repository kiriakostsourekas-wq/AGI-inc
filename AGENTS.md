# Repository instructions

`goal.md` is the authoritative product and acceptance contract. Do not narrow its scope silently; record any necessary correction in the decision log and compliance matrix.

## Trust invariants

- Never put fault ID, seed, expected result, or oracle output in actor context.
- Never trust model-supplied effect class, origin, approval scope, or idempotency key.
- Never execute a commit without a server-bound grant for the exact semantic effect proposal.
- Never retry an unresolved commit before visible-state verification.
- `SAFE_ABORTED` proves zero side effects; use partial/handoff/unknown states otherwise.
- Never label mock or replay output as live.
- Never hand-author evaluation wins or discard failed attempts.
- The actor uses screenshots and coordinate actions only—no DOM, locators, app APIs, database, or evaluator access.

## Commands

```bash
make bootstrap
make lint
make typecheck
make test
make test-e2e
make eval-smoke
```

Update `docs/compliance-matrix.md` when a requirement gains implementation and verification evidence.
