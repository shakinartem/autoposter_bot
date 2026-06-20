# Video Publishing Core — Architecture Plan

## 1. Overview

This document describes the proposed architecture for a unified video publishing core.
The goal is to extend the existing autoposter to support short video publishing
(TikTok, Instagram Reels, YouTube Shorts, VK Clips, etc.) with a clean, extensible
publisher interface and centralized media/video management.

**Principle: do NOT break existing functionality.**
All new code should be additive. Existing publishers, database tables, and Telegram
flows must continue to work unchanged.

## 2. Proposed Data Models

### 2.1 `MediaPost` (replacing / extending current `jobs` table)

Represents a single post (text + media targets).

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_user_id` | INTEGER FK→users | |
| `external_id` | TEXT UNIQUE | Client-generated UUID |
| `title` | TEXT | Short title, max 2200 chars |
| `text` | TEXT | Full caption/description |
| `status` | TEXT | See statuses below |
| `metadata_json` | TEXT | JSON blob |
| `scheduled_at` | TEXT (ISO) | Null = immediate |
| `published_at` | TEXT (ISO) | Actual publish time |
| `created_at` | TEXT (ISO) | |
| `updated_at` | TEXT (ISO) | |

**Statuses:**
- `draft` — being composed
- `ready` — ready to schedule/publish
- `scheduled` — has non-null `scheduled_at` in future
- `queued` — picked up by scheduler
- `publishing` — at least one platform attempt in progress
- `published` — all targets successfully published
- `failed` — all targets failed, no retries remain
- `cancelled` — user cancelled

### 2.2 `MediaAsset` (replacing / extending current `job_media` table)

Represents a single media file (image or video) with its metadata.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `post_id` | INTEGER FK→media_posts | |
| `source` | TEXT | Local path or remote URL |
| `media_type` | TEXT | `image`, `video`, `auto` |
| `order_index` | INTEGER | Display order |
| `original_filename` | TEXT | From Telegram |
| `file_size` | INTEGER | Bytes |
| `mime_type` | TEXT | e.g. `video/mp4` |
| `duration_seconds` | REAL | Video duration |
| `width` | INTEGER | Resolution |
| `height` | INTEGER | Resolution |
| `aspect_ratio` | TEXT | e.g. `9:16`, `1:1`, `16:9` |
| `cloudinary_public_id` | TEXT | If uploaded to Cloudinary |
| `cloudinary_url` | TEXT | Public URL |
| `processed` | INTEGER | 0/1 — transcoded/verified |
| `options_json` | TEXT | Extra options |
| `created_at` | TEXT (ISO) | |

### 2.3 `PublicationTarget`

Links a post to a platform account.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `post_id` | INTEGER FK→media_posts | |
| `account_id` | INTEGER FK→accounts | |
| `platform` | TEXT | denormalized from account |
| `options_json` | TEXT | Per-target overrides |
| `created_at` | TEXT (ISO) | |

### 2.4 `PublicationAttempt`

Tracks each attempt to publish to a specific target.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `post_id` | INTEGER FK→media_posts | |
| `target_id` | INTEGER FK→publication_targets | |
| `platform` | TEXT | |
| `attempt_number` | INTEGER | 1-based |
| `status` | TEXT | `pending`, `publishing`, `success`, `failed` |
| `external_id` | TEXT | Platform's post/video ID |
| `error_code` | TEXT | Platform error code |
| `error_detail` | TEXT | Human-readable |
| `retry_at` | TEXT (ISO) | If retry scheduled |
| `started_at` | TEXT (ISO) | |
| `finished_at` | TEXT (ISO) | |
| `created_at` | TEXT (ISO) | |

## 3. Unified Publisher Interface

```python
class VideoPublisher(ABC):
    platform: str  # e.g. "tiktok", "instagram_reels", "youtube_shorts"

    @abstractmethod
    def validate_media(self, assets: list[MediaAsset]) -> ValidationResult: ...

    @abstractmethod
    def validate_post(self, post: MediaPost, assets: list[MediaAsset]) -> ValidationResult: ...

    @abstractmethod
    def publish(
        self,
        post: MediaPost,
        assets: list[MediaAsset],
        target: PublicationTarget,
        dry_run: bool = False,
    ) -> PublicationAttempt: ...

    @abstractmethod
    def check_status(self, external_id: str) -> StatusResult: ...

    def max_retries(self) -> int:
        return 3

    def retry_delay(self, attempt: int) -> int:
        return [60, 300, 900][min(attempt, 2)]
```

## 4. Validation Service

```python
class VideoValidationService:
    def validate(post: MediaPost, assets: list[MediaAsset], platform: str) -> list[ValidationError]:
        """
        Checks:
        - Video duration within platform limits
        - Aspect ratio requirements
        - File size limits
        - Codec / format support
        - Caption length limits
        - Hashtag / mention rules
        - Content policy restrictions
        """
        ...
```

## 5. Queue Service (extending current scheduler)

```python
class VideoQueueService:
    def enqueue(post_id: int, scheduled_at: datetime | None): ...
    def dequeue() -> MediaPost | None: ...
    def mark_attempt(post_id: int, target_id: int, result: PublicationAttempt): ...
    def retry_failed(max_attempts: int = 3): ...
```

## 6. Retry Logic

- Each `PublicationAttempt` records attempt number
- Exponential backoff based on platform-specific rules
- After `max_retries` attempts → status = `failed`
- Separate `retry_at` field for delayed retry
- Admin can manually retry from Telegram bot

## 7. Error Handling

- `VideoPublishError` with fields: `platform`, `stage`, `code`, `message`, `retryable`
- Structured logging to `publication_attempts` table
- Telegram notification on failures (using existing `notifier.py`)
- Centralized error categorization:
  - `AUTH_ERROR` — token expired/revoked
  - `VALIDATION_ERROR` — media doesn't meet requirements
  - `RATE_LIMIT` — API rate limit
  - `SERVER_ERROR` — platform temporary error
  - `UNKNOWN` — unexpected

## 8. Minimal Safe Implementation Plan

### Phase 1 (current — audit complete)
- [x] Architecture audit
- [x] Current state documented
- [x] Weak points identified

### Phase 2 — add media metadata storage
- [ ] Add `duration_seconds`, `width`, `height`, `aspect_ratio` columns to `job_media` (or new `media_assets` table)
- [ ] Extract metadata on Telegram video download using `ffprobe` (optional dependency) or Python `moviepy`
- [ ] Store Cloudinary URL in `job_media.options_json` if uploaded

### Phase 3 — extend publisher interface
- [ ] Add `validate_media()` method to `Publisher` base class (with default no-op)
- [ ] Add `validate_post()` method to `Publisher` base class
- [ ] Implement `VideoValidationService` with platform-specific rules

### Phase 4 — add `PublicationAttempt` tracking
- [ ] Create `publication_attempts` table
- [ ] Refactor `publish_job()` to record attempts
- [ ] Add retry logic to scheduler

### Phase 5 — YouTube Shorts adapter
- [ ] Implement `YouTubeShortsPublisher` using YouTube Data API v3
- [ ] Register in `AutoposterService.publishers`
- [ ] Extend Telegram admin bot for YouTube accounts

### Phase 6 — Instagram Reels adapter
- [ ] Instagram already supports Reels via `media_type=REELS` in `_create_container`
- [ ] Add explicit `instagram_reels` content_type validation
- [ ] Add Reels-specific options (cover, audio, share_to_feed)

### Phase 7 — TikTok improvements
- [ ] Extract video metadata before upload
- [ ] Add validation for TikTok limits
- [ ] Improve Cloudinary fallback reliability

### Phase 8 — VK Clips, OK, Zen, TenChat, Threads, MAX
- [ ] Each gets a new publisher adapter
- [ ] Register in `AutoposterService.publishers`
- [ ] Add via Telegram admin bot UI

## 9. Non-Goals (out of scope)

- Replacing the entire `admin_bot.py` UI
- Refactoring the billing / subscription system
- Changing the OAuth token flow (SPGUtils Worker)
- Adding new Python dependencies (unless critical)
- Removing legacy file queue support