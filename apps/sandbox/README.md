# Synthetic sandbox

This package hosts three fictional browser applications for the disrupted-trip
reference workflow:

- `/gomail` — cancellation and confirmation inbox;
- `/northstar` — replacement search, exact approval, guarded commit;
- `/dayplan` — calendar update guarded by observable booking evidence.

The same routes are rewritten from `gomail.localhost`, `northstar.localhost`,
and `dayplan.localhost` when those hosts are used. All state is in memory and
scoped by the `run` query parameter / `runId` API field.

## Administrative fixture API

`POST /api/sandbox/reset` and `GET /api/sandbox/state?view=oracle` require
`X-Sandbox-Admin-Token`. Development defaults to `local-sandbox-admin`; a
production build requires an explicit `SANDBOX_ADMIN_TOKEN` and
`SANDBOX_APPROVAL_SECRET` (or the shared `APPROVAL_HMAC_SECRET`).

The public state endpoint deliberately omits fault IDs, seeds, grant records,
attempt counters, and secret references. All records and money are synthetic.
