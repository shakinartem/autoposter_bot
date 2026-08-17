import { API_URL } from "@/lib/api";

export async function startTikTokOAuth(returnPath = "/accounts"): Promise<string> {
  const response = await fetch(
    `${API_URL}/oauth/tiktok/start?return_path=${encodeURIComponent(returnPath)}`,
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : `TikTok OAuth start failed: ${response.status}`,
    );
  }
  if (typeof payload?.authorization_url !== "string") {
    throw new Error("TikTok OAuth did not return an authorization URL");
  }
  return payload.authorization_url;
}
