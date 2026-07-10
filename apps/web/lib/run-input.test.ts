import { describe, expect, it } from "vitest";

import { buildCreateRunRequest } from "./run-input";

describe("live run request boundary", () => {
  it("keeps fault and seed in observer selection, never the actor task contract", () => {
    const request = buildCreateRunRequest({
      maxCost: 450,
      departureAfter: "12:00 PM PT",
      arrivalBy: "8:00 PM PT",
      aisleRequired: true,
      request: "Recover this trip within the reviewed constraints.",
      fault: "F-AMBIGUOUS-COMMIT",
      seed: 1301,
    });
    expect(request.scenario_selection).toEqual({
      scenario_id: "disrupted_trip_v1",
      fault_id: "F-AMBIGUOUS-COMMIT",
      scenario_seed: 1301,
    });
    const actorPayload = JSON.stringify(request.task_contract);
    expect(actorPayload).not.toMatch(/fault|seed|expected_terminal|oracle/i);
    expect(request.task_contract.hard_constraints).toContainEqual({
      field: "additional_cost",
      operator: "less_than_or_equal",
      value: { amount_minor: 45000, currency: "USD" },
    });
  });
});
