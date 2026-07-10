import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { mutationRequestIsSameOrigin } from "@/lib/runtime-proxy-policy";

const SESSION_COOKIE = "trust_demo_session";
const MAX_BODY_BYTES = 256 * 1024;
const runtimeBaseUrl = normalizeRuntimeBase(
  process.env.RUNTIME_BASE_URL ?? "http://localhost:8000",
);

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

function normalizeRuntimeBase(value: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:")
    throw new Error("RUNTIME_BASE_URL must use HTTP or HTTPS.");
  parsed.pathname = parsed.pathname.replace(/\/$/, "");
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

function errorResponse(status: number, code: string, message: string): NextResponse {
  return NextResponse.json({ version: "1.0.0", error: { code, message } }, { status });
}

function allowedPath(path: string[]): boolean {
  return (
    path.length > 0 &&
    (path[0] === "v1" || (path.length === 1 && (path[0] === "healthz" || path[0] === "readyz")))
  );
}

function isSessionCreation(path: string[], method: string): boolean {
  return method === "POST" && path.length === 2 && path[0] === "v1" && path[1] === "sessions";
}

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path } = await context.params;
  if (!allowedPath(path))
    return errorResponse(404, "PROXY_PATH_NOT_ALLOWED", "Runtime path is not public.");
  const mutation = request.method === "POST";
  if (mutation && !mutationRequestIsSameOrigin(request.headers, request.url)) {
    return errorResponse(
      403,
      "CSRF_CHECK_FAILED",
      "Mutation request failed the same-origin check.",
    );
  }
  const creatingSession = isSessionCreation(path, request.method);
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value;
  if (!creatingSession && path[0] === "v1" && !sessionToken) {
    return errorResponse(401, "SESSION_REQUIRED", "The demo session is missing or expired.");
  }

  const upstreamHeaders = new Headers();
  for (const header of [
    "Accept",
    "Content-Type",
    "Idempotency-Key",
    "If-Match",
    "Last-Event-ID",
    "Cache-Control",
  ]) {
    const value = request.headers.get(header);
    if (value) upstreamHeaders.set(header, value);
  }
  if (sessionToken) upstreamHeaders.set("X-Demo-Session-Token", sessionToken);

  let body: ArrayBuffer | undefined;
  if (mutation) {
    const declared = Number(request.headers.get("Content-Length") ?? 0);
    if (declared > MAX_BODY_BYTES)
      return errorResponse(413, "REQUEST_TOO_LARGE", "Request body exceeds 256 KB.");
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES)
      return errorResponse(413, "REQUEST_TOO_LARGE", "Request body exceeds 256 KB.");
  }

  const upstreamUrl = `${runtimeBaseUrl}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: upstreamHeaders,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return errorResponse(503, "RUNTIME_UNAVAILABLE", "The runtime service could not be reached.");
  }

  if (creatingSession && upstream.ok) {
    let payload: { session_id?: unknown; session_token?: unknown; expires_at?: unknown };
    try {
      payload = (await upstream.json()) as typeof payload;
    } catch {
      return errorResponse(
        502,
        "INVALID_RUNTIME_RESPONSE",
        "Runtime returned a malformed session response.",
      );
    }
    if (
      typeof payload.session_id !== "string" ||
      typeof payload.session_token !== "string" ||
      payload.session_token.length < 16 ||
      typeof payload.expires_at !== "string" ||
      !Number.isFinite(Date.parse(payload.expires_at))
    ) {
      return errorResponse(
        502,
        "INVALID_RUNTIME_RESPONSE",
        "Runtime returned an incomplete session response.",
      );
    }
    const response = NextResponse.json(
      { session_id: payload.session_id, expires_at: payload.expires_at },
      { status: upstream.status },
    );
    response.cookies.set({
      name: SESSION_COOKIE,
      value: payload.session_token,
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      expires: new Date(payload.expires_at),
    });
    response.headers.set("Cache-Control", "no-store");
    return response;
  }

  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  for (const header of ["Content-Type", "Retry-After", "Content-Length", "Content-Disposition"]) {
    const value = upstream.headers.get(header);
    if (value) responseHeaders.set(header, value);
  }
  if (upstream.headers.get("Content-Type")?.startsWith("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export async function GET(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context);
}
