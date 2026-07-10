"use client";

import { useCallback, useEffect, useState } from "react";
import type { PublicSandboxState } from "@/lib/types";

type StateResponse = {
  ok: true;
  state: PublicSandboxState;
};

export function useSandboxState(runId: string, pollMs = 2_000) {
  const [state, setState] = useState<PublicSandboxState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/sandbox/state?runId=${encodeURIComponent(runId)}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`State request failed (${response.status})`);
      const payload = (await response.json()) as StateResponse;
      setState(payload.state);
      setError(null);
    } catch (requestError) {
      setError(
        requestError instanceof Error ? requestError.message : "Unable to load synthetic state.",
      );
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), pollMs);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [pollMs, refresh]);

  return { state, error, loading, refresh };
}

export async function readApiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string };
    };
    const code = body.error?.code ? `${body.error.code}: ` : "";
    return `${code}${body.error?.message ?? `Request failed (${response.status})`}`;
  } catch {
    return `Request failed (${response.status})`;
  }
}
