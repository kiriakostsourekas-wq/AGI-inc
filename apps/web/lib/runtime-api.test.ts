import { describe, expect, it, vi } from "vitest";

import { RuntimeApiClient, RuntimeApiError, type CreateRunRequest } from "./runtime-api";

const hash = "a".repeat(64);

function jsonResponse(value: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function runResponse() {
  return {
    run_id: "01900000-0000-7000-8000-000000000001",
    session_id: "01900000-0000-7000-8000-000000000002",
    mode: "protected",
    status: "CREATED",
    task_contract: { schema_version: "1.0.0", goal: "Recover the trip." },
    created_at: "2030-06-13T16:00:00Z",
  };
}

describe("RuntimeApiClient", () => {
  it("uses authenticated, idempotent mutation requests without putting the session in the URL", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(runResponse(), 201));
    const client = new RuntimeApiClient({
      baseUrl: "https://runtime.example.test/",
      sessionToken: "session-token-at-least-16",
      fetcher,
      idempotencyKeyFactory: () => "idem-run-0001",
    });
    const request = {
      task_contract: {},
      scenario_selection: {},
      mode: "protected",
    } as unknown as CreateRunRequest;
    await client.createRun(request);

    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("https://runtime.example.test/v1/runs");
    expect(String(url)).not.toContain("session-token");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("idem-run-0001");
    expect(new Headers(init?.headers).get("X-Trust-CSRF")).toBe("1");
    expect(new Headers(init?.headers).get("X-Demo-Session-Token")).toBe(
      "session-token-at-least-16",
    );
    expect(init?.credentials).toBe("include");
  });

  it("fetches status, event backlog, and a strictly labeled recorded replay", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(runResponse()))
      .mockResolvedValueOnce(
        jsonResponse({
          events: [
            {
              id: "e1",
              sequence_no: 1,
              event_type: "run.created",
              created_at: "2030-06-13T16:00:00Z",
              payload: {},
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          run_id: "01900000-0000-7000-8000-000000000001",
          label: "Recorded replay",
          recorded_at: "2030-06-13T16:10:00Z",
          source_execution_kind: "live_model",
          frames: [
            {
              id: "f1",
              sequence_no: 1,
              chapter: "Observe",
              app: "GoMail",
              path: "/inbox",
              status: "Observed",
              title: "Cancellation visible",
              description: "Rendered notice.",
              evidence: "Screenshot hash abc",
              tone: "accent",
            },
          ],
        }),
      );
    const client = new RuntimeApiClient({ baseUrl: "/api/runtime", fetcher });
    const run = await client.getRun("run-1");
    const events = await client.getRunEvents("run-1");
    const replay = await client.getRunReplay("run-1");
    expect(run.status).toBe("CREATED");
    expect(events.map((value) => value.id)).toEqual(["e1"]);
    expect(replay).toMatchObject({ label: "Recorded replay", source_execution_kind: "live_model" });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/runtime/v1/runs/run-1",
      "/api/runtime/v1/runs/run-1/events",
      "/api/runtime/v1/runs/run-1/replay",
    ]);
  });

  it("approves only the server-issued ID and context, with no client-authored effect scope", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        approval_id: "approval-1",
        run_id: "run-1",
        status: "APPROVED",
        decided_at: "2030-06-13T16:01:00Z",
        resumed: true,
      }),
    );
    const client = new RuntimeApiClient({
      baseUrl: "/runtime",
      sessionToken: "session-token-at-least-16",
      fetcher,
      idempotencyKeyFactory: () => "idem-approval-1",
    });
    await client.decideApproval("approval-1", hash, "approve");

    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("/runtime/v1/approvals/approval-1/approve");
    expect(init?.body).toBe("{}");
    expect(new Headers(init?.headers).get("If-Match")).toBe(`"${hash}"`);
    expect(String(init?.body)).not.toMatch(/flight|price|effect|capability|grant/i);
  });

  it("fails closed if sealed capability material appears in an approval response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        approval_id: "approval-1",
        run_id: "run-1",
        status: "APPROVED",
        decided_at: "2030-06-13T16:01:00Z",
        resumed: true,
        signed_grant: { signature: "must-not-reach-browser" },
      }),
    );
    const client = new RuntimeApiClient({
      baseUrl: "/runtime",
      sessionToken: "session-token-at-least-16",
      fetcher,
      idempotencyKeyFactory: () => "idem-approval-1",
    });
    await expect(client.decideApproval("approval-1", hash, "approve")).rejects.toMatchObject({
      code: "SEALED_APPROVAL_MATERIAL_EXPOSED",
    });
  });

  it("preserves versioned runtime errors and retry guidance", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          version: "1.0.0",
          error: { code: "PUBLIC_RATE_LIMIT", message: "Try the recorded replay." },
        },
        429,
        { "Retry-After": "45" },
      ),
    );
    const client = new RuntimeApiClient({
      baseUrl: "/runtime",
      sessionToken: "session-token-at-least-16",
      fetcher,
    });
    const error = await client.getRun("run-1").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(RuntimeApiError);
    expect(error).toMatchObject({ status: 429, code: "PUBLIC_RATE_LIMIT", retryAfterSeconds: 45 });
  });
});
