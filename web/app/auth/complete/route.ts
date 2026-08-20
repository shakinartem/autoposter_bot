import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const SESSION_COOKIE = "autoposter_session";

function safeReturnPath(value: unknown) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value.slice(0, 500);
}

export async function GET(request: NextRequest) {
  const grant = request.nextUrl.searchParams.get("grant") ?? "";
  if (!grant.startsWith("apg_") || grant.length < 30) {
    return NextResponse.redirect(new URL("/login?error=Invalid%20login%20grant", request.url));
  }

  try {
    const response = await fetch(`${BACKEND_URL}/auth/login-grant/exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grant }),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || typeof payload.session_token !== "string") {
      const detail = typeof payload.detail === "string" ? payload.detail : "Login grant exchange failed";
      return NextResponse.redirect(new URL(`/login?error=${encodeURIComponent(detail)}`, request.url));
    }

    const target = new URL(safeReturnPath(payload.return_path), request.url);
    const output = NextResponse.redirect(target);
    const expiresAt = Date.parse(String(payload.expires_at ?? ""));
    const maxAge = Number.isFinite(expiresAt)
      ? Math.max(300, Math.floor((expiresAt - Date.now()) / 1000))
      : 30 * 24 * 60 * 60;
    output.cookies.set({
      name: SESSION_COOKIE,
      value: payload.session_token,
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge,
    });
    return output;
  } catch {
    return NextResponse.redirect(
      new URL(`/login?error=${encodeURIComponent("Autoposter backend is unavailable")}`, request.url),
    );
  }
}
