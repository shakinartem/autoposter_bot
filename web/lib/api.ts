const API_URL =
  process.env.NEXT_PUBLIC_AUTOPOSTER_API_URL?.replace(/\/$/, "") ??
  "http://localhost:8000";

export type PlatformCapability = {
  platform: string;
  content_types: string[];
  fields: Record<string, Record<string, unknown>>;
  features: Record<string, boolean>;
  limits: Record<string, number>;
};

export type MediaAsset = {
  id?: string | null;
  source: string;
  media_type: string;
  alt_text?: string;
  metadata?: Record<string, unknown>;
};

export type ContentItem = {
  id: string;
  title: string;
  body: string;
  cta: string;
  links: string[];
  hashtags: string[];
  media: MediaAsset[];
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type PlatformVariant = {
  id: string;
  content_id: string;
  platform: string;
  title: string;
  text: string;
  media: MediaAsset[];
  fields: Record<string, unknown>;
  sync_with_master: boolean;
  revision: number;
  metadata: Record<string, unknown>;
};

export type SocialAccount = {
  id: number;
  owner_user_id: number | null;
  name: string;
  platform: string;
  destination: string | null;
  created_at: string;
};

export type Publication = {
  id: string;
  variant_id: string;
  platform: string;
  account_id: number;
  destination: string | null;
  scheduled_at: string | null;
  status: string;
  external_post_id: string | null;
  external_url: string | null;
  published_at: string | null;
  attempt_count: number;
  last_error_code: string | null;
  last_error_message: string | null;
  metadata: Record<string, unknown>;
};

export type PublishResult = {
  ok: boolean;
  status: string;
  external_post_id?: string | null;
  external_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message =
      typeof payload?.detail === "string"
        ? payload.detail
        : `API request failed: ${response.status}`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function listPlatforms(): Promise<Record<string, PlatformCapability>> {
  return request("/api/v1/platforms");
}

export function listAccounts(): Promise<SocialAccount[]> {
  return request("/api/v1/accounts");
}

export function listContent(): Promise<ContentItem[]> {
  return request("/api/v1/content");
}

export function createContent(payload: {
  title: string;
  body: string;
  cta?: string;
  links?: string[];
  hashtags?: string[];
  media?: MediaAsset[];
}): Promise<ContentItem> {
  return request("/api/v1/content", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateContent(
  contentId: string,
  payload: Partial<Pick<ContentItem, "title" | "body" | "cta" | "links" | "hashtags" | "media">>,
): Promise<ContentItem> {
  return request(`/api/v1/content/${contentId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function listVariants(contentId: string): Promise<PlatformVariant[]> {
  return request(`/api/v1/content/${contentId}/variants`);
}

export function upsertVariant(
  contentId: string,
  platform: string,
  payload: {
    title?: string;
    text?: string;
    media?: MediaAsset[];
    fields?: Record<string, unknown>;
    sync_with_master?: boolean;
  },
): Promise<PlatformVariant> {
  return request(`/api/v1/content/${contentId}/variants/${platform}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function createPublication(
  variantId: string,
  payload: { account_id: number; destination?: string | null; scheduled_at?: string | null },
): Promise<Publication> {
  return request(`/api/v1/variants/${variantId}/publications`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function publishPublication(
  publicationId: string,
  dryRun = false,
): Promise<PublishResult> {
  return request(`/api/v1/publications/${publicationId}/publish`, {
    method: "POST",
    body: JSON.stringify({ dry_run: dryRun }),
  });
}

export { API_URL };
