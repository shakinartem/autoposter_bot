# Platform Requirements Draft — Video Publishing

## 1. TikTok — Content Posting API (v2)

### Authentication
- OAuth 2.0 via SPGUtils Worker
- Scopes: `user.info.basic`, `video.publish`, `video.upload`
- Access token lifetime: TODO: verify with official docs (seems to be ~24h with refresh_token)
- Refresh mechanism: `POST /api/oauth/tiktok/refresh` via SPGUtils

### Video Requirements
| Parameter | Value | Source |
|---|---|---|
| Max duration | 180 seconds (3 min) | TikTok API docs |
| Min duration | TODO: verify with official docs | |
| Max file size | 500 MB for direct upload | TikTok API docs |
| Supported formats | MP4, MOV, WEBM, AVI (recommended: MP4 with H.264) | TikTok API docs |
| Aspect ratio | 9:16 (vertical), 1:1 (square), 16:9 (horizontal) | TikTok API docs |
| Recommended resolution | 1080×1920 (vertical) | TikTok best practices |
| Min resolution | TODO: verify with official docs | |
| Max resolution | 3840×2160 (4K) | TikTok API docs |
| Max title length | 2200 characters | TikTok API docs |
| Max caption length | 2200 characters (same as title in current API) | TikTok API docs |

### Content Posting Modes
- `DIRECT_POST` — immediate publish (requires publisher privileges)
- `UPLOAD_DRAFT` — upload to drafts, manual review needed

### Interaction Settings
- `disable_comment`, `disable_duet`, `disable_stitch` — boolean
- `privacy_level`: `SELF_ONLY`, `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`
  - Availability depends on creator_info: `privacy_level_options`

### Rate Limits
- TODO: verify with official docs (app-level + user-level)

### Upload
- Chunked upload: chunk_size up to 64 MB, min 5 MB
- PULL_FROM_URL: source must be ownership-verified
- FILE_UPLOAD: directly via chunked PUT

### Error Codes (observed)
- `spam_risk_user_banned_from_posting` — Direct Post blocked for account
- `access_token_invalid`, `invalid_token`, `expired_token` — auth issues
- `invalid_param`, `invalid_params` — malformed request
- `unsupported_post_mode`, `unsupported_publish_mode` — mode not available

### Current Implementation Status
- ✅ Full direct_post flow
- ✅ Upload_draft fallback
- ✅ Local video upload with chunking
- ✅ Cloudinary URL fallback (optional)
- ✅ Status polling (12 attempts, 10s interval)
- ✅ Token refresh on 401
- ✅ Creator info query (privacy options, interaction flags)

---

## 2. Instagram Reels — Meta Graph API

### Authentication
- OAuth 2.0 via SPGUtils Worker (Meta / Instagram Business)
- Scopes: `instagram_business_content_publish`, `pages_show_list`, `business_management`
- Access token: long-lived (60 days), refreshable
- API version: v25.0 (configurable)

### Video Requirements
| Parameter | Value | Source |
|---|---|---|
| Max duration | 90 seconds (Reels) | Meta Graph API docs |
| Min duration | TODO: verify with official docs (usually 3s) | |
| Max file size | TODO: verify with official docs (up to 100MB API limit observed) | |
| Supported formats | MP4 with H.264 codec | Meta Graph API docs |
| Aspect ratio | 9:16 (vertical, recommended), 1:1, 4:5 | Meta Reels guidelines |
| Recommended resolution | 1080×1920 | Meta best practices |
| Min width | TODO: verify with official docs | |
| Max file size for Stories | TODO: verify with official docs | |
| Caption length | 2200 characters | Meta Graph API |
| Hashtags | First comment or caption | Best practice |

### Content Types (already supported)
- `instagram_feed_image` — single photo
- `instagram_video` — feed video / Reels
- `instagram_carousel` — multi-media (up to 10 items)
- `instagram_story_image` — story photo
- `instagram_story_video` — story video

### Reels-Specific Fields (Graph API)
- `media_type: REELS` — triggers Reels format
- `share_to_feed: true/false` — share to main feed
- `audio_name` — original audio name (optional)
- `cover_url` — custom cover image URL (optional)

### Rate Limits
- TODO: verify with official docs (200 calls per user per hour typical)

### Upload Flow (current)
1. Media → Cloudinary → public URL
2. `POST /{ig-user-id}/media` with `media_type=REELS`
3. Poll `GET /{creation-id}` until `status_code=FINISHED`
4. `POST /{ig-user-id}/media_publish` with `creation_id`

### Current Implementation Status
- ✅ Feed image, video, carousel, stories
- ✅ Reels via `media_type=REELS` with `share_to_feed=true`
- ✅ Status polling (20 attempts, 5s interval)
- ✅ Retry on rate limits and network errors
- ✅ Proxy support (SOCKS5/HTTP for graph.facebook.com)
- TODO: explicit `instagram_reels` content_type validation
- TODO: Reels-specific options (cover, audio)

---

## 3. YouTube Shorts — YouTube Data API v3

### Authentication
- OAuth 2.0 — not yet implemented
- Scopes: `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube`
- Access token: refreshable (typically 1h + refresh token)

### Video Requirements
| Parameter | Value | Source |
|---|---|---|
| Max duration | 60 seconds (Shorts) | YouTube Help / API docs |
| Aspect ratio | 9:16 (vertical), square allowed | YouTube Shorts guidelines |
| Max file size | 256 GB for verified accounts | YouTube Data API docs |
| Min file size | No minimum | |
| Supported formats | MP4, MOV, AVI, WMV, FLV, MKV, WebM | YouTube Help |
| Recommended codec | H.264, AAC audio | YouTube best practices |
| Recommended resolution | 1080×1920 (vertical) | YouTube Shorts guidelines |
| Max title length | 100 characters | YouTube Data API |
| Max description length | 5000 characters | YouTube Data API |
| Max tags | 500 characters total | YouTube Data API |

### Upload Methods
- Resumable upload (recommended): `POST /upload/youtube/v3/videos?uploadType=resumable`
- Simple upload: `POST /upload/youtube/v3/videos?uploadType=simple`
- Direct upload (not URL-based): video bytes in request body

### Video Categories (relevant)
- `22` — People & Blogs
- `24` — Entertainment
- `28` — Science & Technology

### Content Features
- `madeForKids` — boolean, required
- `embeddable` — boolean
- `publicStatsViewable` — boolean
- Privacy status: `public`, `unlisted`, `private`
- `publishAt` — scheduled publish (ISO 8601)
- `notifySubscribers` — boolean

### Rate Limits
- TODO: verify with official docs (10,000 units/day typical for standard API)
- One upload costs ~1600 units

### Implementation Required (no existing code)
- New `YouTubeShortsPublisher` class
- OAuth flow via SPGUtils Worker (or direct Google OAuth)
- Resumable upload with chunking
- Caption/title/description mapping
- Privacy and scheduling support
- New database table or options for YouTube channel accounts

### Current Implementation Status
- ❌ No YouTube integration exists yet
- ❌ No OAuth flow for Google
- ❌ No upload logic
- ❌ No Telegram admin UI for YouTube

---

## 4. Cross-Platform Comparison

| Feature | TikTok | Instagram Reels | YouTube Shorts |
|---|---|---|---|
| Max duration | 180s | 90s | 60s |
| Recommended aspect | 9:16 | 9:16 | 9:16 |
| Max file size | 500 MB | ~100 MB (TODO) | 256 GB |
| Upload via URL | ✅ (verified host) | ✅ (any public URL) | ❌ (direct upload only) |
| Status polling | ✅ | ✅ | ❌ (sync or webhook) |
| Scheduled publish | ✅ (via draft) | ❌ (via Graph API scheduling?) | ✅ (publishAt) |
| Chunked upload | ✅ | N/A | ✅ (resumable) |
| Retry logic | ✅ (3 attempts) | ✅ (3 attempts) | ❌ (not yet) |
| OAuth via Worker | ✅ | ✅ | ❌ (not yet) |

## 5. Future Platforms (to be researched)

- **VK Clips** — VK Video API with `video.save`, similar to existing VK publisher
- **OK (Одноклассники)** — OK API with media upload
- **Дзен** — Zen Publisher API (Yandex)
- **TenChat** — TenChat API
- **Threads** — Meta Threads API (currently text/image only, video pending)
- **MAX** — TODO: research API availability

---

## Notes

- All platform limits marked `TODO: verify with official docs` need confirmation before implementing validation rules.
- Platform limits can change without notice. Validation rules should be configurable (e.g., via settings or a JSON config file), not hardcoded.
- Some platforms (like YouTube) require direct upload — this means the bot server must handle large file transfers, which may require infrastructure changes (e.g., dedicated storage, background workers).