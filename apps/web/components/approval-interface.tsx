"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Panel, buttonClassName } from "@trust/ui";

import { constraintRows } from "@/lib/mock-data";
import {
  isFixtureRunId,
  RuntimeApiClient,
  RuntimeApiError,
  type ApprovalDecisionResponse,
  type PublicApprovalRequest,
} from "@/lib/runtime-api";
import { configuredRuntime, readRuntimeSession } from "@/lib/runtime-session";

type ApprovalState = "pending" | "approved" | "rejected" | "expired";

function FixtureApprovalInterface({ runId }: { runId: string }) {
  const [state, setState] = useState<ApprovalState>("pending");
  const [remaining, setRemaining] = useState(180);

  useEffect(() => {
    if (state !== "pending") return;
    const timer = window.setInterval(
      () =>
        setRemaining((value) => {
          if (value <= 1) {
            window.clearInterval(timer);
            setState("expired");
            return 0;
          }
          return value - 1;
        }),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [state]);

  const minutes = Math.floor(remaining / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (remaining % 60).toString().padStart(2, "0");

  if (state !== "pending") {
    const approved = state === "approved";
    return (
      <Panel className="approval-card">
        <div className="approval-result">
          <span className="approval-result__icon" aria-hidden="true">
            {approved ? "✓" : "×"}
          </span>
          <Badge tone={approved ? "success" : "warning"}>Mock interaction</Badge>
          <h2>
            {approved
              ? "Approval preview accepted"
              : state === "expired"
                ? "Approval preview expired"
                : "Approval preview rejected"}
          </h2>
          <p>
            {approved
              ? "No server capability or booking was created. In the implemented runtime, this exact context would be signed server-side and consumed once."
              : "No capability was minted and no side effect occurred."}
          </p>
          <Link className={buttonClassName({ variant: "secondary" })} href={`/runs/${runId}`}>
            Return to run console
          </Link>
        </div>
      </Panel>
    );
  }

  return (
    <Panel className="approval-card">
      <div className="approval-card__top">
        <span>Approval scope preview · no capability minted</span>
        <Badge tone="warning">
          UI TTL {minutes}:{seconds}
        </Badge>
      </div>
      <div className="approval-card__body">
        <div className="approval-route">
          <div className="approval-airport">
            <strong>SFO</strong>
            <span>2:10 PM</span>
            <small>Jun 14 · PT</small>
          </div>
          <div className="approval-route__flight">
            <span>NS451 · Nonstop</span>
            <span className="approval-route__line" aria-hidden="true" />
            <span>2h 05m</span>
          </div>
          <div className="approval-airport">
            <strong>SEA</strong>
            <span>4:15 PM</span>
            <small>Jun 14 · PT</small>
          </div>
        </div>
        <div className="approval-detail-grid">
          <div className="approval-detail">
            <span>Cabin</span>
            <strong>Economy</strong>
          </div>
          <div className="approval-detail">
            <span>Seat</span>
            <strong>12D · Aisle</strong>
          </div>
          <div className="approval-detail">
            <span>Passenger</span>
            <strong>Maya Chen</strong>
          </div>
          <div className="approval-detail">
            <span>Stops</span>
            <strong>Nonstop</strong>
          </div>
        </div>
        <div className="approval-price">
          <div>
            <span>Exact additional cost</span>
            <strong>$389.00 USD</strong>
          </div>
          <small>Includes synthetic taxes and fees shown at confirmation.</small>
        </div>
        <div className="approval-constraints">
          <h3>Contract check</h3>
          {constraintRows.map((row) => (
            <div className="constraint-pass" key={row.label}>
              <span>{row.label}</span>
              <strong>{row.value}</strong>
              <small>{row.status === "Fixed" ? "FIXED" : "PASS"}</small>
            </div>
          ))}
        </div>
        <div className="approval-effect">
          <h3>What happens after approval</h3>
          <p>
            One synthetic rebooking may be committed through Northstar’s visible confirmation
            control. The runtime must then verify the booking from two observable sources before it
            can update the existing DayPlan block once.
          </p>
        </div>
        <details className="approval-binding">
          <summary>Technical binding</summary>
          <div className="approval-binding__content">
            <div>
              <span>Contract hash</span>
              <code>c8e4…3a71</code>
            </div>
            <div>
              <span>Observation hash</span>
              <code>18b1…0cc4</code>
            </div>
            <div>
              <span>Context hash</span>
              <code>ad91…86ef</code>
            </div>
            <div>
              <span>Idempotency key</span>
              <code>preview-only</code>
            </div>
          </div>
        </details>
        <p className="approval-binding-copy">
          Any material change to flight, route, time, seat, traveler, price, currency, contract, or
          observed page invalidates this approval.
        </p>
        <div className="approval-card__actions">
          <Button size="lg" onClick={() => setState("approved")}>
            Approve NS451 · $389 additional
          </Button>
          <Button variant="ghost" size="lg" onClick={() => setState("rejected")}>
            Reject
          </Button>
        </div>
      </div>
    </Panel>
  );
}

type LiveApprovalView =
  | { kind: "loading" }
  | { kind: "empty"; message: string }
  | { kind: "pending"; approval: PublicApprovalRequest }
  | { kind: "submitting"; approval: PublicApprovalRequest; decision: "approve" | "reject" }
  | { kind: "decided"; approval: PublicApprovalRequest; result: ApprovalDecisionResponse }
  | { kind: "stale"; message: string }
  | { kind: "error"; message: string };

function formatScenarioDate(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
    timeZoneName: "short",
  }).format(parsed);
}

function formatMoney(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100);
}

function LiveApprovalCard({ runId }: { runId: string }) {
  const [view, setView] = useState<LiveApprovalView>({ kind: "loading" });
  const [remaining, setRemaining] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    const runtime = configuredRuntime();
    const session = readRuntimeSession();
    if (!runtime.enabled || !session) {
      setView({
        kind: "error",
        message: "An active live runtime session is required. No fixture approval was substituted.",
      });
      return () => abort.abort();
    }
    const client = new RuntimeApiClient({ baseUrl: runtime.baseUrl });
    void client
      .getRun(runId, abort.signal)
      .then((run) => {
        const approval = run.pending_approval;
        if (!approval) {
          setView({ kind: "empty", message: "This run has no pending approval request." });
          return;
        }
        if (approval.run_id !== run.run_id) {
          setView({
            kind: "error",
            message: "The approval did not match this run and was not displayed.",
          });
          return;
        }
        if (approval.status !== "PENDING") {
          setView({
            kind: "empty",
            message: `This approval is already ${approval.status.toLowerCase()}.`,
          });
          return;
        }
        setRemaining(
          Math.max(0, Math.floor((Date.parse(approval.expires_at) - Date.now()) / 1_000)),
        );
        setView({ kind: "pending", approval });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setView({
            kind: "error",
            message: error instanceof Error ? error.message : "The approval could not be loaded.",
          });
      });
    return () => abort.abort();
  }, [runId]);

  useEffect(() => {
    if (view.kind !== "pending" && view.kind !== "submitting") return;
    const timer = window.setInterval(() => setRemaining((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearInterval(timer);
  }, [view.kind]);

  async function submit(approval: PublicApprovalRequest, decision: "approve" | "reject") {
    const runtime = configuredRuntime();
    const session = readRuntimeSession();
    if (!session) {
      setView({
        kind: "error",
        message: "The demo session expired before the decision was submitted.",
      });
      return;
    }
    setView({ kind: "submitting", approval, decision });
    try {
      const client = new RuntimeApiClient({ baseUrl: runtime.baseUrl });
      const result = await client.decideApproval(
        approval.approval_id,
        approval.approved_context_hash,
        decision,
      );
      if (result.approval_id !== approval.approval_id || result.run_id !== runId) {
        setView({
          kind: "error",
          message: "The runtime decision response did not match the displayed approval.",
        });
        return;
      }
      setView({ kind: "decided", approval, result });
    } catch (error: unknown) {
      if (
        error instanceof RuntimeApiError &&
        (error.code === "APPROVAL_STALE" ||
          error.code === "APPROVAL_EXPIRED" ||
          error.status === 412)
      ) {
        setView({
          kind: "stale",
          message:
            "The server rejected this decision because its exact semantic context is stale or expired. No capability was minted.",
        });
      } else {
        setView({
          kind: "error",
          message: error instanceof Error ? error.message : "The decision could not be submitted.",
        });
      }
    }
  }

  if (
    view.kind === "loading" ||
    view.kind === "empty" ||
    view.kind === "stale" ||
    view.kind === "error"
  ) {
    const title =
      view.kind === "loading"
        ? "Loading exact approval…"
        : view.kind === "stale"
          ? "Approval stale"
          : view.kind === "empty"
            ? "No approval waiting"
            : "Approval unavailable";
    const message =
      view.kind === "loading" ? "Fetching the server-created semantic scope." : view.message;
    return (
      <Panel className="approval-card">
        <div className="approval-result">
          <Badge tone={view.kind === "error" ? "danger" : "warning"}>{view.kind}</Badge>
          <h2>{title}</h2>
          <p>{message}</p>
          <strong>No client-side capability or fixture decision was created.</strong>
          <Link className={buttonClassName({ variant: "secondary" })} href={`/runs/${runId}`}>
            Return to run console
          </Link>
        </div>
      </Panel>
    );
  }

  if (view.kind === "decided") {
    const approved = view.result.status === "APPROVED";
    return (
      <Panel className="approval-card">
        <div className="approval-result">
          <span className="approval-result__icon" aria-hidden="true">
            {approved ? "✓" : "×"}
          </span>
          <Badge tone={approved ? "success" : "warning"}>
            Server decision · {view.result.status}
          </Badge>
          <h2>{approved ? "Exact proposal approved" : "Proposal rejected"}</h2>
          <p>
            {approved
              ? "The runtime accepted this decision and may resume only the paused action. Any signed capability remains sealed server-side and is never returned to this browser."
              : "No capability was minted and the paused action may not execute."}
          </p>
          <Link className={buttonClassName({ variant: "secondary" })} href={`/runs/${runId}`}>
            Return to run console
          </Link>
        </div>
      </Panel>
    );
  }

  const approval = view.approval;
  const scope = approval.scope;
  const minutes = Math.floor(remaining / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (remaining % 60).toString().padStart(2, "0");
  const expired = remaining <= 0;
  const submitting = view.kind === "submitting";
  return (
    <Panel className="approval-card">
      <div className="approval-card__top">
        <span>Server-created exact semantic scope</span>
        <Badge tone={expired ? "danger" : "warning"}>
          Security TTL {minutes}:{seconds}
        </Badge>
      </div>
      <div className="approval-card__body">
        <div className="approval-route">
          <div className="approval-airport">
            <strong>{scope.origin_airport}</strong>
            <span>{formatScenarioDate(scope.departure)}</span>
            <small>{scope.marketing_carrier}</small>
          </div>
          <div className="approval-route__flight">
            <span>
              {scope.flight_id} ·{" "}
              {scope.stop_count === 0
                ? "Nonstop"
                : `${scope.stop_count} stop${scope.stop_count === 1 ? "" : "s"}`}
            </span>
            <span className="approval-route__line" aria-hidden="true" />
            <span>{scope.operating_carrier}</span>
          </div>
          <div className="approval-airport">
            <strong>{scope.destination_airport}</strong>
            <span>{formatScenarioDate(scope.arrival)}</span>
            <small>Pacific time</small>
          </div>
        </div>
        <div className="approval-detail-grid">
          <div className="approval-detail">
            <span>Cabin</span>
            <strong>{scope.cabin}</strong>
          </div>
          <div className="approval-detail">
            <span>Seat</span>
            <strong>
              {scope.seat ? `${scope.seat} · ` : ""}
              {scope.seat_type}
            </strong>
          </div>
          <div className="approval-detail">
            <span>Passenger</span>
            <strong>{scope.traveler_display_name}</strong>
          </div>
          <div className="approval-detail">
            <span>Fare class</span>
            <strong>{scope.fare_class}</strong>
          </div>
        </div>
        <div className="approval-price">
          <div>
            <span>Exact additional cost</span>
            <strong>{formatMoney(scope.total_additional_cost_minor, scope.currency)}</strong>
          </div>
          <small>Fee-inclusive synthetic amount supplied by the runtime.</small>
        </div>
        <div className="approval-constraints">
          <h3>Contract check</h3>
          {scope.constraints.map((constraint) => (
            <div className="constraint-pass" key={constraint.label}>
              <span>{constraint.label}</span>
              <strong>{constraint.value}</strong>
              <small>{constraint.satisfied ? "PASS" : "FAIL"}</small>
            </div>
          ))}
        </div>
        <div className="approval-effect">
          <h3>What happens immediately after approval</h3>
          <p>{scope.immediate_effect}</p>
        </div>
        <details className="approval-binding">
          <summary>Technical binding</summary>
          <div className="approval-binding__content">
            <div>
              <span>Approval ID</span>
              <code>{approval.approval_id}</code>
            </div>
            <div>
              <span>Semantic context hash</span>
              <code>{approval.approved_context_hash}</code>
            </div>
            <div>
              <span>Requested</span>
              <code>{approval.requested_at}</code>
            </div>
            <div>
              <span>Expires</span>
              <code>{approval.expires_at}</code>
            </div>
          </div>
        </details>
        <p className="approval-binding-copy">
          Any material change to flight, route, times, stops, cabin, fare, seat, traveler,
          fee-inclusive price, currency, reservation, or contract invalidates this approval. The
          server rechecks the scope; this browser cannot sign or mint a grant.
        </p>
        <div className="approval-card__actions">
          <Button
            size="lg"
            disabled={expired || submitting}
            onClick={() => void submit(approval, "approve")}
          >
            {submitting && view.decision === "approve"
              ? "Submitting exact decision…"
              : expired
                ? "Approval expired"
                : `Approve ${scope.flight_id} · ${formatMoney(scope.total_additional_cost_minor, scope.currency)}`}
          </Button>
          <Button
            variant="ghost"
            size="lg"
            disabled={submitting}
            onClick={() => void submit(approval, "reject")}
          >
            {submitting && view.decision === "reject" ? "Rejecting…" : "Reject"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}

export function ApprovalInterface({ runId }: { runId: string }) {
  return isFixtureRunId(runId) ? (
    <FixtureApprovalInterface runId={runId} />
  ) : (
    <LiveApprovalCard runId={runId} />
  );
}
