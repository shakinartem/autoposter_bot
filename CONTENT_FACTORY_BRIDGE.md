# Content Factory ↔ Autoposter production bridge

This release connects Content Factory to Autoposter Content OS 0.12 without merging their domains.

## Contract

Production packages use `content-package/1.1` and include immutable lineage: content version, Factory delivery id, generation run, batch/rubric when present, prompt version/hash, model/router decision, and experiment exposure. `content-package/1.0` remains parser-compatible only for migration; Factory excludes lineage-incomplete publications from allocation learning.

## Flow

1. Factory produces and human-approves canonical content.
2. Factory sends `content-package/1.1` with delivery-scoped `Idempotency-Key`.
3. Autoposter validates token, schema and idempotency before side effects.
4. Factory media is copied into Autoposter-owned durable storage through the SSRF-safe downloader.
5. Autoposter persists master content and platform variants in the mapped workspace.
6. Autoposter schedules/publishes and collects analytics.
7. Analytics are written to the durable feedback outbox and returned to Factory.
8. Factory verifies callback lineage and payload SHA-256 against its immutable delivery before learning.

## Required secret pairing

Factory:
```env
AUTOPOSTER_URL=http://autoposter-api:8000/api/v1/integrations/content-factory/packages
AUTOPOSTER_TOKEN=<delivery-secret>
PERFORMANCE_INGEST_TOKEN=<performance-secret>
```

Autoposter:
```env
CONTENT_FACTORY_INGEST_TOKEN=<delivery-secret>
CONTENT_FACTORY_PERFORMANCE_URL=http://content-factory-api:8000/performance/ingest
CONTENT_FACTORY_PERFORMANCE_TOKEN=<performance-secret>
CONTENT_FACTORY_DEFAULT_WORKSPACE_ID=1
CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST=true
CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS=<factory-assets-host>
CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS=false
```

The delivery and performance secrets must match their paired values exactly.

## Security boundary

Production startup fails closed unless PostgreSQL, encrypted credential keys, session auth, explicit hosts/origins, Telegram OIDC, bridge tokens and the Factory media allowlist are configured safely. Media ingestion rejects redirects, private/reserved addresses, oversized payloads and MIME/signature mismatches.

## Idempotency

- same delivery key + same payload → same receipt;
- same key + changed payload → HTTP 409;
- a new approved Factory version creates a new delivery id/key;
- PostgreSQL advisory locking serializes concurrent ingest attempts for the same key.

## Acceptance

After server deployment:
1. send one media-free 1.1 package and replay it;
2. verify changed-payload replay returns 409;
3. send one image and confirm the Autoposter copy survives Factory URL expiry;
4. publish one real platform variant;
5. collect one numeric analytics milestone;
6. confirm it reaches Factory exactly once with the exact delivery/prompt/model lineage.

The final two steps require real provider/social credentials and therefore are operator acceptance, not something CI can fake.
