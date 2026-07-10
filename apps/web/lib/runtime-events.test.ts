import { describe, expect, it, vi } from "vitest";

import { RuntimeApiClient } from "./runtime-api";
import { fetchRunEventBacklog, SseDecoder, subscribeRunEvents } from "./runtime-events";

function streamResponse(body: string): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function event(id: string, sequence: number): string {
  return `id: ${id}\nevent: run.event\ndata: ${JSON.stringify({ id, sequence_no: sequence, event_type: "run.state_changed", created_at: "2030-06-13T16:00:00Z", payload: { status: "OBSERVING" } })}\n\n`;
}

describe("SseDecoder", () => {
  it("parses split CRLF records, comments, retry hints, and multiline data", () => {
    const decoder = new SseDecoder();
    expect(
      decoder.push(': keep-alive\r\n\r\nid: 7\r\nevent: trace\r\nretry: 750\r\ndata: {"a":\r\n'),
    ).toEqual([]);
    expect(decoder.push("data: 1}\r\n\r\n")).toEqual([
      { id: "7", event: "trace", retry: 750, data: '{"a":\n1}' },
    ]);
  });
});

describe("run event subscription", () => {
  it("fetches the persisted SSE backlog using the runtime wire timestamp aliases", async () => {
    const payload = {
      sequence: 1,
      event_type: "run.created",
      occurred_at: "2030-06-13T16:00:00Z",
      payload: { status: "CREATED" },
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        streamResponse(`id: 1\nevent: run.created\ndata: ${JSON.stringify(payload)}\n\n`),
      );
    const client = new RuntimeApiClient({ baseUrl: "/api/runtime", fetcher });
    const events = await fetchRunEventBacklog(client, "run-1");
    expect(events).toEqual([
      {
        id: "1",
        sequence_no: 1,
        event_type: "run.created",
        created_at: "2030-06-13T16:00:00Z",
        payload: { status: "CREATED" },
        step_id: undefined,
      },
    ]);
  });

  it("reconnects with Last-Event-ID, deduplicates, and never places credentials in the URL", async () => {
    const abort = new AbortController();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(streamResponse(event("41", 41)))
      .mockResolvedValueOnce(streamResponse(event("41", 41) + event("42", 42)));
    const client = new RuntimeApiClient({
      baseUrl: "https://runtime.example.test",
      sessionToken: "session-token-at-least-16",
      fetcher,
    });
    const received: string[] = [];
    const subscription = subscribeRunEvents(client, "run-1", {
      signal: abort.signal,
      maxReconnects: 2,
      sleep: async () => undefined,
      onEvent(value) {
        received.push(value.id);
        if (value.id === "42") abort.abort();
      },
    });
    await subscription.done;

    expect(received).toEqual(["41", "42"]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [secondUrl, secondInit] = fetcher.mock.calls[1] ?? [];
    expect(String(secondUrl)).not.toContain("session-token");
    expect(new Headers(secondInit?.headers).get("Last-Event-ID")).toBe("41");
    expect(new Headers(secondInit?.headers).get("X-Demo-Session-Token")).toBe(
      "session-token-at-least-16",
    );
  });
});
