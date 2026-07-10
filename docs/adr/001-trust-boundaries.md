# ADR 001: Trust boundaries and approval lifecycle

- Status: accepted
- Date: 2026-07-09

## Context

The model, browser page, public client, runtime supervisor, sandbox servers, and evaluator have different authority. Treating model-proposed fields or evaluation metadata as trusted would invalidate the project claim.

## Decision

1. The actor receives an immutable `TaskContract` but never `RunManifest`, fault, seed, expected outcome, or oracle data.
2. The model proposes a UI action only. The runtime derives effect class, origin, semantic context, authorization requirement, and idempotency key.
3. A commit proposal is paused. Human approval authorizes the same immutable `effect_proposal_id`.
4. Approval binds an RFC 8785 canonical semantic context, not a screenshot hash.
5. Grant consumption and booking creation are atomic; a separate business uniqueness invariant permits at most one active replacement per original reservation.
6. The runtime verifier uses visible UI evidence. A separately deployed oracle with separate credentials reads sealed ground truth only after termination.
7. `SAFE_ABORTED` proves no side effect. Unknown or partial committed effects use explicit non-safe terminal states.
8. The stored synthetic card removes secret-field entry from the actor surface.

## Consequences

- Additional types and tables are required for effect proposals, approval requests, grants, and side effects.
- Baseline mode must retain the same visible approval opportunity while ablating server binding and recovery components.
- Cross-language canonicalization fixtures are release-critical.
- The oracle cannot be an in-process runtime helper.
