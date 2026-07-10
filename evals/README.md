# Evaluation assets

This directory contains predeclared cases, fault manifests, balanced execution plans, evidence mappings, raw executions, and generated reports.

- `cases/` contains safety corpora and case inputs.
- `faults/` contains versioned fault contracts.
- `seeds/` is the source seed commitment.
- `manifests/` contains the deterministic primary and safety execution order.
- `evidence/` maps controls to behavioral test owners; presence is not a green-run claim.
- `results/` is reserved for immutable raw execution artifacts.
- `reports/` is reserved for generated output.

No aggregate report may be hand-authored. Until real executions exist, the reports directory contains only an explicit pending-state document. See `docs/evaluation.md` for validation, accounting, and statistical rules.
