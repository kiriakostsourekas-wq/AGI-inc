import type { SessionResponse } from "./runtime-api";

const SESSION_STORAGE_KEY = "trust-runtime.demo-session.v1";

export interface StoredRuntimeSession extends SessionResponse {}

export function readRuntimeSession(
  storage: Pick<Storage, "getItem" | "removeItem"> = window.sessionStorage,
  now = Date.now(),
): StoredRuntimeSession | undefined {
  const raw = storage.getItem(SESSION_STORAGE_KEY);
  if (!raw) return undefined;
  try {
    const value = JSON.parse(raw) as Partial<StoredRuntimeSession>;
    if (
      typeof value.session_id !== "string" ||
      typeof value.expires_at !== "string" ||
      !Number.isFinite(Date.parse(value.expires_at)) ||
      Date.parse(value.expires_at) <= now
    ) {
      storage.removeItem(SESSION_STORAGE_KEY);
      return undefined;
    }
    return value as StoredRuntimeSession;
  } catch {
    storage.removeItem(SESSION_STORAGE_KEY);
    return undefined;
  }
}

export function writeRuntimeSession(
  session: StoredRuntimeSession,
  storage: Pick<Storage, "setItem"> = window.sessionStorage,
): void {
  storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
}

export function clearRuntimeSession(
  storage: Pick<Storage, "removeItem"> = window.sessionStorage,
): void {
  storage.removeItem(SESSION_STORAGE_KEY);
}

export function configuredRuntime(): { enabled: boolean; baseUrl: string } {
  const enabled = process.env.NEXT_PUBLIC_LIVE_RUNS_ENABLED === "true";
  return { enabled, baseUrl: "/api/runtime" };
}
