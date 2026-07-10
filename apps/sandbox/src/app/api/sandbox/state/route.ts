import { NextResponse } from "next/server";
import { jsonError, requireSandboxAdmin } from "@/lib/api";
import { getOracleState, getPublicState } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const runId = url.searchParams.get("runId") || "demo";
    const view = url.searchParams.get("view") || "public";
    if (view === "oracle") {
      requireSandboxAdmin(request);
      return NextResponse.json({ ok: true, state: getOracleState(runId) });
    }
    return NextResponse.json({ ok: true, state: getPublicState(runId) });
  } catch (error) {
    return jsonError(error);
  }
}
