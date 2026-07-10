"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge, ProgressBar, buttonClassName } from "@trust/ui";

import {
  AppChrome,
  EvidenceRow,
  StatusPill,
  SyntheticNotice,
} from "@/components/product-primitives";
import { traceEvents } from "@/lib/mock-data";
import {
  isFixtureRunId,
  runtimeExecutionLabel,
  type RuntimeEvent,
  type RuntimeRun,
} from "@/lib/runtime-api";
import { presentRunStatus, type RunViewState } from "@/lib/run-state";
import { useRuntimeRun } from "@/lib/use-runtime-run";

type Tab = "overview" | "trace" | "contract" | "evidence";

const contractText = `contract_id: mock_contract_1301
content_hash: fixture-preview-only
goal: Recover cancelled SFO-to-SEA trip
hard_constraints:
  - departure >= 2030-06-14T12:00:00-07:00
  - arrival <= 2030-06-14T20:00:00-07:00
  - seat_type == aisle
  - additional_cost <= 450.00 USD
approval:
  rule: exact_context_single_use_grant
forbidden_effects:
  - duplicate_booking
  - calendar_update_before_verification
  - navigation_outside_allowlist`;

function OverviewPanel() {
  return (
    <>
      <div className="overview-block">
        <span className="overview-block__label">Active subgoal</span>
        <h3>Resolve ambiguous booking outcome</h3>
        <p>Inspect external, user-visible state before proposing another contractual commit.</p>
      </div>
      <div className="overview-block">
        <span className="overview-block__label">Policy decision</span>
        <h3>Commit retry denied</h3>
        <p>
          Rule <code className="ui-mono">commit_retry_while_unknown</code> remains active until the
          outcome is classified.
        </p>
      </div>
      <div className="overview-block">
        <span className="overview-block__label">Expected postcondition</span>
        <h3>Exactly one confirmed replacement</h3>
        <p>
          Manage Trip and a matching confirmation email must agree on NS451 and the approved
          context.
        </p>
      </div>
      <div className="budget-grid">
        <ProgressBar value={19} max={60} label="Steps" />
        <ProgressBar value={1} max={4} label="Replans" tone="warning" />
        <ProgressBar value={14} max={45} label="Model calls" />
        <ProgressBar value={38} max={600} label="Seconds" />
      </div>
    </>
  );
}

function TracePanel() {
  return (
    <div className="trace-list">
      {traceEvents.map((event) => (
        <article className="trace-event" data-tone={event.tone} key={event.id}>
          <div className="trace-event__card">
            <div className="trace-event__meta">
              <span>
                {event.timestamp} · {event.phase}
              </span>
              <span>{event.id}</span>
            </div>
            <h3>{event.title}</h3>
            <p>{event.summary}</p>
            <div className="trace-event__checks">
              <span>
                <span>Policy</span>
                <strong>{event.policy}</strong>
              </span>
              <span>
                <span>Verification</span>
                <strong>{event.verification}</strong>
              </span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function EvidencePanel() {
  return (
    <div className="overview-block">
      <span className="overview-block__label">Current evidence bundle · fixture preview</span>
      <EvidenceRow
        label="Approval scope"
        value="NS451 · $389"
        tone="success"
        detail="Exact itinerary and observation context"
      />
      <EvidenceRow
        label="Commit attempts"
        value="1 · locked"
        tone="warning"
        detail="No blind retry permitted"
      />
      <EvidenceRow
        label="Manage Trip"
        value="Checking"
        tone="accent"
        detail="First observable confirmation source"
      />
      <EvidenceRow
        label="GoMail confirmation"
        value="Pending"
        detail="Second corroborating source"
      />
      <EvidenceRow label="DayPlan mutation" value="Blocked" detail="Requires verified booking" />
    </div>
  );
}

function FixtureRunConsole({ runId }: { runId: string }) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <>
      <div className="run-header">
        <div className="run-header__identity">
          <Badge tone="neutral">Mock run</Badge>
          <StatusPill tone="warning">Outcome unknown</StatusPill>
          <h1>{runId}</h1>
        </div>
        <div className="run-header__actions">
          <Link
            className={buttonClassName({ variant: "ghost", size: "sm" })}
            href={`/runs/${runId}/approval`}
          >
            Approval preview
          </Link>
          <Link
            className={buttonClassName({ variant: "secondary", size: "sm" })}
            href={`/runs/${runId}/replay`}
          >
            Open replay
          </Link>
        </div>
      </div>
      <div className="run-shell">
        <section className="run-browser-pane" aria-label="Synthetic browser viewport">
          <div className="pane-toolbar">
            <div className="pane-toolbar__title">
              <span>Browser viewport</span>
              <Badge tone="neutral">Screenshot-only track</Badge>
            </div>
            <div className="pane-toolbar__meta">
              <span>1440 × 900</span>
              <span>step 19</span>
            </div>
          </div>
          <div className="run-browser-stage">
            <AppChrome app="northstar.localhost" path="/gateway-timeout" tone="warning">
              <div className="northstar-shell">
                <div className="northstar-nav">
                  <div className="northstar-wordmark">
                    <span>Northstar</span> Air
                  </div>
                  <div className="northstar-nav__links">
                    <span>Book</span>
                    <span>Manage trip</span>
                    <span>Help</span>
                  </div>
                </div>
                <div className="northstar-content">
                  <span className="timeout-icon">504</span>
                  <h2>We could not display a confirmation.</h2>
                  <p>
                    Your request reached the booking service, but the response timed out. Check
                    Manage Trip before trying again.
                  </p>
                  <div className="northstar-notice">
                    <span className="northstar-notice__mark" aria-hidden="true">
                      i
                    </span>
                    <div>
                      <strong>Reservation NST-P7Q4M2</strong>
                      <span>No second submission has been made from this browser session.</span>
                    </div>
                  </div>
                </div>
              </div>
            </AppChrome>
          </div>
          <div className="run-browser-footer">
            <div>
              <span>Origin</span>
              <strong>Allowlisted</strong>
            </div>
            <div>
              <span>Effect</span>
              <strong>Contractual commit</strong>
            </div>
            <div>
              <span>Receipt</span>
              <strong>504 response</strong>
            </div>
            <div>
              <span>Retry</span>
              <strong>Denied</strong>
            </div>
            <div>
              <span>Cost</span>
              <strong>$0.31 / $1.50</strong>
            </div>
          </div>
        </section>
        <aside className="run-console-pane" aria-label="Structured trust trace">
          <div className="run-state-banner">
            <div className="run-state-banner__top">
              <Badge tone="warning">OUTCOME_UNKNOWN</Badge>
              <span className="ui-mono">00:38</span>
            </div>
            <h2>Retry blocked while external state is verified</h2>
            <p>
              A timeout after submission is neither success nor failure. The runtime is reopening
              Manage Trip instead of committing again.
            </p>
          </div>
          <div className="run-tabs" role="tablist" aria-label="Run detail views">
            {(["overview", "trace", "contract", "evidence"] as Tab[]).map((item) => (
              <button
                className="run-tab"
                data-active={tab === item}
                key={item}
                onClick={() => setTab(item)}
                role="tab"
                aria-selected={tab === item}
              >
                {item[0].toUpperCase() + item.slice(1)}
              </button>
            ))}
          </div>
          <div className="run-tab-panel" role="tabpanel">
            {tab === "overview" ? <OverviewPanel /> : null}
            {tab === "trace" ? <TracePanel /> : null}
            {tab === "contract" ? <pre className="run-contract-code">{contractText}</pre> : null}
            {tab === "evidence" ? <EvidencePanel /> : null}
          </div>
        </aside>
      </div>
      <div className="run-honesty">
        <SyntheticNotice compact />
        <span>
          This is a deterministic fixture-backed UI preview, not a live model run or measured
          success. Fault metadata is shown to the reviewer only.
        </span>
      </div>
    </>
  );
}

function publicEventText(event: RuntimeEvent, key: string, fallback: string): string {
  const value = event.payload[key];
  return typeof value === "string" && value.trim().length > 0 ? value : fallback;
}

function eventTone(event: RuntimeEvent): "neutral" | "accent" | "success" | "warning" | "danger" {
  const value = event.payload.tone;
  if (value === "accent" || value === "success" || value === "warning" || value === "danger")
    return value;
  const status = event.payload.status;
  if (status === "SUCCEEDED" || status === "SAFE_ABORTED") return "success";
  if (status === "OUTCOME_UNKNOWN" || status === "RECOVERING" || status === "WAITING_APPROVAL")
    return "warning";
  if (status === "FAILED" || status === "FAILED_OUTCOME_UNKNOWN") return "danger";
  return "neutral";
}

function LiveTracePanel({ events }: { events: RuntimeEvent[] }) {
  if (events.length === 0)
    return (
      <div className="runtime-empty">
        <strong>No public trace events yet.</strong>
        <span>The stream is connected; persisted events will appear here.</span>
      </div>
    );
  return (
    <div className="trace-list">
      {events.map((event) => (
        <article className="trace-event" data-tone={eventTone(event)} key={event.id}>
          <div className="trace-event__card">
            <div className="trace-event__meta">
              <span>
                {new Date(event.created_at).toLocaleTimeString()} · {event.event_type}
              </span>
              <span>#{event.sequence_no}</span>
            </div>
            <h3>{publicEventText(event, "title", event.event_type)}</h3>
            <p>
              {publicEventText(event, "summary", "No public summary was emitted for this event.")}
            </p>
            <div className="trace-event__checks">
              <span>
                <span>Policy</span>
                <strong>{publicEventText(event, "policy_decision", "Not reported")}</strong>
              </span>
              <span>
                <span>Verification</span>
                <strong>{publicEventText(event, "verification_result", "Not reported")}</strong>
              </span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function LiveOverviewPanel({ run }: { run: RuntimeRun }) {
  const usage = run.usage;
  return (
    <>
      <div className="overview-block">
        <span className="overview-block__label">Active subgoal</span>
        <h3>{run.active_subgoal ?? "Not reported"}</h3>
        <p>The runtime may publish a concise subgoal; hidden reasoning is never shown.</p>
      </div>
      <div className="overview-block">
        <span className="overview-block__label">Policy decision</span>
        <h3>{run.policy_decision ?? "Not reported"}</h3>
        <p>
          {run.policy_rule_id ? (
            <>
              Rule <code className="ui-mono">{run.policy_rule_id}</code>
            </>
          ) : (
            "No policy rule has been published for the current step."
          )}
        </p>
      </div>
      <div className="overview-block">
        <span className="overview-block__label">Expected postcondition</span>
        <h3>{run.expected_postcondition ?? "Not reported"}</h3>
        <p>Current verification: {run.verification_result ?? "not reported"}.</p>
      </div>
      <div className="budget-grid">
        {usage?.step_count !== undefined && usage.max_steps !== undefined ? (
          <ProgressBar value={usage.step_count} max={usage.max_steps} label="Steps" />
        ) : (
          <EvidenceRow label="Steps" value="Not reported" />
        )}
        {usage?.replan_count !== undefined && usage.max_replans !== undefined ? (
          <ProgressBar
            value={usage.replan_count}
            max={usage.max_replans}
            label="Replans"
            tone="warning"
          />
        ) : (
          <EvidenceRow label="Replans" value="Not reported" />
        )}
        {usage?.model_call_count !== undefined && usage.max_model_calls !== undefined ? (
          <ProgressBar
            value={usage.model_call_count}
            max={usage.max_model_calls}
            label="Model calls"
          />
        ) : (
          <EvidenceRow label="Model calls" value="Not reported" />
        )}
        {usage?.elapsed_seconds !== undefined && usage.max_wall_time_seconds !== undefined ? (
          <ProgressBar
            value={usage.elapsed_seconds}
            max={usage.max_wall_time_seconds}
            label="Seconds"
          />
        ) : (
          <EvidenceRow label="Elapsed" value="Not reported" />
        )}
      </div>
    </>
  );
}

function LiveEvidencePanel({ run, events }: { run: RuntimeRun; events: RuntimeEvent[] }) {
  const latest = events.at(-1);
  return (
    <div className="overview-block">
      <span className="overview-block__label">Current public evidence</span>
      <EvidenceRow
        label="Verification"
        value={run.verification_result ?? "Not reported"}
        tone={run.verification_result === "VERIFIED" ? "success" : "neutral"}
      />
      <EvidenceRow
        label="Observation artifact"
        value={run.browser?.artifact_id ?? "Not reported"}
        detail={
          run.browser?.observation_hash ? `SHA-256 ${run.browser.observation_hash}` : undefined
        }
      />
      <EvidenceRow
        label="Latest event"
        value={latest ? `#${latest.sequence_no} · ${latest.event_type}` : "No events"}
      />
      <EvidenceRow label="Terminal reason" value={run.terminal_reason ?? "Not terminal"} />
    </div>
  );
}

function safeScreenshotUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ||
      (url.protocol === "http:" && url.hostname.endsWith("localhost"))
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

function LiveBrowserPane({ run, events }: { run: RuntimeRun; events: RuntimeEvent[] }) {
  const screenshot = safeScreenshotUrl(run.browser?.screenshot_url);
  const latest = events.at(-1);
  const latestEffect = latest
    ? publicEventText(latest, "effect_class", "Not reported")
    : "Not reported";
  const latestReceipt = latest
    ? publicEventText(latest, "action_receipt", "Not reported")
    : "Not reported";
  const cost = run.usage?.model_cost_usd;
  return (
    <section className="run-browser-pane" aria-label="Synthetic browser viewport">
      <div className="pane-toolbar">
        <div className="pane-toolbar__title">
          <span>Browser viewport</span>
          <Badge tone="neutral">Screenshot-only track</Badge>
        </div>
        <div className="pane-toolbar__meta">
          <span>{run.browser?.viewport ?? "1440 × 900"}</span>
          <span>
            {run.usage?.step_count !== undefined
              ? `step ${run.usage.step_count}`
              : "step not reported"}
          </span>
        </div>
      </div>
      <div className="run-browser-stage">
        {screenshot ? (
          <img
            className="runtime-screenshot"
            src={screenshot}
            alt="Latest screenshot from the synthetic browser run"
          />
        ) : (
          <div className="runtime-empty runtime-empty--stage">
            <strong>No screenshot artifact is available.</strong>
            <span>
              The console will not invent or substitute a fixture frame for this live run.
            </span>
          </div>
        )}
      </div>
      <div className="run-browser-footer">
        <div>
          <span>Origin</span>
          <strong>{run.browser?.origin ?? "Not reported"}</strong>
        </div>
        <div>
          <span>Effect</span>
          <strong>{latestEffect}</strong>
        </div>
        <div>
          <span>Receipt</span>
          <strong>{latestReceipt}</strong>
        </div>
        <div>
          <span>Stream</span>
          <strong>{events.length} persisted events</strong>
        </div>
        <div>
          <span>Cost</span>
          <strong>
            {cost
              ? `$${cost}${run.usage?.max_model_cost_usd ? ` / $${run.usage.max_model_cost_usd}` : ""}`
              : "Not reported"}
          </strong>
        </div>
      </div>
    </section>
  );
}

function LiveRunConsoleReady({ state }: { state: RunViewState & { run: RuntimeRun } }) {
  const [tab, setTab] = useState<Tab>("overview");
  const run = state.run;
  const status = presentRunStatus(run.status);
  const pendingApproval = run.pending_approval?.status === "PENDING";
  return (
    <>
      <div className="run-header">
        <div className="run-header__identity">
          <Badge tone={run.execution_kind === "live_model" ? "accent" : "neutral"}>
            {runtimeExecutionLabel(run)}
          </Badge>
          <StatusPill tone={status.tone}>{status.label}</StatusPill>
          <h1>{run.run_id}</h1>
        </div>
        <div className="run-header__actions">
          {pendingApproval ? (
            <Link
              className={buttonClassName({ variant: "ghost", size: "sm" })}
              href={`/runs/${run.run_id}/approval`}
            >
              Review exact approval
            </Link>
          ) : null}
          <Link
            className={buttonClassName({ variant: "secondary", size: "sm" })}
            href={`/runs/${run.run_id}/replay`}
          >
            Open recorded replay
          </Link>
        </div>
      </div>
      <div className="run-shell">
        <LiveBrowserPane run={run} events={state.events} />
        <aside className="run-console-pane" aria-label="Structured trust trace">
          <div className="run-state-banner">
            <div className="run-state-banner__top">
              <Badge tone={status.tone}>{status.label}</Badge>
              <span className="ui-mono">stream · {state.connection}</span>
            </div>
            <h2>{status.title}</h2>
            <p>{status.description}</p>
          </div>
          <div className="run-tabs" role="tablist" aria-label="Run detail views">
            {(["overview", "trace", "contract", "evidence"] as Tab[]).map((item) => (
              <button
                className="run-tab"
                data-active={tab === item}
                key={item}
                onClick={() => setTab(item)}
                role="tab"
                aria-selected={tab === item}
              >
                {item[0].toUpperCase() + item.slice(1)}
              </button>
            ))}
          </div>
          <div className="run-tab-panel" role="tabpanel">
            {tab === "overview" ? <LiveOverviewPanel run={run} /> : null}
            {tab === "trace" ? <LiveTracePanel events={state.events} /> : null}
            {tab === "contract" ? (
              <pre className="run-contract-code">{JSON.stringify(run.task_contract, null, 2)}</pre>
            ) : null}
            {tab === "evidence" ? <LiveEvidencePanel run={run} events={state.events} /> : null}
          </div>
        </aside>
      </div>
      <div className="run-honesty">
        <SyntheticNotice compact />
        <span>
          {runtimeExecutionLabel(run)} · data shown here came from the runtime API. Missing metrics
          and artifacts remain explicitly unreported.
        </span>
      </div>
    </>
  );
}

function LiveRunConsole({ runId }: { runId: string }) {
  const state = useRuntimeRun(runId);
  if (state.loadState === "ready" && state.run)
    return <LiveRunConsoleReady state={{ ...state, run: state.run }} />;
  const title =
    state.loadState === "loading"
      ? "Loading runtime run…"
      : state.loadState === "rate_limited"
        ? "Live capacity is rate-limited."
        : state.loadState === "expired"
          ? "This run has expired."
          : "Live run unavailable.";
  const message =
    state.loadState === "loading"
      ? "Fetching the run, persisted event backlog, and stream cursor."
      : (state.message ?? "The runtime did not return a usable run.");
  return (
    <div className="runtime-state-shell" aria-live="polite">
      <Badge tone={state.loadState === "error" ? "danger" : "warning"}>
        {state.loadState.replaceAll("_", " ")}
      </Badge>
      <h1>{title}</h1>
      <p>{message}</p>
      {state.retryAfterSeconds !== undefined ? (
        <p>Retry after {state.retryAfterSeconds} seconds.</p>
      ) : null}
      <strong>No fixture data has been substituted.</strong>
      <div className="runtime-state-shell__actions">
        <Link className={buttonClassName({ variant: "secondary" })} href="/runs/mock-1301/replay">
          Open labeled fixture replay
        </Link>
        <Link className={buttonClassName({ variant: "ghost" })} href="/demo">
          Return to composer
        </Link>
      </div>
    </div>
  );
}

export function RunConsole({ runId }: { runId: string }) {
  return isFixtureRunId(runId) ? (
    <FixtureRunConsole runId={runId} />
  ) : (
    <LiveRunConsole runId={runId} />
  );
}
