# Trust Runtime

Trust Runtime is an open-source portfolio prototype for making browser-agent side effects inspectable, authorized, verified, and recoverable.

The reference workflow uses three clearly synthetic applications—GoMail, Northstar Air, and DayPlan—to recover a cancelled trip. The actor operates through screenshots and coordinate-based browser actions. A deterministic supervisor enforces the immutable task contract, pauses the exact commit action for approval, blocks blind retries, verifies observable outcomes, and records an append-only trace. A separate oracle process scores completed runs from sealed sandbox state.

The authoritative product and acceptance contract is [goal.md](./goal.md).

## Current status

Implementation is in progress. The deterministic browser workflow, atomic PostgreSQL grant/booking/effect gateway, filesystem and S3 artifact backends, exact browser egress guard, replay artifacts, operator-capped evaluation queue, disclosed baseline/protected workers, sealed oracle scoring, strict raw export, and immutable metric provenance are implemented. No reliability metric is claimed until the pinned paid evaluation has run and its raw rows are published.

## Local workflow

```bash
cp .env.example .env
make bootstrap
make db-up
make db-migrate
make demo
```

The deterministic mock adapter requires no external model key. A real live-model run requires `AGENT_PROVIDER=openai`, a server-side `OPENAI_API_KEY`, and pinned positive model input/output prices. PostgreSQL is required whenever public live runs are enabled.

For a paid evaluation, queue the pinned plan with `make eval-paired`, run the runtime and sealed-oracle workers as separate processes with `make eval-worker-runtime` and `make eval-worker-oracle`, then generate the immutable report with `make eval-report EVALUATION_ID=<uuid>`. Runtime and oracle processes use separate PostgreSQL roles and the oracle alone receives `ORACLE_SANDBOX_ADMIN_TOKEN`.

## Honesty boundary

- All applications, identities, reservations, and money are synthetic.
- Mock runs, live model runs, and recorded replays are mechanically labeled.
- The actor cannot read the DOM, application APIs, databases, evaluator state, fault IDs, seeds, or expected answers.
- The project is not production-ready, universal, on-device, or affiliated with AGI, Inc.

Licensed under Apache-2.0.
