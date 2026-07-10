import { NextResponse } from "next/server";
import { jsonError, readJsonObject, requiredString } from "@/lib/api";
import { updateCalendar } from "@/lib/store";
import { SandboxError } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const body = await readJsonObject(request);
    const idempotencyKey = request.headers.get("idempotency-key") ?? "";
    if (!idempotencyKey) {
      throw new SandboxError(
        "APPROVAL_INVALID",
        "Idempotency-Key is required for a calendar mutation.",
        400,
      );
    }
    const evidenceValue = body.evidence;
    if (!Array.isArray(evidenceValue)) {
      throw new SandboxError(
        "BOOKING_NOT_VERIFIED",
        "evidence must contain both required observable channels.",
        400,
      );
    }
    const evidence = evidenceValue.filter(
      (value): value is "manage_trip" | "confirmation_email" =>
        value === "manage_trip" || value === "confirmation_email",
    );
    const result = await updateCalendar({
      runId: requiredString(body, "runId"),
      bookingId: requiredString(body, "bookingId"),
      idempotencyKey,
      evidence,
    });
    return NextResponse.json({ ...result, synthetic: true });
  } catch (error) {
    return jsonError(error);
  }
}
