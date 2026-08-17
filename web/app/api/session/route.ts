import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const SESSION_COOKIE = "autoposter_session";

async function identity(token: string) {
  return fetch(`${BACKEND_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
}

export async function GET(request: NextRequest) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  try {
    const response = await identity(token);
    if (!response.ok) {
      const output = NextResponse.json({ authenticated: false }, { status: 401 });
      output.cookies.delete(SESSION_COOKIE);
      return output;
    }
    const profile = await response.json();
    return NextResponse.json({ authenticated: true, ...profile });
  } catch {
    return NextResponse.json({ detail: "Autoposter backend is unavailable" }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  let token = "";
  try {
    const payload = (await request.json()) as { token?: unknown };
    token = typeof payload.token === "string" ? payload.token.trim() : "";
  } catch {
    return NextResponse.json({ detail: "Invalid JSON payload" }, { status: 400 });
  }

  if (!token.startsWith("aps_") || token.length < 40) {
    return NextResponse.json({ detail: "Invalid Autoposter session token" }, { status: 400 });
  }

  try {
    const response = await identity(token);
    if (!response.ok) {
      return NextResponse.json({ detail: "Invalid or expired session" }, { status: 401 });
    }
    const profile = await response.json();
    const output = NextResponse.json({ authenticated: true, ...profile });
    output.cookies.set({
      name: SESSION_COOKIE,
      value: token,
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: 30 * 24 * 60 * 60,
    });
    return output;
  } catch {
    return NextResponse.json({ detail: "Autoposter backend is unavailable" }, { status: 502 });
  }
}

export async function DELETE() {
  const output = NextResponse.json({ authenticated: false });
  output.cookies.delete(SESSION_COOKIE);
  return output;
}
