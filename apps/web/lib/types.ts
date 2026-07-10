export type FaultId =
  | "none"
  | "F-UI-DRIFT"
  | "F-PRICE-DRIFT"
  | "F-AMBIGUOUS-COMMIT"
  | "S-PROMPT-INJECTION"
  | "S-NO-COMPLIANT-OPTION";

export type TraceTone = "neutral" | "accent" | "success" | "warning" | "danger";

export interface FaultOption {
  id: FaultId;
  shortLabel: string;
  title: string;
  description: string;
  expectedBehavior: string;
  seedRange: string;
  advanced?: boolean;
}

export interface TraceEvent {
  id: string;
  timestamp: string;
  phase: string;
  title: string;
  summary: string;
  policy: string;
  verification: string;
  tone: TraceTone;
  source: string;
}

export interface ReplayFrame {
  id: string;
  chapter: string;
  app: "GoMail" | "Northstar Air" | "DayPlan" | "Runtime";
  path: string;
  status: string;
  title: string;
  description: string;
  evidence: string;
  tone: TraceTone;
}
