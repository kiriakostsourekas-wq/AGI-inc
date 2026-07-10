import { NextResponse } from "next/server";
import { jsonError, readJsonObject, requireSandboxAdmin, requiredString } from "@/lib/api";
import { issueApproval } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    requireSandboxAdmin(request);
    const body = await readJsonObject(request);
    const grant = await issueApproval(
      requiredString(body, "runId"),
      requiredString(body, "flightId"),
      Date.now(),
      requiredString(body, "expectedContextHash"),
      typeof body.runtimeGrantId === "string" ? body.runtimeGrantId : undefined,
    );
    const response = NextResponse.json({
      ok: true,
      approval: {
        grantId: grant.grantId,
        context: grant.context,
        contextHash: grant.contextHash,
        expiresAt: grant.expiresAt,
      },
      synthetic: true,
    });
    response.cookies.set("trust_sandbox_grant", grant.token, {
      httpOnly: true,
      sameSite: "strict",
      secure: process.env.NODE_ENV === "production",
      path: "/api/sandbox/commit",
      maxAge: 180,
    });
    return response;
  } catch (error) {
    return jsonError(error);
  }
}
