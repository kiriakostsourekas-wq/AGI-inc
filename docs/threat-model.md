# Threat model

Status: living release document. Every control requires a direct negative test before launch.

## Assets

- immutable user task contract;
- authority to create one synthetic rebooking and one post-verification calendar update;
- approval grants and stable idempotency keys;
- model-provider and infrastructure credentials;
- trace integrity and benchmark evidence;
- isolation between actor-visible state and sealed evaluation state.

## Trust boundaries

1. Browser page content is untrusted data.
2. Model output is an untrusted proposal.
3. The policy/approval subsystem is security authoritative.
4. Sandbox application servers enforce commit and calendar invariants.
5. The oracle is privileged but post-termination only.
6. The public UI is not trusted to label run provenance or authorize effects.

## Threats and required controls

| Threat                             | Required control                                                          | Required evidence                                        |
| ---------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------- |
| Prompt injection expands authority | actor contract and tool registry immutable; page content marked untrusted | five seeded attacks plus captured final model request    |
| Model labels booking as read       | runtime derives effect class from trusted UI registry                     | negative policy test                                     |
| Approval scope changes             | RFC 8785 semantic context hash and server validation                      | in-budget and over-budget drift tests                    |
| Approval replay or double submit   | single-use grant, row lock, atomic consume and commit                     | concurrent gateway plus rendered durable-browser probe   |
| Duplicate booking after 504        | stable effect key plus one-active-replacement invariant                   | ambiguous-commit suite                                   |
| Unknown commit called safe abort   | explicit `FAILED_OUTCOME_UNKNOWN`/handoff states                          | state-machine tests                                      |
| Actor reads fault/oracle metadata  | separate context types, credentials, and processes                        | captured OpenAI payload and real Chromium denial test    |
| Secret leaks                       | stored synthetic payment method; no secret entry tool                     | repository, payload, artifact, and browser-storage scans |
| Lookalike redirect                 | context-wide request guard plus post-navigation origin check              | unit redirect/click tests and real Chromium denial test  |
| Cross-run contamination            | fresh browser context, fixture namespace, cookies, grants                 | concurrent isolation test                                |
| Fabricated metrics                 | reports generated only from immutable raw executions                      | report regeneration hash test                            |

The protected rendered commit path is fail-closed: Northstar receives only a runtime grant identifier through its admin-authenticated approval binding, calls the token-protected internal gateway as a consequence of the rendered click, and materializes UI state only after PostgreSQL atomically consumes the grant and creates the booking and side-effect rows. Production sandbox commits require this gateway by default.

## Explicit non-claims

This MVP is synthetic, narrow, and not production-ready, universal, private, on-device, or affiliated with AGI, Inc.
