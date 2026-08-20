import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = process.env.AUTOPOSTER_API_KEY ?? "";
const AUTH_MODE = (process.env.AUTOPOSTER_WEB_AUTH_MODE ?? "service").trim().toLowerCase();
const SESSION_COOKIE = "autoposter_session";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

type StreamingRequestInit = RequestInit & { duplex?: "half" };

function credentialFor(request: NextRequest): string | null {
  if (AUTH_MODE === "session") {
    return request.cookies.get(SESSION_COOKIE)?.value ?? null;
  }
  if (AUTH_MODE === "service") {
    return API_KEY || null;
  }
  return null;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const credential = credentialFor(request);
  if (!credential) {
    const status = AUTH_MODE === "session" ? 401 : 503;
    return NextResponse.json(
      {
        detail:
          AUTH_MODE === "session"
            ? "Authenticated Autoposter session required"
            : "AUTOPOSTER_API_KEY is not configured for service auth mode",
      },
      { status },
    );
  }
  if (!["service", "session"].includes(AUTH_MODE)) {
    return NextResponse.json(
      { detail: "AUTOPOSTER_WEB_AUTH_MODE must be either service or session" },
      { status: 503 },
    );
  }

  const { path } = await context.params;
  const backendPath = `/api/v1/${path.map(encodeURIComponent).join("/")}`;
  const target = `${BACKEND_URL}${backendPath}${request.nextUrl.search}`;
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${credential}`);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const init: StreamingRequestInit = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    cache: "no-store",
  };
  if (hasBody && request.body) init.duplex = "half";

  try {
    const response = await fetch(target, init);
    return new NextResponse(response.body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        detail: "Autoposter backend is unavailable",
        error: error instanceof Error ? error.message : "unknown error",
      },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
