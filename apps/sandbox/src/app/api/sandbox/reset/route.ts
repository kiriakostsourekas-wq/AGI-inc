import { NextResponse } from "next/server";
import { jsonError, readJsonObject, requiredString, requireSandboxAdmin } from "@/lib/api";
import { resetScenario } from "@/lib/store";
import { isFaultId, SandboxError } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    requireSandboxAdmin(request);
    const body = await readJsonObject(request);
    const runId = requiredString(body, "runId");
    const seed = body.seed ?? 1001;
    const faultId = body.faultId ?? "NONE";
    const mode = body.mode ?? "protected";
    if (!Number.isSafeInteger(seed) || Number(seed) < 0) {
      throw new SandboxError("APPROVAL_INVALID", "seed must be a non-negative integer.", 400);
    }
    if (!isFaultId(faultId)) {
      throw new SandboxError(
        "APPROVAL_INVALID",
        "faultId is not a supported synthetic fault.",
        400,
      );
    }
    if (mode !== "baseline" && mode !== "protected") {
      throw new SandboxError("APPROVAL_INVALID", "mode must be baseline or protected.", 400);
    }
    const state = await resetScenario(runId, Number(seed), faultId, mode);
    return NextResponse.json({ ok: true, state });
  } catch (error) {
    return jsonError(error);
  }
}
