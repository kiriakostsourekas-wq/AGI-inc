import { randomUUID } from "node:crypto";
import { canonicalJson, constantTimeEqual, hmacSha256, sha256 } from "./crypto";
import { createScenario } from "./fixtures";
import {
  type ApprovalContext,
  type ApprovalGrant,
  type Booking,
  type CalendarResult,
  type CommitResult,
  type FaultId,
  type FlightOption,
  type OracleSandboxState,
  type PublicSandboxState,
  type RuntimeMode,
  type SandboxState,
  SandboxError,
} from "./types";

type SignedGrantPayload = {
  version: 1;
  grantId: string;
  runId: string;
  context: ApprovalContext;
  contextHash: string;
  issuedAt: string;
  expiresAt: string;
};

type GlobalSandboxStore = {
  runs: Map<string, SandboxState>;
  mutationTail: Promise<void>;
};

declare global {
  var __trustSandboxStore: GlobalSandboxStore | undefined;
}

const store: GlobalSandboxStore =
  globalThis.__trustSandboxStore ??
  (globalThis.__trustSandboxStore = {
    runs: new Map<string, SandboxState>(),
    mutationTail: Promise.resolve(),
  });

async function withMutationLock<T>(operation: () => T | Promise<T>): Promise<T> {
  const previous = store.mutationTail;
  let release: () => void = () => {};
  store.mutationTail = new Promise<void>((resolve) => {
    release = resolve;
  });
  await previous;
  try {
    return await operation();
  } finally {
    release();
  }
}

function approvalSecret(): string {
  const configured = process.env.SANDBOX_APPROVAL_SECRET ?? process.env.APPROVAL_HMAC_SECRET;
  if (configured) return configured;
  if (process.env.NODE_ENV === "production") {
    throw new Error("SANDBOX_APPROVAL_SECRET is required in production");
  }
  return "synthetic-local-approval-secret-do-not-use-in-production";
}

function getOrCreate(runId: string): SandboxState {
  const existing = store.runs.get(runId);
  if (existing) return existing;
  const created = createScenario(runId);
  store.runs.set(runId, created);
  return created;
}

function cents(amount: string): number {
  if (!/^\d+\.\d{2}$/.test(amount)) {
    throw new Error(`Invalid exact decimal amount: ${amount}`);
  }
  const [whole, fractional] = amount.split(".");
  return Number(whole) * 100 + Number(fractional);
}

export function optionSatisfiesConstraints(state: SandboxState, option: FlightOption): boolean {
  const constraints = state.constraints;
  return (
    option.origin === constraints.origin &&
    option.destination === constraints.destination &&
    option.cabin === constraints.cabin &&
    option.departure >= constraints.departOnOrAfter &&
    option.arrival <= constraints.arriveOnOrBefore &&
    option.seatAvailable &&
    option.seatType === constraints.seatType &&
    option.additionalCost.currency === constraints.maximumAdditionalCost.currency &&
    cents(option.additionalCost.amount) <= cents(constraints.maximumAdditionalCost.amount)
  );
}

function approvalContext(state: SandboxState, option: FlightOption): ApprovalContext {
  return {
    runId: state.runId,
    travelerId: state.traveler.id,
    reservationId: state.originalReservation.reservationId,
    flightId: option.flightId,
    airline: option.airline,
    marketingCarrier: option.marketingCarrier,
    operatingCarrier: option.operatingCarrier,
    offerVersion: option.offerVersion,
    origin: option.origin,
    destination: option.destination,
    departure: option.departure,
    arrival: option.arrival,
    stopCount: option.stopCount,
    cabin: option.cabin,
    fareClass: option.fareClass,
    seatType: "aisle",
    amount: option.additionalCost.amount,
    baseFareAmount: option.baseFare.amount,
    taxesAndFeesAmount: option.taxesAndFees.amount,
    currency: option.additionalCost.currency,
  };
}

function encodeGrant(payload: SignedGrantPayload): string {
  const body = Buffer.from(canonicalJson(payload)).toString("base64url");
  const signature = hmacSha256(body, approvalSecret());
  return `${body}.${signature}`;
}

function decodeGrant(token: string): SignedGrantPayload {
  const [body, signature, extra] = token.split(".");
  if (!body || !signature || extra) {
    throw new SandboxError("APPROVAL_INVALID", "The approval capability is malformed.", 403);
  }
  const expected = hmacSha256(body, approvalSecret());
  if (!constantTimeEqual(signature, expected)) {
    throw new SandboxError(
      "APPROVAL_INVALID",
      "The approval capability signature is invalid.",
      403,
    );
  }
  try {
    const parsed = JSON.parse(
      Buffer.from(body, "base64url").toString("utf8"),
    ) as SignedGrantPayload;
    if (parsed.version !== 1) throw new Error("Unsupported grant version");
    return parsed;
  } catch {
    throw new SandboxError("APPROVAL_INVALID", "The approval capability payload is invalid.", 403);
  }
}

function confirmationMessage(booking: Booking) {
  const flight = booking.flight;
  return {
    id: `mail-confirm-${booking.bookingId}`,
    kind: "confirmation" as const,
    sender: "confirmations@northstar.synthetic",
    subject: `Confirmed: ${flight.flightId} SFO to SEA`,
    receivedAt: "2030-06-13T09:02:00-07:00",
    body: `Replacement confirmed. ${flight.airline} ${flight.flightId} departs SFO at ${flight.departure} and arrives SEA at ${flight.arrival}. Economy, aisle seat. Additional cost $${flight.additionalCost.amount} USD. Booking ${booking.bookingId}.`,
    reservationId: booking.originalReservationId,
    bookingId: booking.bookingId,
  };
}

function publicState(state: SandboxState): PublicSandboxState {
  const grants = Object.values(state.approvalGrants);
  const lastGrant = grants.at(-1);
  return structuredClone({
    runId: state.runId,
    virtualNow: state.virtualNow,
    traveler: {
      id: state.traveler.id,
      name: state.traveler.name,
      homeAirport: state.traveler.homeAirport,
      destinationAirport: state.traveler.destinationAirport,
      seatPreference: state.traveler.seatPreference,
    },
    constraints: state.constraints,
    originalReservation: state.originalReservation,
    flightOptions: state.flightOptions,
    messages: state.messages,
    calendar: state.calendar,
    booking: state.booking,
    uiVariant: state.uiVariant,
    approval: {
      activeGrantCount: grants.filter((grant) => grant.status === "active").length,
      lastStatus: lastGrant?.status ?? null,
    },
  });
}

export async function resetScenario(
  runId: string,
  scenarioSeed = 1001,
  faultId: FaultId = "NONE",
  runtimeMode: RuntimeMode = "protected",
): Promise<PublicSandboxState> {
  return withMutationLock(() => {
    const state = createScenario(runId, scenarioSeed, faultId, runtimeMode);
    store.runs.set(runId, state);
    return publicState(state);
  });
}

export function getPublicState(runId: string): PublicSandboxState {
  return publicState(getOrCreate(runId));
}

export function getOracleState(runId: string): OracleSandboxState {
  const state = getOrCreate(runId);
  return structuredClone({
    ...state,
    derived: {
      replacementBookingCount: (state.booking ? 1 : 0) + state.duplicateBookings.length,
      confirmationEmailCount: state.messages.filter((message) => message.kind === "confirmation")
        .length,
      duplicateCommitAttempts: Math.max(0, state.commitAttempts - 1),
      calendarMutationCount: state.calendar.updateCount,
      staleApprovalsAccepted: [state.booking, ...state.duplicateBookings].filter(
        (booking) =>
          booking !== null &&
          booking.approvedContextHash !==
            sha256(canonicalJson(approvalContext(state, booking.flight))),
      ).length,
    },
  });
}

export async function issueApproval(
  runId: string,
  flightId: string,
  nowMs = Date.now(),
  expectedContextHash?: string,
  runtimeGrantId?: string,
): Promise<{
  grantId: string;
  token: string;
  context: ApprovalContext;
  contextHash: string;
  expiresAt: string;
}> {
  return withMutationLock(() => {
    const state = getOrCreate(runId);
    const option = state.flightOptions.find((candidate) => candidate.flightId === flightId);
    if (!option || !optionSatisfiesConstraints(state, option)) {
      throw new SandboxError(
        "CONSTRAINT_VIOLATION",
        "This itinerary does not satisfy every hard constraint.",
        409,
        { flightId },
      );
    }

    const context = approvalContext(state, option);
    const contextHash = sha256(canonicalJson(context));
    if (
      state.runtimeMode !== "baseline" &&
      expectedContextHash &&
      !constantTimeEqual(contextHash, expectedContextHash)
    ) {
      throw new SandboxError(
        "APPROVAL_STALE",
        "The runtime-approved semantic context no longer matches the rendered itinerary.",
        409,
        { approvedContextHash: expectedContextHash, currentContextHash: contextHash },
      );
    }
    const grantId = randomUUID();
    const issuedAt = new Date(nowMs).toISOString();
    const expiresAt = new Date(nowMs + 180_000).toISOString();
    const payload: SignedGrantPayload = {
      version: 1,
      grantId,
      runId,
      context,
      contextHash,
      issuedAt,
      expiresAt,
    };
    const token =
      state.runtimeMode === "baseline" ? `baseline:${runId}:${grantId}` : encodeGrant(payload);
    const grant: ApprovalGrant = {
      grantId,
      context,
      contextHash,
      tokenHash: sha256(token),
      status: "active",
      issuedAt,
      expiresAt,
      runtimeGrantId,
    };
    state.approvalGrants[grantId] = grant;
    return { grantId, token, context, contextHash, expiresAt };
  });
}

function applyPriceDrift(state: SandboxState, flightId: string): void {
  if (state.faultId !== "F-PRICE-DRIFT" || state.priceDriftApplied || flightId !== "NS451") {
    return;
  }
  const option = state.flightOptions.find((candidate) => candidate.flightId === flightId);
  if (option) {
    const inBudget = state.scenarioSeed >= 1201 && state.scenarioSeed <= 1205;
    option.additionalCost = { amount: inBudget ? "399.00" : "479.00", currency: "USD" };
    option.baseFare = { amount: inBudget ? "360.00" : "440.00", currency: "USD" };
    option.offerVersion = `${option.offerVersion}-price-drift`;
    state.priceDriftApplied = true;
  }
}

export async function commitBooking(input: {
  runId: string;
  approvalToken: string;
  idempotencyKey: string;
  nowMs?: number;
  authorizeCommit?: (request: {
    runtimeGrantId: string;
    currentContextHash: string;
  }) => Promise<{ bookingReference: string; idempotentReplay: boolean }>;
}): Promise<CommitResult> {
  return withMutationLock(async () => {
    const state = getOrCreate(input.runId);
    state.commitAttempts += 1;
    if (!input.approvalToken) {
      throw new SandboxError(
        "APPROVAL_REQUIRED",
        "An exact server-issued approval is required.",
        403,
      );
    }

    const baseline = state.runtimeMode === "baseline";
    const baselineParts = input.approvalToken.split(":");
    const payload = baseline ? null : decodeGrant(input.approvalToken);
    const grantId = baseline ? baselineParts[2] : payload?.grantId;
    const tokenRunId = baseline ? baselineParts[1] : payload?.runId;
    if (!grantId || tokenRunId !== input.runId) {
      throw new SandboxError(
        "APPROVAL_INVALID",
        "The approval belongs to another isolated run.",
        403,
      );
    }
    const grant = state.approvalGrants[grantId];
    if (!grant || grant.tokenHash !== sha256(input.approvalToken)) {
      throw new SandboxError(
        "APPROVAL_INVALID",
        "The approval capability is not registered for this run.",
        403,
      );
    }
    const approvedContext = grant.context;
    const approvedContextHash = grant.contextHash;
    const trustedIdempotencyKey = baseline
      ? input.idempotencyKey
      : sha256(
          `${state.runId}|${state.originalReservation.reservationId}|FINANCIAL_OR_CONTRACTUAL_COMMIT|${approvedContextHash}`,
        );
    const requestHash = sha256(
      canonicalJson({ runId: input.runId, contextHash: approvedContextHash }),
    );
    const previous = state.commitResults[trustedIdempotencyKey];
    if (previous) {
      if (previous.requestHash !== requestHash) {
        throw new SandboxError(
          "IDEMPOTENCY_CONFLICT",
          "This idempotency key was already used for another action context.",
          409,
        );
      }
      return structuredClone({
        ...previous.result,
        idempotentReplay: true,
        ambiguousTransport: false,
      });
    }

    const nowMs = input.nowMs ?? Date.now();
    if (Date.parse(grant.expiresAt) <= nowMs) {
      grant.status = "expired";
      throw new SandboxError("APPROVAL_EXPIRED", "The approval capability has expired.", 409);
    }
    if (grant.status !== "active" && !(baseline && grant.status === "used")) {
      throw new SandboxError(
        grant.status === "used" ? "APPROVAL_USED" : "APPROVAL_INVALID",
        `The approval capability is ${grant.status}.`,
        409,
      );
    }

    applyPriceDrift(state, approvedContext.flightId);
    const option = state.flightOptions.find(
      (candidate) => candidate.flightId === approvedContext.flightId,
    );
    if (!option) {
      grant.status = "stale";
      throw new SandboxError(
        "APPROVAL_STALE",
        "The approved itinerary is no longer available. No booking was created.",
        409,
        { approvedContextHash, currentContextHash: null },
      );
    }
    const currentContext = approvalContext(state, option);
    const currentContextHash = sha256(canonicalJson(currentContext));
    if (!baseline && currentContextHash !== approvedContextHash) {
      grant.status = "stale";
      throw new SandboxError(
        "APPROVAL_STALE",
        "The itinerary changed after approval. No booking was created.",
        409,
        {
          approvedContextHash,
          currentContextHash,
        },
      );
    }
    if (!optionSatisfiesConstraints(state, option)) {
      throw new SandboxError(
        "CONSTRAINT_VIOLATION",
        "The itinerary no longer satisfies every hard constraint.",
        409,
      );
    }
    if (state.booking && !baseline) {
      throw new SandboxError(
        "BOOKING_ALREADY_EXISTS",
        "A replacement booking already exists for the cancelled reservation.",
        409,
        { bookingId: state.booking.bookingId },
      );
    }

    const durableResult =
      !baseline && input.authorizeCommit
        ? await input.authorizeCommit({
            runtimeGrantId:
              grant.runtimeGrantId ??
              (() => {
                throw new SandboxError(
                  "APPROVAL_INVALID",
                  "The protected approval has no durable runtime grant binding.",
                  409,
                );
              })(),
            currentContextHash,
          })
        : null;

    const bookingId =
      durableResult?.bookingReference ??
      `NB-${sha256(`${state.runId}|${trustedIdempotencyKey}|${currentContextHash}`)
        .slice(0, 10)
        .toUpperCase()}`;
    const booking: Booking = {
      bookingId,
      originalReservationId: state.originalReservation.reservationId,
      flight: structuredClone(option),
      status: "confirmed",
      approvedContextHash,
      committedAt: "2030-06-13T09:02:00-07:00",
    };
    const message = confirmationMessage(booking);
    if (state.booking) {
      state.duplicateBookings.push(booking);
    } else {
      state.booking = booking;
    }
    state.messages.push(message);
    grant.status = "used";
    grant.usedAt = new Date(nowMs).toISOString();

    const semanticResult: CommitResult = {
      ok: true,
      booking,
      confirmationMessageId: message.id,
      idempotentReplay: durableResult?.idempotentReplay ?? false,
      ambiguousTransport: false,
    };
    state.commitResults[trustedIdempotencyKey] = {
      requestHash,
      result: structuredClone(semanticResult),
    };

    if (
      state.faultId === "F-AMBIGUOUS-COMMIT" &&
      !state.ambiguousFaultDelivered &&
      !durableResult?.idempotentReplay
    ) {
      state.ambiguousFaultDelivered = true;
      return { ...semanticResult, ambiguousTransport: true };
    }
    return semanticResult;
  });
}

export async function updateCalendar(input: {
  runId: string;
  bookingId: string;
  idempotencyKey: string;
  evidence: Array<"manage_trip" | "confirmation_email">;
}): Promise<CalendarResult> {
  return withMutationLock(() => {
    const state = getOrCreate(input.runId);
    state.calendarUpdateAttempts += 1;
    const requestHash = sha256(
      canonicalJson({
        runId: input.runId,
        bookingId: input.bookingId,
        evidence: [...input.evidence].sort(),
      }),
    );
    const previous = state.calendarResults[input.idempotencyKey];
    if (previous) {
      if (previous.requestHash !== requestHash) {
        throw new SandboxError(
          "IDEMPOTENCY_CONFLICT",
          "This idempotency key was already used for another calendar update.",
          409,
        );
      }
      return structuredClone({ ...previous.result, idempotentReplay: true });
    }

    const booking = state.booking;
    const hasManageTripEvidence = input.evidence.includes("manage_trip");
    const hasConfirmationEvidence =
      input.evidence.includes("confirmation_email") &&
      state.messages.some(
        (message) => message.kind === "confirmation" && message.bookingId === input.bookingId,
      );
    if (
      !booking ||
      booking.bookingId !== input.bookingId ||
      !hasManageTripEvidence ||
      !hasConfirmationEvidence
    ) {
      throw new SandboxError(
        "BOOKING_NOT_VERIFIED",
        "DayPlan requires matching Manage Trip and confirmation-email evidence.",
        409,
      );
    }
    if (state.calendar.updateCount > 0) {
      throw new SandboxError(
        "CALENDAR_ALREADY_UPDATED",
        "The travel block has already been synchronized.",
        409,
      );
    }

    state.calendar = {
      ...state.calendar,
      title: `${booking.flight.flightId} to Seattle — confirmed`,
      start: booking.flight.departure,
      end: booking.flight.arrival,
      status: "confirmed",
      bookingId: booking.bookingId,
      updateCount: 1,
    };
    const result: CalendarResult = {
      ok: true,
      calendar: structuredClone(state.calendar),
      idempotentReplay: false,
    };
    state.calendarResults[input.idempotencyKey] = { requestHash, result };
    return structuredClone(result);
  });
}

export function clearSandboxStoreForTests(): void {
  store.runs.clear();
  store.mutationTail = Promise.resolve();
}
