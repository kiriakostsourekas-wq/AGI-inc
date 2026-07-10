"use client";

import { useEffect, useReducer } from "react";

import { RuntimeApiClient } from "./runtime-api";
import { fetchRunEventBacklog, subscribeRunEvents, type EventSubscription } from "./runtime-events";
import { initialRunViewState, runViewReducer } from "./run-state";
import { configuredRuntime, readRuntimeSession } from "./runtime-session";

const terminalStates = new Set([
  "SUCCEEDED",
  "PARTIAL_SUCCESS",
  "HANDOFF_REQUIRED",
  "FAILED_OUTCOME_UNKNOWN",
  "SAFE_ABORTED",
  "FAILED",
  "CANCELLED",
]);

export function useRuntimeRun(runId: string) {
  const [state, dispatch] = useReducer(runViewReducer, initialRunViewState);

  useEffect(() => {
    const abort = new AbortController();
    let subscription: EventSubscription | undefined;
    const runtime = configuredRuntime();
    const session = readRuntimeSession();
    if (!runtime.enabled) {
      dispatch({
        type: "failed",
        error: new Error("Live runtime access is disabled for this deployment."),
      });
      return () => abort.abort();
    }
    if (!session) {
      dispatch({
        type: "failed",
        error: new Error("This run is not attached to an active browser session."),
      });
      return () => abort.abort();
    }
    const client = new RuntimeApiClient({ baseUrl: runtime.baseUrl });

    void Promise.all([
      client.getRun(runId, abort.signal),
      fetchRunEventBacklog(client, runId, abort.signal),
    ])
      .then(([run, events]) => {
        if (abort.signal.aborted) return;
        dispatch({ type: "loaded", run, events });
        if (terminalStates.has(run.status)) {
          dispatch({ type: "connection", connection: "closed" });
          return;
        }
        subscription = subscribeRunEvents(client, runId, {
          signal: abort.signal,
          lastEventId: events.at(-1)?.id,
          onState: (connection) => dispatch({ type: "connection", connection }),
          onEvent: (event) => {
            dispatch({ type: "event", event });
            const nextStatus = event.payload.status;
            if (typeof nextStatus === "string" && terminalStates.has(nextStatus))
              subscription?.close();
          },
          onError: () => dispatch({ type: "connection", connection: "failed" }),
        });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted) dispatch({ type: "failed", error });
      });

    return () => {
      abort.abort();
      subscription?.close();
    };
  }, [runId]);

  return state;
}
