import {
  RuntimeApiClient,
  RuntimeApiError,
  decodeRuntimeEvent,
  type RuntimeEvent,
} from "./runtime-api";

const DEFAULT_RETRY_MS = 1_000;
const MIN_RETRY_MS = 250;
const MAX_RETRY_MS = 10_000;
const MAX_EVENT_BYTES = 256 * 1024;

export type EventConnectionState =
  "connecting" | "connected" | "reconnecting" | "closed" | "failed";

export interface SseRecord {
  id?: string;
  event: string;
  data?: string;
  retry?: number;
}

/** Incremental SSE parser that handles CRLF, split chunks, comments, and multi-line data. */
export class SseDecoder {
  private buffer = "";

  push(chunk: string): SseRecord[] {
    this.buffer += chunk;
    if (this.buffer.length > MAX_EVENT_BYTES * 2) {
      throw new RuntimeApiError("Event stream exceeded the public payload limit.", {
        status: 502,
        code: "SSE_EVENT_TOO_LARGE",
      });
    }
    const records: SseRecord[] = [];
    let boundary = findBoundary(this.buffer);
    while (boundary !== undefined) {
      const block = this.buffer.slice(0, boundary.index);
      this.buffer = this.buffer.slice(boundary.index + boundary.length);
      const parsed = parseBlock(block);
      if (parsed) records.push(parsed);
      boundary = findBoundary(this.buffer);
    }
    return records;
  }
}

function findBoundary(value: string): { index: number; length: number } | undefined {
  const lf = value.indexOf("\n\n");
  const crlf = value.indexOf("\r\n\r\n");
  if (lf < 0 && crlf < 0) return undefined;
  if (crlf >= 0 && (lf < 0 || crlf <= lf)) return { index: crlf, length: 4 };
  return { index: lf, length: 2 };
}

function parseBlock(block: string): SseRecord | undefined {
  if (!block || block.startsWith(":")) return undefined;
  let id: string | undefined;
  let event = "message";
  let retry: number | undefined;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const raw = colon < 0 ? "" : line.slice(colon + 1);
    const value = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "id" && !value.includes("\0")) id = value;
    else if (field === "event") event = value || "message";
    else if (field === "data") data.push(value);
    else if (field === "retry" && /^\d+$/.test(value)) retry = Number(value);
  }
  if (data.join("\n").length > MAX_EVENT_BYTES) {
    throw new RuntimeApiError("Event stream payload exceeded 256 KB.", {
      status: 502,
      code: "SSE_EVENT_TOO_LARGE",
    });
  }
  if (data.length === 0 && retry === undefined && id === undefined) return undefined;
  return { id, event, data: data.length > 0 ? data.join("\n") : undefined, retry };
}

export interface SubscribeRunEventsOptions {
  signal?: AbortSignal;
  lastEventId?: string;
  maxReconnects?: number;
  onEvent: (event: RuntimeEvent) => void;
  onState?: (state: EventConnectionState) => void;
  onError?: (error: RuntimeApiError) => void;
  sleep?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
}

export interface EventSubscription {
  close: () => void;
  done: Promise<void>;
}

/** Fetch the persisted SSE backlog once before opening the reconnecting tail. */
export async function fetchRunEventBacklog(
  client: RuntimeApiClient,
  runId: string,
  signal?: AbortSignal,
): Promise<RuntimeEvent[]> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Cache-Control": "no-cache",
  };
  const sessionToken = client.getSessionTokenForStream();
  if (sessionToken) headers["X-Demo-Session-Token"] = sessionToken;
  const response = await client.getFetchImplementation()(
    `${client.baseUrl}/v1/runs/${encodeURIComponent(runId)}/events`,
    {
      method: "GET",
      headers,
      credentials: "include",
      cache: "no-store",
      signal,
    },
  );
  if (!response.ok) throw await streamApiError(response);
  if (!response.body)
    throw new RuntimeApiError("Runtime returned an empty event stream.", {
      status: 502,
      code: "SSE_BODY_MISSING",
    });
  const reader = response.body.getReader();
  const textDecoder = new TextDecoder();
  const sse = new SseDecoder();
  const events: RuntimeEvent[] = [];
  const seen = new Set<string>();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const record of sse.push(textDecoder.decode(value, { stream: true }))) {
      if (record.data === undefined) continue;
      let payload: unknown;
      try {
        payload = JSON.parse(record.data) as unknown;
      } catch {
        throw new RuntimeApiError("Runtime emitted malformed event JSON.", {
          status: 502,
          code: "INVALID_SSE_EVENT",
        });
      }
      const event = decodeRuntimeEvent(payload, record.id);
      if (!seen.has(event.id)) {
        seen.add(event.id);
        events.push(event);
      }
    }
  }
  return events.sort((left, right) => left.sequence_no - right.sequence_no);
}

export function subscribeRunEvents(
  client: RuntimeApiClient,
  runId: string,
  options: SubscribeRunEventsOptions,
): EventSubscription {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener("abort", forwardAbort, { once: true });
  const done = runEventLoop(client, runId, { ...options, signal: controller.signal })
    .catch((error: unknown) => {
      if (controller.signal.aborted) return;
      const runtimeError =
        error instanceof RuntimeApiError
          ? error
          : new RuntimeApiError("The event stream disconnected.", {
              status: 503,
              code: "SSE_DISCONNECTED",
            });
      options.onState?.("failed");
      options.onError?.(runtimeError);
    })
    .finally(() => {
      options.signal?.removeEventListener("abort", forwardAbort);
      if (controller.signal.aborted) options.onState?.("closed");
    });
  return { close: () => controller.abort(), done };
}

async function runEventLoop(
  client: RuntimeApiClient,
  runId: string,
  options: SubscribeRunEventsOptions & { signal: AbortSignal },
): Promise<void> {
  const maxReconnects = options.maxReconnects ?? 8;
  const sleep = options.sleep ?? abortableSleep;
  const seenIds = new Set<string>();
  let lastEventId = options.lastEventId;
  let reconnects = 0;
  let retryMs = DEFAULT_RETRY_MS;

  while (!options.signal.aborted) {
    options.onState?.(reconnects === 0 ? "connecting" : "reconnecting");
    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      };
      const sessionToken = client.getSessionTokenForStream();
      if (sessionToken) headers["X-Demo-Session-Token"] = sessionToken;
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;
      const response = await client.getFetchImplementation()(
        `${client.baseUrl}/v1/runs/${encodeURIComponent(runId)}/events`,
        {
          method: "GET",
          headers,
          credentials: "include",
          cache: "no-store",
          signal: options.signal,
        },
      );
      if (!response.ok) throw await streamApiError(response);
      if (!response.body)
        throw new RuntimeApiError("Runtime returned an empty event stream.", {
          status: 502,
          code: "SSE_BODY_MISSING",
        });
      options.onState?.("connected");
      const reader = response.body.getReader();
      const textDecoder = new TextDecoder();
      const sse = new SseDecoder();
      let receivedThisConnection = false;

      while (!options.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const record of sse.push(textDecoder.decode(value, { stream: true }))) {
          if (record.retry !== undefined) retryMs = clampRetry(record.retry);
          if (record.data === undefined) {
            if (record.id) lastEventId = record.id;
            continue;
          }
          let payload: unknown;
          try {
            payload = JSON.parse(record.data) as unknown;
          } catch {
            throw new RuntimeApiError("Runtime emitted malformed event JSON.", {
              status: 502,
              code: "INVALID_SSE_EVENT",
            });
          }
          const event = decodeRuntimeEvent(payload, record.id);
          const eventId = record.id ?? event.id;
          lastEventId = eventId;
          if (seenIds.has(eventId)) continue;
          seenIds.add(eventId);
          receivedThisConnection = true;
          options.onEvent(event);
        }
      }
      if (options.signal.aborted) return;
      if (receivedThisConnection) reconnects = 0;
    } catch (error: unknown) {
      if (options.signal.aborted) return;
      if (error instanceof RuntimeApiError && !isRetryable(error.status)) throw error;
    }

    reconnects += 1;
    if (reconnects > maxReconnects) {
      throw new RuntimeApiError("Event stream did not recover after bounded retries.", {
        status: 503,
        code: "SSE_RECONNECT_EXHAUSTED",
      });
    }
    await sleep(retryMs, options.signal);
  }
}

function clampRetry(value: number): number {
  return Math.min(MAX_RETRY_MS, Math.max(MIN_RETRY_MS, value));
}

function isRetryable(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

async function streamApiError(response: Response): Promise<RuntimeApiError> {
  let message = `Event stream failed with HTTP ${response.status}.`;
  let code = `HTTP_${response.status}`;
  try {
    const payload = (await response.json()) as { error?: { code?: string; message?: string } };
    message = payload.error?.message ?? message;
    code = payload.error?.code ?? code;
  } catch {
    // The status remains authoritative even if an intermediary returned HTML.
  }
  return new RuntimeApiError(message, { status: response.status, code });
}

function abortableSleep(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, milliseconds);
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}
