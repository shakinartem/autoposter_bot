import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

function safeReturnPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value.slice(0, 500);
}

export async function GET(request: NextRequest) {
  const returnPath = safeReturnPath(request.nextUrl.searchParams.get("return_path"));
  try {
    const response = await fetch(
      `${BACKEND_URL}/auth/telegram/start?return_path=${encodeURIComponent(returnPath)}`,
      { cache: "no-store" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || typeof payload.authorization_url !== "string") {
      const detail = typeof payload.detail === "string" ? payload.detail : "Telegram login is unavailable";
      return NextResponse.redirect(new URL(`/login?error=${encodeURIComponent(detail)}`, request.url));
    }
    return NextResponse.redirect(payload.authorization_url);
  } catch {
    return NextResponse.redirect(
      new URL(`/login?error=${encodeURIComponent("Autoposter backend is unavailable")}`, request.url),
    );
  }
}
