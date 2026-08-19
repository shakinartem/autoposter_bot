const API_URL = "/api/autoposter";

export type PlatformCapability = {
  platform: string;
  content_types: string[];
  fields: Record<string, Record<string, unknown>>;
  features: Record<string, boolean>;
  limits: Record<string, number>;
};

export type Workspace = { id: number; name: string; owner_user_id: number; created_at: string };

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
  public_options: Record<string, unknown>;
  created_at: string;
};

export type ConnectionFieldSpec = {
  type: "text" | "secret" | "select";
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: string[];
  default?: unknown;
};

export type AccountConnectionSpec = {
  platform: string;
  title: string;
  destination: {
    label: string;
    placeholder?: string;
    required?: boolean;
  };
  fields: Record<string, ConnectionFieldSpec>;
  notes?: string;
};

export type OAuthProviderStatus = {
  platform: string;
  configured: boolean;
};

export type Publication = {
  id: string;
  variant_id: string;
  platform: string;
  account_id: number;
  destination: string | null;
  scheduled_at: string | null;
  status: string;
  provider_tracking_id: string | null;
  external_post_id: string | null;
  external_url: string | null;
  published_at: string | null;
  attempt_count: number;
  last_error_code: string | null;
  last_error_message: string | null;
  metadata: Record<string, unknown>;
};

export type OperationsOverview = {
  health: "healthy" | "degraded" | "critical" | string;
  generated_at: string;
  publication_statuses: Record<string, number>;
  queue: { due_count: number; oldest_due_at: string | null; lag_seconds: number; retry_scheduled: number };
  reconciliation: { processing: number; unknown_outcomes: number };
  attempts_24h: { total: number; failed: number; failure_rate: number };
  analytics: { latest_snapshot_at: string | null; lag_seconds: number | null };
  publishing: { latest_published_at: string | null; published_24h: number };
};

export type PublishResult = {
  ok: boolean;
  status: string;
  provider_tracking_id?: string | null;
  external_post_id?: string | null;
  external_url?: string | null;
  published_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable: boolean;
  rate_limit_reset_at?: string | null;
  raw_response?: Record<string, unknown>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message = typeof payload?.detail === "string" ? payload.detail : `API request failed: ${response.status}`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function uploadMedia(file: File): Promise<MediaAsset> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_URL}/media`, { method: "POST", body: form });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `Media upload failed: ${response.status}`);
  }
  return response.json() as Promise<MediaAsset>;
}

export function getWorkspace(): Promise<Workspace> { return request("/workspace"); }
export function listPlatforms(): Promise<Record<string, PlatformCapability>> { return request("/platforms"); }
export function listAccounts(): Promise<SocialAccount[]> { return request("/accounts"); }
export function listAccountConnectionSpecs(): Promise<Record<string, AccountConnectionSpec>> { return request("/account-connections"); }
export function listOAuthProviders(): Promise<Record<string, OAuthProviderStatus>> { return request("/oauth/providers"); }

export function startOAuth(platform: string, returnPath = "/accounts"): Promise<{ authorization_url: string }> {
  return request(`/oauth/${encodeURIComponent(platform)}/start?return_path=${encodeURIComponent(returnPath)}`, {
    method: "POST",
  });
}

export function createAccount(payload: {
  name: string;
  platform: string;
  destination?: string | null;
  options?: Record<string, unknown>;
}): Promise<SocialAccount> {
  return request("/accounts", { method: "POST", body: JSON.stringify(payload) });
}

export function updateAccount(
  accountId: number,
  payload: { name?: string; destination?: string | null; options?: Record<string, unknown> },
): Promise<SocialAccount> {
  return request(`/accounts/${accountId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function listContent(): Promise<ContentItem[]> { return request("/content"); }

export function createContent(payload: {
  title: string;
  body: string;
  cta?: string;
  links?: string[];
  hashtags?: string[];
  media?: MediaAsset[];
}): Promise<ContentItem> {
  return request("/content", { method: "POST", body: JSON.stringify(payload) });
}

export function updateContent(
  contentId: string,
  payload: Partial<Pick<ContentItem, "title" | "body" | "cta" | "links" | "hashtags" | "media">>,
): Promise<ContentItem> {
  return request(`/content/${contentId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function listVariants(contentId: string): Promise<PlatformVariant[]> { return request(`/content/${contentId}/variants`); }

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
  return request(`/content/${contentId}/variants/${platform}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function createPublication(
  variantId: string,
  payload: { account_id: number; destination?: string | null; scheduled_at?: string | null },
): Promise<Publication> {
  return request(`/variants/${variantId}/publications`, { method: "POST", body: JSON.stringify(payload) });
}

export function getOperationsOverview(): Promise<OperationsOverview> { return request("/operations/overview"); }

export function listPublications(status?: string): Promise<Publication[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/publications${query}`);
}

export function publishPublication(publicationId: string, dryRun = false): Promise<PublishResult> {
  return request(`/publications/${publicationId}/publish`, {
    method: "POST",
    body: JSON.stringify({ dry_run: dryRun }),
  });
}

export { API_URL };
