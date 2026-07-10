import { NextResponse } from "next/server";
import { constantTimeEqual } from "./crypto";
import { SandboxError } from "./types";

export function jsonError(error: unknown): NextResponse {
  if (error instanceof SandboxError) {
    return NextResponse.json(
      {
        error: {
          code: error.code,
          message: error.message,
          details: error.details ?? null,
        },
      },
      { status: error.status },
    );
  }
  console.error("Synthetic sandbox request failed", error);
  return NextResponse.json(
    {
      error: {
        code: "INTERNAL_ERROR",
        message: "The synthetic sandbox could not complete the request.",
      },
    },
    { status: 500 },
  );
}

export async function readJsonObject(request: Request): Promise<Record<string, unknown>> {
  const value = (await request.json()) as unknown;
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new SandboxError("APPROVAL_INVALID", "A JSON object is required.", 400);
  }
  return value as Record<string, unknown>;
}

export function requiredString(body: Record<string, unknown>, field: string): string {
  const value = body[field];
  if (typeof value !== "string" || value.trim() === "") {
    throw new SandboxError("APPROVAL_INVALID", `${field} must be a non-empty string.`, 400);
  }
  return value;
}

export function requireSandboxAdmin(request: Request): void {
  const expected =
    process.env.SANDBOX_ADMIN_TOKEN ??
    (process.env.NODE_ENV === "production" ? "" : "local-sandbox-admin");
  const received = request.headers.get("x-sandbox-admin-token") ?? "";
  if (!expected || !constantTimeEqual(received, expected)) {
    throw new SandboxError("UNAUTHORIZED", "A valid sandbox admin token is required.", 401);
  }
}
