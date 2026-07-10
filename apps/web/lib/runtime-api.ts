import type { FaultId, TraceTone } from "./types";

export const RUN_STATUSES = [
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
] as const;

export type RunStatus = (typeof RUN_STATUSES)[number];
export type RunMode = "baseline" | "protected" | "mock" | "replay";
export type ExecutionKind = "live_model" | "deterministic_mock" | "recorded_replay";
export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED";

export interface RuntimeUsage {
  elapsed_seconds?: number;
  step_count?: number;
  model_call_count?: number;
  model_cost_usd?: string;
  max_steps?: number;
  max_model_calls?: number;
  max_model_cost_usd?: string;
  max_wall_time_seconds?: number;
  replan_count?: number;
  max_replans?: number;
}

export interface BrowserObservationSummary {
  screenshot_url?: string;
  origin?: string;
  path?: string;
  viewport?: string;
  observation_hash?: string;
  artifact_id?: string;
}

export interface ApprovalConstraint {
  label: string;
  value: string;
  satisfied: boolean;
}

/** Public, display-only semantic scope. A signed grant is never a public API value. */
export interface ApprovalScope {
  marketing_carrier: string;
  operating_carrier: string;
  flight_id: string;
  origin_airport: string;
  destination_airport: string;
  departure: string;
  arrival: string;
  stop_count: number;
  cabin: string;
  fare_class: string;
  seat_type: string;
  seat?: string;
  traveler_display_name: string;
  total_additional_cost_minor: number;
  currency: string;
  constraints: ApprovalConstraint[];
  immediate_effect: string;
}

export interface PublicApprovalRequest {
  approval_id: string;
  run_id: string;
  status: ApprovalStatus;
  summary: string;
  approved_context_hash: string;
  requested_at: string;
  expires_at: string;
  scope: ApprovalScope;
}

export interface RuntimeRun {
  run_id: string;
  session_id: string;
  mode: RunMode;
  status: RunStatus;
  execution_kind?: ExecutionKind;
  model_provider?: string;
  model_id?: string;
  task_contract: Record<string, unknown>;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  terminal_reason?: string;
  active_subgoal?: string;
  expected_postcondition?: string;
  policy_decision?: string;
  policy_rule_id?: string;
  verification_result?: string;
  usage?: RuntimeUsage;
  browser?: BrowserObservationSummary;
  pending_approval?: PublicApprovalRequest;
}

export interface RuntimeEvent {
  id: string;
  sequence_no: number;
  event_type: string;
  created_at: string;
  step_id?: string;
  payload: Record<string, unknown>;
}

export interface ReplayFrameRecord {
  id: string;
  event_id?: string;
  sequence_no: number;
  chapter: string;
  app: string;
  path: string;
  status: string;
  title: string;
  description: string;
  evidence: string;
  tone: TraceTone;
  screenshot_url?: string;
}

export interface ReplayBundle {
  run_id: string;
  label: "Recorded replay";
  recorded_at: string;
  source_execution_kind: ExecutionKind;
  frames: ReplayFrameRecord[];
}

export interface SessionResponse {
  session_id: string;
  expires_at: string;
}

export interface TaskContractDraft {
  schema_version: "1.0.0";
  goal: string;
  hard_constraints: Array<{ field: string; operator: string; value: unknown }>;
  preferences: Array<{ field: string; direction: "ascending" | "descending" }>;
  success_predicates: Array<{ predicate_id: string; parameters?: Record<string, unknown> }>;
  forbidden_effects: string[];
  approval_rules: Array<{ effect: "FINANCIAL_OR_CONTRACTUAL_COMMIT"; rule: string }>;
  allowed_origins: string[];
  allowed_tools: string[];
  scenario_now: string;
  max_steps: number;
  max_model_calls: number;
  max_replans: number;
  max_wall_time_seconds: number;
  max_model_cost_usd: string;
  max_read_retries_per_step: number;
  max_commit_retries: 0;
  non_progress_limit: number;
  approval_ttl_seconds: number;
  max_commit_observation_age_seconds: number;
}

export interface CreateRunRequest {
  task_contract: TaskContractDraft;
  scenario_selection: {
    scenario_id: "disrupted_trip_v1";
    fault_id: Exclude<FaultId, "none"> | null;
    scenario_seed: number;
  };
  mode: "protected";
}

export interface ApprovalDecisionResponse {
  approval_id: string;
  run_id: string;
  status: Exclude<ApprovalStatus, "PENDING">;
  decided_at: string;
  resumed: boolean;
}

interface ApiErrorPayload {
  version?: string;
  error?: { code?: string; message?: string };
}

export class RuntimeApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds?: number;

  constructor(
    message: string,
    {
      status,
      code,
      retryAfterSeconds,
    }: { status: number; code: string; retryAfterSeconds?: number },
  ) {
    super(message);
    this.name = "RuntimeApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export interface RuntimeApiClientOptions {
  baseUrl: string;
  sessionToken?: string;
  fetcher?: typeof fetch;
  idempotencyKeyFactory?: () => string;
  requestTimeoutMs?: number;
  authMode?: "cookie" | "header";
}

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown, label: string): JsonRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new RuntimeApiError(`${label} response was malformed.`, {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  return value as JsonRecord;
}

function requiredString(record: JsonRecord, key: string, label = key): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new RuntimeApiError(`${label} was missing from the runtime response.`, {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  return value;
}

function optionalString(record: JsonRecord, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function optionalNumber(record: JsonRecord, key: string): number | undefined {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function requiredNumber(record: JsonRecord, key: string, label = key): number {
  const value = optionalNumber(record, key);
  if (value === undefined) {
    throw new RuntimeApiError(`${label} was missing from the runtime response.`, {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  return value;
}

function isRunStatus(value: unknown): value is RunStatus {
  return typeof value === "string" && (RUN_STATUSES as readonly string[]).includes(value);
}

function isRunMode(value: unknown): value is RunMode {
  return value === "baseline" || value === "protected" || value === "mock" || value === "replay";
}

function isExecutionKind(value: unknown): value is ExecutionKind {
  return value === "live_model" || value === "deterministic_mock" || value === "recorded_replay";
}

function isApprovalStatus(value: unknown): value is ApprovalStatus {
  return (
    value === "PENDING" ||
    value === "APPROVED" ||
    value === "REJECTED" ||
    value === "EXPIRED" ||
    value === "CANCELLED"
  );
}

function decodeUsage(value: unknown): RuntimeUsage | undefined {
  if (value === undefined) return undefined;
  const record = asRecord(value, "usage");
  return {
    elapsed_seconds: optionalNumber(record, "elapsed_seconds"),
    step_count: optionalNumber(record, "step_count"),
    model_call_count: optionalNumber(record, "model_call_count"),
    model_cost_usd: optionalString(record, "model_cost_usd"),
    max_steps: optionalNumber(record, "max_steps"),
    max_model_calls: optionalNumber(record, "max_model_calls"),
    max_model_cost_usd: optionalString(record, "max_model_cost_usd"),
    max_wall_time_seconds: optionalNumber(record, "max_wall_time_seconds"),
    replan_count: optionalNumber(record, "replan_count"),
    max_replans: optionalNumber(record, "max_replans"),
  };
}

function decodeBrowser(value: unknown): BrowserObservationSummary | undefined {
  if (value === undefined) return undefined;
  const record = asRecord(value, "browser");
  return {
    screenshot_url: optionalString(record, "screenshot_url"),
    origin: optionalString(record, "origin"),
    path: optionalString(record, "path"),
    viewport: optionalString(record, "viewport"),
    observation_hash: optionalString(record, "observation_hash"),
    artifact_id: optionalString(record, "artifact_id"),
  };
}

function decodeApprovalConstraint(value: unknown): ApprovalConstraint {
  const record = asRecord(value, "approval constraint");
  if (typeof record.satisfied !== "boolean") {
    throw new RuntimeApiError("Approval constraint result was missing.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  return {
    label: requiredString(record, "label"),
    value: requiredString(record, "value"),
    satisfied: record.satisfied,
  };
}

function decodeApproval(value: unknown): PublicApprovalRequest | undefined {
  if (value === undefined || value === null) return undefined;
  const record = asRecord(value, "approval");
  const status = record.status;
  if (!isApprovalStatus(status)) {
    throw new RuntimeApiError("Approval status was invalid.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  const scope = asRecord(record.scope, "approval scope");
  const constraints = Array.isArray(scope.constraints)
    ? scope.constraints.map(decodeApprovalConstraint)
    : [];
  return {
    approval_id:
      optionalString(record, "approval_id") ?? requiredString(record, "request_id", "approval ID"),
    run_id: requiredString(record, "run_id"),
    status,
    summary: requiredString(record, "summary"),
    approved_context_hash: requiredString(record, "approved_context_hash"),
    requested_at:
      optionalString(record, "requested_at") ??
      requiredString(record, "created_at", "requested_at"),
    expires_at: requiredString(record, "expires_at"),
    scope: {
      marketing_carrier: requiredString(scope, "marketing_carrier"),
      operating_carrier: requiredString(scope, "operating_carrier"),
      flight_id: requiredString(scope, "flight_id"),
      origin_airport: requiredString(scope, "origin_airport"),
      destination_airport: requiredString(scope, "destination_airport"),
      departure: requiredString(scope, "departure"),
      arrival: requiredString(scope, "arrival"),
      stop_count: requiredNumber(scope, "stop_count"),
      cabin: requiredString(scope, "cabin"),
      fare_class: requiredString(scope, "fare_class"),
      seat_type: requiredString(scope, "seat_type"),
      seat: optionalString(scope, "seat"),
      traveler_display_name: requiredString(scope, "traveler_display_name"),
      total_additional_cost_minor: requiredNumber(scope, "total_additional_cost_minor"),
      currency: requiredString(scope, "currency"),
      constraints,
      immediate_effect: requiredString(scope, "immediate_effect"),
    },
  };
}

export function decodeRuntimeRun(value: unknown): RuntimeRun {
  const record = asRecord(value, "run");
  const status = record.status;
  const mode = record.mode;
  if (!isRunStatus(status) || !isRunMode(mode)) {
    throw new RuntimeApiError("Run status or mode was invalid.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  const executionKind = record.execution_kind;
  const taskContract = asRecord(record.task_contract, "task contract");
  return {
    run_id: requiredString(record, "run_id"),
    session_id: requiredString(record, "session_id"),
    mode,
    status,
    execution_kind: isExecutionKind(executionKind) ? executionKind : undefined,
    model_provider: optionalString(record, "model_provider"),
    model_id: optionalString(record, "model_id"),
    task_contract: taskContract,
    created_at: requiredString(record, "created_at"),
    started_at: optionalString(record, "started_at"),
    finished_at: optionalString(record, "finished_at"),
    terminal_reason: optionalString(record, "terminal_reason"),
    active_subgoal: optionalString(record, "active_subgoal"),
    expected_postcondition: optionalString(record, "expected_postcondition"),
    policy_decision: optionalString(record, "policy_decision"),
    policy_rule_id: optionalString(record, "policy_rule_id"),
    verification_result: optionalString(record, "verification_result"),
    usage: decodeUsage(record.usage),
    browser: decodeBrowser(record.browser),
    pending_approval: decodeApproval(record.pending_approval),
  };
}

export function decodeRuntimeEvent(value: unknown, fallbackId?: string): RuntimeEvent {
  const record = asRecord(value, "event");
  const payload = asRecord(record.payload ?? {}, "event payload");
  const sequence = optionalNumber(record, "sequence_no") ?? optionalNumber(record, "sequence") ?? 0;
  return {
    id:
      optionalString(record, "id") ??
      optionalString(record, "event_id") ??
      fallbackId ??
      String(sequence),
    sequence_no: sequence,
    event_type: requiredString(record, "event_type"),
    created_at:
      optionalString(record, "created_at") ??
      requiredString(record, "occurred_at", "event timestamp"),
    step_id: optionalString(record, "step_id"),
    payload,
  };
}

function decodeReplay(value: unknown): ReplayBundle {
  const record = asRecord(value, "replay");
  if (record.label !== "Recorded replay") {
    throw new RuntimeApiError("Replay was not explicitly labeled as recorded.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  const source = record.source_execution_kind;
  if (!isExecutionKind(source)) {
    throw new RuntimeApiError("Replay source execution kind was invalid.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  const framesValue = record.frames;
  if (!Array.isArray(framesValue)) {
    throw new RuntimeApiError("Replay frames were malformed.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  const frames = framesValue.map((value, index): ReplayFrameRecord => {
    const frame = asRecord(value, "replay frame");
    const tone = frame.tone;
    return {
      id: optionalString(frame, "id") ?? `frame-${index + 1}`,
      event_id: optionalString(frame, "event_id"),
      sequence_no: optionalNumber(frame, "sequence_no") ?? index + 1,
      chapter: requiredString(frame, "chapter"),
      app: requiredString(frame, "app"),
      path: requiredString(frame, "path"),
      status: requiredString(frame, "status"),
      title: requiredString(frame, "title"),
      description: requiredString(frame, "description"),
      evidence: requiredString(frame, "evidence"),
      tone:
        tone === "accent" || tone === "success" || tone === "warning" || tone === "danger"
          ? tone
          : "neutral",
      screenshot_url: optionalString(frame, "screenshot_url"),
    };
  });
  return {
    run_id: requiredString(record, "run_id"),
    label: "Recorded replay",
    recorded_at: requiredString(record, "recorded_at"),
    source_execution_kind: source,
    frames,
  };
}

function hasForbiddenApprovalMaterial(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasForbiddenApprovalMaterial);
  if (typeof value !== "object" || value === null) return false;
  return Object.entries(value as JsonRecord).some(([key, child]) => {
    const normalized = key.toLowerCase();
    return (
      ["capability", "grant", "signature", "nonce", "hmac", "secret"].some((word) =>
        normalized.includes(word),
      ) || hasForbiddenApprovalMaterial(child)
    );
  });
}

function decodeApprovalDecision(value: unknown): ApprovalDecisionResponse {
  if (hasForbiddenApprovalMaterial(value)) {
    throw new RuntimeApiError(
      "Runtime attempted to expose sealed approval material to the browser.",
      { status: 502, code: "SEALED_APPROVAL_MATERIAL_EXPOSED" },
    );
  }
  const record = asRecord(value, "approval decision");
  const status = record.status;
  if (
    status !== "APPROVED" &&
    status !== "REJECTED" &&
    status !== "EXPIRED" &&
    status !== "CANCELLED"
  ) {
    throw new RuntimeApiError("Approval decision status was invalid.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
  return {
    approval_id:
      optionalString(record, "approval_id") ?? requiredString(record, "request_id", "approval ID"),
    run_id: requiredString(record, "run_id"),
    status,
    decided_at: requiredString(record, "decided_at"),
    resumed: record.resumed === true,
  };
}

function createSecureIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi || typeof cryptoApi.randomUUID !== "function") {
    throw new Error("Secure random UUID support is required for mutation idempotency keys.");
  }
  return cryptoApi.randomUUID();
}

function normalizeBaseUrl(baseUrl: string): string {
  const trimmed = baseUrl.trim();
  if (!trimmed) throw new Error("Runtime API base URL is required.");
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

export class RuntimeApiClient {
  readonly baseUrl: string;
  private sessionToken?: string;
  private readonly fetcher: typeof fetch;
  private readonly keyFactory: () => string;
  private readonly requestTimeoutMs: number;
  private readonly authMode: "cookie" | "header";

  constructor({
    baseUrl,
    sessionToken,
    fetcher = fetch,
    idempotencyKeyFactory = createSecureIdempotencyKey,
    requestTimeoutMs = 30_000,
    authMode,
  }: RuntimeApiClientOptions) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.sessionToken = sessionToken;
    this.fetcher = fetcher;
    this.keyFactory = idempotencyKeyFactory;
    this.requestTimeoutMs = requestTimeoutMs;
    this.authMode = authMode ?? (this.baseUrl.startsWith("/") ? "cookie" : "header");
  }

  setSessionToken(token: string): void {
    if (token.length < 16) throw new Error("Runtime session token is malformed.");
    this.sessionToken = token;
  }

  async createSession(clientLabel = "trust-runtime-web"): Promise<SessionResponse> {
    const response = await this.request("/v1/sessions", {
      method: "POST",
      body: { client_label: clientLabel },
      mutation: true,
      authenticated: false,
    });
    const record = asRecord(response, "session");
    const rawToken = optionalString(record, "session_token");
    if (this.authMode === "header") {
      if (!rawToken)
        throw new RuntimeApiError("Session token was missing from the direct runtime response.", {
          status: 502,
          code: "INVALID_RUNTIME_RESPONSE",
        });
      this.setSessionToken(rawToken);
    }
    const session = {
      session_id: requiredString(record, "session_id"),
      expires_at: requiredString(record, "expires_at"),
    };
    return session;
  }

  async createRun(payload: CreateRunRequest): Promise<RuntimeRun> {
    return decodeRuntimeRun(
      await this.request("/v1/runs", { method: "POST", body: payload, mutation: true }),
    );
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<RuntimeRun> {
    return decodeRuntimeRun(
      await this.request(`/v1/runs/${encodeURIComponent(runId)}`, { method: "GET", signal }),
    );
  }

  async getRunEvents(runId: string, signal?: AbortSignal): Promise<RuntimeEvent[]> {
    const response = await this.request(`/v1/runs/${encodeURIComponent(runId)}/events`, {
      method: "GET",
      signal,
      headers: { Accept: "application/json" },
    });
    const values = Array.isArray(response) ? response : asRecord(response, "event list").events;
    if (!Array.isArray(values)) {
      throw new RuntimeApiError("Event list was malformed.", {
        status: 502,
        code: "INVALID_RUNTIME_RESPONSE",
      });
    }
    return values.map((value) => decodeRuntimeEvent(value));
  }

  async getRunReplay(runId: string, signal?: AbortSignal): Promise<ReplayBundle> {
    return decodeReplay(
      await this.request(`/v1/runs/${encodeURIComponent(runId)}/replay`, { method: "GET", signal }),
    );
  }

  async decideApproval(
    approvalId: string,
    contextHash: string,
    decision: "approve" | "reject",
  ): Promise<ApprovalDecisionResponse> {
    if (!/^[a-f0-9]{64}$/.test(contextHash)) throw new Error("Approval context hash is malformed.");
    const response = await this.request(
      `/v1/approvals/${encodeURIComponent(approvalId)}/${decision}`,
      {
        method: "POST",
        body: {},
        mutation: true,
        headers: { "If-Match": `"${contextHash}"` },
      },
    );
    return decodeApprovalDecision(response);
  }

  getSessionTokenForStream(): string | undefined {
    if (this.authMode === "header" && !this.sessionToken)
      throw new RuntimeApiError("This run is not attached to an active demo session.", {
        status: 401,
        code: "SESSION_REQUIRED",
      });
    return this.sessionToken;
  }

  getFetchImplementation(): typeof fetch {
    return this.fetcher;
  }

  private async request(
    path: string,
    options: {
      method: "GET" | "POST";
      body?: unknown;
      mutation?: boolean;
      authenticated?: boolean;
      headers?: Record<string, string>;
      signal?: AbortSignal;
    },
  ): Promise<unknown> {
    const authenticated = options.authenticated ?? true;
    if (authenticated && this.authMode === "header" && !this.sessionToken) {
      throw new RuntimeApiError("This run is not attached to an active demo session.", {
        status: 401,
        code: "SESSION_REQUIRED",
      });
    }
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.mutation) {
      headers["Idempotency-Key"] = this.keyFactory();
      headers["X-Trust-CSRF"] = "1";
    }
    if (authenticated && this.sessionToken) headers["X-Demo-Session-Token"] = this.sessionToken;

    const timeoutController = new AbortController();
    const timeout = setTimeout(
      () => timeoutController.abort(new DOMException("Runtime request timed out", "TimeoutError")),
      this.requestTimeoutMs,
    );
    const abortFromCaller = () => timeoutController.abort(options.signal?.reason);
    options.signal?.addEventListener("abort", abortFromCaller, { once: true });
    try {
      const response = await this.fetcher(`${this.baseUrl}${path}`, {
        method: options.method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: "include",
        cache: "no-store",
        signal: timeoutController.signal,
      });
      const payload = await readJson(response);
      if (!response.ok) throw apiError(response, payload);
      return payload;
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abortFromCaller);
    }
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new RuntimeApiError("Runtime returned a non-JSON response.", {
      status: 502,
      code: "INVALID_RUNTIME_RESPONSE",
    });
  }
}

function apiError(response: Response, payload: unknown): RuntimeApiError {
  const record =
    typeof payload === "object" && payload !== null ? (payload as ApiErrorPayload) : undefined;
  const retryHeader = response.headers.get("Retry-After");
  const parsedRetry = retryHeader === null ? undefined : Number(retryHeader);
  return new RuntimeApiError(
    record?.error?.message ?? `Runtime request failed with HTTP ${response.status}.`,
    {
      status: response.status,
      code: record?.error?.code ?? `HTTP_${response.status}`,
      retryAfterSeconds: Number.isFinite(parsedRetry) ? parsedRetry : undefined,
    },
  );
}

export function isFixtureRunId(runId: string): boolean {
  return runId.startsWith("mock-") || runId.startsWith("fixture-");
}

export function runtimeExecutionLabel(run: RuntimeRun): string {
  if (run.execution_kind === "live_model") return "Live model run";
  if (run.execution_kind === "deterministic_mock" || run.mode === "mock") return "Mock run";
  if (run.execution_kind === "recorded_replay" || run.mode === "replay") return "Recorded replay";
  return "Runtime run";
}
