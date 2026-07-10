import { describe, expect, it } from "vitest";

import { RuntimeApiError, type RuntimeEvent, type RuntimeRun } from "./runtime-api";
import { initialRunViewState, presentRunStatus, runViewReducer } from "./run-state";

const run: RuntimeRun = {
  run_id: "run-1",
  session_id: "session-1",
  mode: "protected",
  status: "OBSERVING",
  task_contract: {},
  created_at: "2030-06-13T16:00:00Z",
};

function event(id: string, sequence: number, status?: string): RuntimeEvent {
  return {
    id,
    sequence_no: sequence,
    event_type: "run.state_changed",
    created_at: "2030-06-13T16:00:00Z",
    payload: status ? { status } : {},
  };
}

describe("run console state", () => {
  it("orders and deduplicates resumed stream events while applying authoritative states", () => {
    let state = runViewReducer(initialRunViewState, {
      type: "loaded",
      run,
      events: [event("2", 2), event("1", 1)],
    });
    state = runViewReducer(state, { type: "event", event: event("2", 2) });
    state = runViewReducer(state, { type: "event", event: event("3", 3, "OUTCOME_UNKNOWN") });
    expect(state.events.map((value) => value.id)).toEqual(["1", "2", "3"]);
    expect(state.run?.status).toBe("OUTCOME_UNKNOWN");
  });

  it("represents rate limits and expired runs without fixture substitution", () => {
    const limited = runViewReducer(initialRunViewState, {
      type: "failed",
      error: new RuntimeApiError("Capacity reached.", {
        status: 429,
        code: "RATE_LIMIT",
        retryAfterSeconds: 30,
      }),
    });
    const expired = runViewReducer(initialRunViewState, {
      type: "failed",
      error: new RuntimeApiError("Gone.", { status: 404, code: "RUN_NOT_FOUND" }),
    });
    expect(limited).toMatchObject({ loadState: "rate_limited", retryAfterSeconds: 30 });
    expect(expired.loadState).toBe("expired");
  });

  it("describes unknown and partial outcomes without calling them safe", () => {
    expect(presentRunStatus("FAILED_OUTCOME_UNKNOWN").description).toContain("may have occurred");
    expect(presentRunStatus("PARTIAL_SUCCESS").description).toContain("not a safe abort");
  });
});
