import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = process.env.AUTOPOSTER_API_KEY ?? "";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function proxy(request: NextRequest, context: RouteContext) {
  if (!API_KEY) {
    return NextResponse.json(
      { detail: "AUTOPOSTER_API_KEY is not configured on the web server" },
      { status: 503 },
    );
  }

  const { path } = await context.params;
  const backendPath = `/api/v1/${path.map(encodeURIComponent).join("/")}`;
  const target = `${BACKEND_URL}${backendPath}${request.nextUrl.search}`;
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${API_KEY}`);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const body = hasBody ? await request.arrayBuffer() : undefined;

  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
    const responseBody = await response.arrayBuffer();
    return new NextResponse(responseBody, {
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
