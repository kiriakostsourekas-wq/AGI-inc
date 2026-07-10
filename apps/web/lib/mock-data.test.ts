import { describe, expect, it } from "vitest";

import { buildTaskRequest, faultOptions, replayFrames, traceEvents } from "./mock-data";

describe("synthetic product fixtures", () => {
  it("publishes every required fault and safety case", () => {
    expect(faultOptions.map((fault) => fault.id)).toEqual(
      expect.arrayContaining([
        "F-UI-DRIFT",
        "F-PRICE-DRIFT",
        "F-AMBIGUOUS-COMMIT",
        "S-PROMPT-INJECTION",
        "S-NO-COMPLIANT-OPTION",
      ]),
    );
  });

  it("shows outcome-unknown before external verification", () => {
    const unknownIndex = traceEvents.findIndex((event) => event.phase === "OUTCOME_UNKNOWN");
    const verifiedIndex = traceEvents.findIndex((event) => event.verification === "VERIFIED");
    expect(unknownIndex).toBeGreaterThanOrEqual(0);
    expect(verifiedIndex).toBeGreaterThan(unknownIndex);
  });

  it("updates the calendar only in the final replay frame", () => {
    expect(replayFrames.at(-1)?.app).toBe("DayPlan");
    expect(replayFrames.at(-1)?.evidence).toContain("1 booking");
  });

  it("compiles structured constraints into an auditable request", () => {
    const request = buildTaskRequest({
      maxCost: 425,
      departureAfter: "1:00 PM PT",
      arrivalBy: "7:30 PM PT",
      aisleRequired: true,
    });
    expect(request).toContain("$425");
    expect(request).toContain("require an aisle seat");
    expect(request).toContain("only after the booking is confirmed");
  });
});
