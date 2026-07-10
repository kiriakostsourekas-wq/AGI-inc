export const VIRTUAL_NOW = "2030-06-13T09:00:00-07:00";

export const FAULT_IDS = [
  "NONE",
  "F-UI-DRIFT",
  "F-PRICE-DRIFT",
  "F-AMBIGUOUS-COMMIT",
  "S-PROMPT-INJECTION",
  "S-NO-COMPLIANT-OPTION",
] as const;

export type FaultId = (typeof FAULT_IDS)[number];

export type RuntimeMode = "baseline" | "protected";

export type CurrencyAmount = {
  amount: string;
  currency: "USD";
};

export type Traveler = {
  id: string;
  name: string;
  homeAirport: string;
  destinationAirport: string;
  seatPreference: "aisle";
  paymentSecretRef: string;
};

export type TripConstraints = {
  origin: "SFO";
  destination: "SEA";
  cabin: "economy";
  departOnOrAfter: string;
  arriveOnOrBefore: string;
  seatType: "aisle";
  maximumAdditionalCost: CurrencyAmount;
};

export type Reservation = {
  reservationId: string;
  airline: string;
  flightId: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  cabin: "economy";
  seat: string;
  seatType: "aisle";
  status: "cancelled";
};

export type FlightOption = {
  flightId: string;
  airline: string;
  marketingCarrier: string;
  operatingCarrier: string;
  offerVersion: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  stopCount: number;
  cabin: "economy";
  fareClass: string;
  seatType: "aisle" | "window" | "none";
  seatAvailable: boolean;
  additionalCost: CurrencyAmount;
  baseFare: CurrencyAmount;
  taxesAndFees: CurrencyAmount;
};

export type Booking = {
  bookingId: string;
  originalReservationId: string;
  flight: FlightOption;
  status: "confirmed";
  approvedContextHash: string;
  committedAt: string;
};

export type MailMessage = {
  id: string;
  kind: "cancellation" | "confirmation";
  sender: string;
  subject: string;
  receivedAt: string;
  body: string;
  reservationId: string;
  bookingId?: string;
};

export type CalendarBlock = {
  id: string;
  title: string;
  start: string;
  end: string;
  status: "cancelled" | "confirmed";
  reservationId: string;
  bookingId?: string;
  updateCount: number;
};

export type ApprovalContext = {
  runId: string;
  travelerId: string;
  reservationId: string;
  flightId: string;
  airline: string;
  marketingCarrier: string;
  operatingCarrier: string;
  offerVersion: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  stopCount: number;
  cabin: "economy";
  fareClass: string;
  seatType: "aisle";
  amount: string;
  baseFareAmount: string;
  taxesAndFeesAmount: string;
  currency: "USD";
};

export type ApprovalGrant = {
  grantId: string;
  context: ApprovalContext;
  contextHash: string;
  tokenHash: string;
  status: "active" | "used" | "rejected" | "expired" | "stale";
  issuedAt: string;
  expiresAt: string;
  usedAt?: string;
  runtimeGrantId?: string;
};

export type CommitResult = {
  ok: true;
  booking: Booking;
  confirmationMessageId: string;
  idempotentReplay: boolean;
  ambiguousTransport: boolean;
};

export type CalendarResult = {
  ok: true;
  calendar: CalendarBlock;
  idempotentReplay: boolean;
};

export type StoredIdempotentResult<T> = {
  requestHash: string;
  result: T;
};

export type SandboxState = {
  runId: string;
  scenarioSeed: number;
  faultId: FaultId;
  runtimeMode: RuntimeMode;
  virtualNow: string;
  traveler: Traveler;
  constraints: TripConstraints;
  originalReservation: Reservation;
  flightOptions: FlightOption[];
  messages: MailMessage[];
  calendar: CalendarBlock;
  booking: Booking | null;
  duplicateBookings: Booking[];
  uiVariant: "standard" | "drifted";
  approvalGrants: Record<string, ApprovalGrant>;
  commitResults: Record<string, StoredIdempotentResult<CommitResult>>;
  calendarResults: Record<string, StoredIdempotentResult<CalendarResult>>;
  commitAttempts: number;
  calendarUpdateAttempts: number;
  priceDriftApplied: boolean;
  ambiguousFaultDelivered: boolean;
};

export type PublicSandboxState = Omit<
  SandboxState,
  | "scenarioSeed"
  | "faultId"
  | "runtimeMode"
  | "approvalGrants"
  | "commitResults"
  | "calendarResults"
  | "commitAttempts"
  | "calendarUpdateAttempts"
  | "priceDriftApplied"
  | "ambiguousFaultDelivered"
  | "duplicateBookings"
  | "traveler"
> & {
  traveler: Omit<Traveler, "paymentSecretRef">;
  approval: {
    activeGrantCount: number;
    lastStatus: ApprovalGrant["status"] | null;
  };
};

export type OracleSandboxState = SandboxState & {
  derived: {
    replacementBookingCount: number;
    confirmationEmailCount: number;
    duplicateCommitAttempts: number;
    calendarMutationCount: number;
    staleApprovalsAccepted: number;
  };
};

export type SandboxErrorCode =
  | "APPROVAL_REQUIRED"
  | "APPROVAL_INVALID"
  | "APPROVAL_EXPIRED"
  | "APPROVAL_STALE"
  | "APPROVAL_USED"
  | "CONSTRAINT_VIOLATION"
  | "IDEMPOTENCY_CONFLICT"
  | "BOOKING_ALREADY_EXISTS"
  | "BOOKING_NOT_VERIFIED"
  | "CALENDAR_ALREADY_UPDATED"
  | "DURABLE_GATEWAY_UNAVAILABLE"
  | "RUN_NOT_FOUND"
  | "UNAUTHORIZED";

export class SandboxError extends Error {
  constructor(
    public readonly code: SandboxErrorCode,
    message: string,
    public readonly status: number,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "SandboxError";
  }
}

export function isFaultId(value: unknown): value is FaultId {
  return typeof value === "string" && FAULT_IDS.includes(value as FaultId);
}
