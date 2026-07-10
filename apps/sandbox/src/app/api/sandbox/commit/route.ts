import { randomUUID } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { jsonError, readJsonObject, requiredString } from "@/lib/api";
import { commitBooking } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function durableAuthorizer() {
  const runtimeBaseUrl = process.env.RUNTIME_INTERNAL_BASE_URL;
  const gatewayToken = process.env.SANDBOX_GATEWAY_TOKEN;
  const required =
    process.env.SANDBOX_REQUIRE_DURABLE_GATEWAY === "true" ||
    (process.env.NODE_ENV === "production" &&
      process.env.SANDBOX_REQUIRE_DURABLE_GATEWAY !== "false");
  if (!runtimeBaseUrl || !gatewayToken) {
    if (required) {
      throw new Error("Durable runtime gateway configuration is required.");
    }
    return undefined;
  }
  return async (payload: { runtimeGrantId: string; currentContextHash: string }) => {
    const response = await fetch(
      `${runtimeBaseUrl.replace(/\/$/, "")}/internal/v1/gateway/commit`,
      {
        method: "POST",
        redirect: "error",
        headers: {
          "content-type": "application/json",
          "x-sandbox-gateway-token": gatewayToken,
        },
        body: JSON.stringify({
          grant_id: payload.runtimeGrantId,
          current_context_hash: payload.currentContextHash,
        }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    const body = (await response.json()) as unknown;
    if (!response.ok || body === null || typeof body !== "object") {
      throw new Error(`Durable runtime gateway rejected the commit with HTTP ${response.status}.`);
    }
    const result = body as Record<string, unknown>;
    if (typeof result.booking_reference !== "string") {
      throw new Error("Durable runtime gateway returned malformed booking evidence.");
    }
    return {
      bookingReference: result.booking_reference,
      idempotentReplay: result.idempotent_replay === true,
    };
  };
}

export async function POST(request: NextRequest) {
  try {
    const body = await readJsonObject(request);
    const result = await commitBooking({
      runId: requiredString(body, "runId"),
      approvalToken:
        request.cookies.get("trust_sandbox_grant")?.value ??
        request.headers.get("x-sandbox-approval-token") ??
        "",
      idempotencyKey: request.headers.get("idempotency-key") ?? `request-${randomUUID()}`,
      authorizeCommit: durableAuthorizer(),
    });
    if (result.ambiguousTransport) {
      return NextResponse.json(
        {
          error: {
            code: "OUTCOME_UNKNOWN",
            message:
              "The synthetic airline did not return a final response. Verify external state before any retry.",
          },
        },
        { status: 504 },
      );
    }
    return NextResponse.json({ ...result, synthetic: true });
  } catch (error) {
    return jsonError(error);
  }
}
