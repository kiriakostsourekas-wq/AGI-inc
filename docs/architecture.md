# Architecture

Status: implementation in progress. This document describes the frozen trust boundaries; code and evidence links are added as components land.

## Core loop

```text
observe -> propose -> derive effect -> authorize -> act -> verify -> recover/finalize
```

The model proposes only a UI action and expected visible result. The runtime independently derives the current origin, effect class, semantic commit context, approval requirement, and stable idempotency key.

## Process boundaries

```text
Product UI (Next.js :3000)
  └─ Runtime API (FastAPI :8000)
       ├─ screenshot-only Playwright actor
       ├─ deterministic policy / approval / verifier
       ├─ Postgres runtime role
       └─ filesystem or S3-compatible artifacts

Synthetic sandbox (Next.js :3001)
  ├─ GoMail
  ├─ Northstar Air ──internal token──> runtime trust gateway
  └─ DayPlan + verified-booking guard

Sealed oracle (separate process and credentials)
  └─ post-termination reads of ground-truth state only
```

The runtime process does not receive oracle database credentials. The oracle cannot send an action, recovery hint, or intermediate score to the actor.

## Actor-visible versus sealed state

| Data                   |   Actor |     Runtime supervisor |   Public observer | Sealed oracle |
| ---------------------- | ------: | ---------------------: | ----------------: | ------------: |
| Task contract          |     yes |                    yes |               yes |           yes |
| Current screenshot     |     yes |                    yes |               yes |     after run |
| Current browser origin |     yes |                    yes |               yes |     after run |
| Fault ID and seed      |      no |          injector only |               yes |           yes |
| Expected outcome       |      no |                     no |     no during run |           yes |
| Sandbox database       |      no |                     no |                no |           yes |
| Approval context       | summary |                    yes |               yes |     after run |
| Oracle result          |      no | after termination only | after publication |           yes |

## Approval lifecycle

The commit click itself is paused. Policy creates an immutable `effect_proposal_id`, and the human decision applies to that proposal. The approval server signs the semantic context—not raw pixels. The sandbox receives only the durable grant ID through its admin-authenticated binding. A genuine rendered click triggers Northstar's server request to the token-protected internal runtime gateway; the gateway reconstructs authority from PostgreSQL and consumes the grant in the same transaction that creates the booking and side-effect rows. Northstar materializes its visible projection only after that transaction succeeds. Baseline evaluation deliberately omits this binding.

The Playwright context aborts every HTTP(S) and WebSocket request outside the three exact contract origins, and the executor checks the resulting origin again after navigation or clicks. Model, artifact, and telemetry hosts use a separate server-only allowlist.

## Persistence and concurrency

PostgreSQL is the source of truth for runs, append-only events, derived effect
proposals, approvals, grants, the side-effect ledger, and worker jobs. Externally
visible IDs are application-generated UUIDv7 values; the event store alone uses a
monotonic `bigint identity` key. Every foreign key has a supporting index, active
queue/approval lookups use partial indexes, and workers claim jobs through `FOR
UPDATE SKIP LOCKED`.

The Northstar gateway verifies the stored HMAC capability and current semantic
context, consumes the exact grant, inserts the idempotency-ledger row, and creates
the synthetic booking in one short transaction. A partial unique index permits at
most one confirmed replacement for the original reservation within an isolated run even if different
semantic contexts race. Browser, model, network, and object-storage work is never
performed while a database transaction is open.

The application uses a bounded transaction-mode-compatible pool (10 connections,
5 overflow, 10-second acquisition timeout) and a five-second statement timeout.
`runtime_app` cannot access the `oracle` schema; `eval_oracle` is read-only on
runtime/sandbox state and owns only oracle results. RLS is intentionally not
enabled because no client connects directly and this MVP is not multi-tenant. If
either condition changes, indexed session-scoped RLS policies are a release gate.

## Clocks

- `ScenarioClock` controls the synthetic 2030 travel world.
- `SecurityClock` is trusted UTC wall time for grants, sessions, retention, and rate limits.

The two clocks are never interchangeable.
