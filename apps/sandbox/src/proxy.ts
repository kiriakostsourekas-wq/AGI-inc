import { type NextRequest, NextResponse } from "next/server";

const hostRoutes: Record<string, string> = {
  gomail: "/gomail",
  northstar: "/northstar",
  dayplan: "/dayplan",
};

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname === "/favicon.ico" ||
    Object.values(hostRoutes).some(
      (route) => pathname === route || pathname.startsWith(`${route}/`),
    )
  ) {
    return NextResponse.next();
  }

  const hostname = (request.headers.get("host") ?? "").split(":")[0]?.toLowerCase();
  const subdomain = hostname?.split(".")[0] ?? "";
  const route = hostRoutes[subdomain];
  if (!route) return NextResponse.next();

  const destination = request.nextUrl.clone();
  destination.pathname = pathname === "/" ? route : `${route}${pathname}`;
  return NextResponse.rewrite(destination);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
