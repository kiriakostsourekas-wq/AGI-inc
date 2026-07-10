import type { EventConnectionState } from "./runtime-events";
import { RuntimeApiError, type RunStatus, type RuntimeEvent, type RuntimeRun } from "./runtime-api";

export type RunLoadState = "loading" | "ready" | "empty" | "expired" | "rate_limited" | "error";

export interface RunViewState {
  loadState: RunLoadState;
  run?: RuntimeRun;
  events: RuntimeEvent[];
  connection: EventConnectionState;
  message?: string;
  retryAfterSeconds?: number;
}

export type RunViewAction =
  | { type: "loaded"; run: RuntimeRun; events: RuntimeEvent[] }
  | { type: "event"; event: RuntimeEvent }
  | { type: "connection"; connection: EventConnectionState }
  | { type: "failed"; error: unknown }
  | { type: "empty" };

export const initialRunViewState: RunViewState = {
  loadState: "loading",
  events: [],
  connection: "connecting",
};

export function runViewReducer(state: RunViewState, action: RunViewAction): RunViewState {
  if (action.type === "loaded") {
    const unique = deduplicateEvents(action.events);
    return { ...state, loadState: "ready", run: action.run, events: unique, message: undefined };
  }
  if (action.type === "event") {
    if (state.events.some((event) => event.id === action.event.id)) return state;
    const nextRun = state.run ? applyEventToRun(state.run, action.event) : undefined;
    return { ...state, run: nextRun, events: [...state.events, action.event].sort(compareEvents) };
  }
  if (action.type === "connection") return { ...state, connection: action.connection };
  if (action.type === "empty")
    return { ...state, loadState: "empty", message: "No run was returned by the runtime." };
  if (action.error instanceof RuntimeApiError) {
    if (action.error.status === 429) {
      return {
        ...state,
        loadState: "rate_limited",
        message: action.error.message,
        retryAfterSeconds: action.error.retryAfterSeconds,
      };
    }
    if (action.error.status === 404 || action.error.code === "RUN_EXPIRED") {
      return {
        ...state,
        loadState: "expired",
        message: "This public run is missing or has expired.",
      };
    }
    return { ...state, loadState: "error", message: action.error.message };
  }
  return { ...state, loadState: "error", message: "The runtime could not be reached." };
}

function compareEvents(left: RuntimeEvent, right: RuntimeEvent): number {
  if (left.sequence_no !== right.sequence_no) return left.sequence_no - right.sequence_no;
  return left.id.localeCompare(right.id);
}

function deduplicateEvents(events: RuntimeEvent[]): RuntimeEvent[] {
  const byId = new Map(events.map((event) => [event.id, event]));
  return [...byId.values()].sort(compareEvents);
}

function applyEventToRun(run: RuntimeRun, event: RuntimeEvent): RuntimeRun {
  const status = event.payload.status;
  if (typeof status === "string" && isRunStatus(status)) {
    return { ...run, status };
  }
  return run;
}

function isRunStatus(value: string): value is RunStatus {
  return [
    "CREATED",
    "ENV_RESET",
    "CONTRACT_VALIDATED",
    "OBSERVING",
    "PLANNING",
    "REPLANNING",
    "ACTION_PROPOSED",
    "POLICY_CHECKING",
    "WAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "RECOVERING",
    "OUTCOME_UNKNOWN",
    "FINALIZING",
    "SUCCEEDED",
    "PARTIAL_SUCCESS",
    "HANDOFF_REQUIRED",
    "FAILED_OUTCOME_UNKNOWN",
    "SAFE_ABORTED",
    "FAILED",
    "CANCELLED",
  ].includes(value);
}

export interface RunStatusPresentation {
  label: string;
  title: string;
  description: string;
  tone: "neutral" | "accent" | "success" | "warning" | "danger";
}

export function presentRunStatus(status: RunStatus): RunStatusPresentation {
  const presentations: Record<RunStatus, RunStatusPresentation> = {
    CREATED: {
      label: "CREATED",
      title: "Run queued",
      description: "The immutable contract has been accepted.",
      tone: "neutral",
    },
    ENV_RESET: {
      label: "ENV_RESET",
      title: "Synthetic apps reset",
      description: "A fresh isolated scenario is being prepared.",
      tone: "accent",
    },
    CONTRACT_VALIDATED: {
      label: "CONTRACT_VALIDATED",
      title: "Contract verified",
      description: "Budgets, constraints, and authority are fixed for this run.",
      tone: "accent",
    },
    OBSERVING: {
      label: "OBSERVING",
      title: "Reading the rendered interface",
      description: "The actor is inspecting a screenshot on an allowlisted origin.",
      tone: "accent",
    },
    PLANNING: {
      label: "PLANNING",
      title: "Planning the next bounded action",
      description: "One externally meaningful action will be proposed.",
      tone: "accent",
    },
    REPLANNING: {
      label: "REPLANNING",
      title: "Replanning from evidence",
      description: "The task contract remains unchanged.",
      tone: "warning",
    },
    ACTION_PROPOSED: {
      label: "ACTION_PROPOSED",
      title: "Action proposed",
      description: "Policy will derive effect and authority from trusted state.",
      tone: "accent",
    },
    POLICY_CHECKING: {
      label: "POLICY_CHECKING",
      title: "Checking authority",
      description: "The runtime—not the model—is classifying the effect.",
      tone: "accent",
    },
    WAITING_APPROVAL: {
      label: "WAITING_APPROVAL",
      title: "Exact approval required",
      description: "Execution is paused on one server-bound semantic proposal.",
      tone: "warning",
    },
    EXECUTING: {
      label: "EXECUTING",
      title: "Executing an authorized action",
      description: "The actor is using the rendered synthetic interface.",
      tone: "accent",
    },
    VERIFYING: {
      label: "VERIFYING",
      title: "Verifying observable state",
      description: "A click or model claim is not sufficient evidence.",
      tone: "accent",
    },
    RECOVERING: {
      label: "RECOVERING",
      title: "Recovering from a classified failure",
      description: "Recovery remains within the immutable contract.",
      tone: "warning",
    },
    OUTCOME_UNKNOWN: {
      label: "OUTCOME_UNKNOWN",
      title: "Commit outcome unknown",
      description: "Commit retry is blocked until external state is verified.",
      tone: "warning",
    },
    FINALIZING: {
      label: "FINALIZING",
      title: "Assembling evidence",
      description: "Terminal claims are checked against verified postconditions.",
      tone: "accent",
    },
    SUCCEEDED: {
      label: "SUCCEEDED",
      title: "Trip recovered and verified",
      description: "The evidence bundle supports every success predicate.",
      tone: "success",
    },
    PARTIAL_SUCCESS: {
      label: "PARTIAL_SUCCESS",
      title: "Booking verified; follow-up incomplete",
      description: "A side effect occurred, so this is not a safe abort.",
      tone: "warning",
    },
    HANDOFF_REQUIRED: {
      label: "HANDOFF_REQUIRED",
      title: "Human handoff required",
      description: "The runtime stopped without making an unsupported success claim.",
      tone: "warning",
    },
    FAILED_OUTCOME_UNKNOWN: {
      label: "FAILED_OUTCOME_UNKNOWN",
      title: "Manual reconciliation required",
      description: "A commit may have occurred and its outcome remains unresolved.",
      tone: "danger",
    },
    SAFE_ABORTED: {
      label: "SAFE_ABORTED",
      title: "Safely aborted",
      description: "Zero irreversible or incomplete side effects were verified.",
      tone: "success",
    },
    FAILED: {
      label: "FAILED",
      title: "Run failed",
      description: "The runtime stopped without claiming task success.",
      tone: "danger",
    },
    CANCELLED: {
      label: "CANCELLED",
      title: "Run cancelled",
      description: "The session owner stopped this run.",
      tone: "neutral",
    },
  };
  return presentations[status];
}
