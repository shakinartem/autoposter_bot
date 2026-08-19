# Autoposter — Content Distribution OS

Autoposter развивается из Telegram-first бота в workspace-scoped web/API платформу дистрибуции контента. Legacy Telegram admin bot остаётся операционной поверхностью, но новая архитектура строится вокруг FastAPI, Next.js, platform variants, encrypted social credentials и scheduled workers.

## Фактический scope текущей ветки

Production registry сейчас содержит четыре площадки:

- `Telegram`: текст, фото, видео, альбомы;
- `VK`: текст, фото, видео на стену;
- `Instagram`: feed image, video/Reels, carousel, Stories;
- `TikTok`: video publishing/draft flow.

`YouTube` и следующие платформы — roadmap, а не готовая часть текущего registry.

## Content model

```text
ContentItem (Master)
  -> PlatformVariant (platform-native версия)
    -> Publication (account + destination + schedule + lifecycle)
      -> PublicationAttempt
```

Platform variant остаётся синхронизирован с Master, пока пользователь не делает platform-specific override. После override вариант живёт независимо.

## Архитектура

- FastAPI backend;
- Next.js Composer / Calendar / Social Connections;
- SQLite как development/legacy bridge;
- PostgreSQL как production persistence;
- PostgreSQL scheduled queue;
- workspace isolation;
- encrypted social credentials с MultiFernet rotation;
- private local/S3-compatible media storage;
- streaming media upload через Next.js BFF;
- platform-aware media materialization;
- TikTok OAuth;
- Instagram Login OAuth;
- refresh credentials перед direct/scheduled publish;
- Telegram OIDC public login;
- revocable workspace user sessions;
- RBAC `viewer / editor / admin / owner`;
- live multi-workspace switching.

## Authentication

API поддерживает два явных режима.

### Service mode

Для внутренних server-side callers используется workspace API key:

```env
AUTOPOSTER_WEB_AUTH_MODE=service
AUTOPOSTER_API_KEY=replace-with-long-workspace-key
AUTOPOSTER_API_KEYS_JSON={"replace-with-long-workspace-key":1}
```

Ключ остаётся только на сервере Next.js и не попадает в browser JavaScript.

### Session mode

Для пользовательского web-доступа используются opaque revocable sessions. Raw session token **не хранится в БД** — сохраняется только SHA-256 hash.

```env
AUTOPOSTER_WEB_AUTH_MODE=session
```

В этом режиме BFF использует только HttpOnly cookie `autoposter_session` и не делает fallback на service API key.

Роли workspace:

- `viewer` — чтение контента, календаря, статусов;
- `editor` — создание/редактирование контента, media upload, schedule/publish;
- `admin` — editor permissions + social account/OAuth management + member management;
- `owner` — управление административным доступом;
- `service` — internal server credential, не пользовательская роль.

### Telegram Web Login

Публичный вход использует Telegram OIDC Authorization Code Flow с PKCE.

Поток:

```text
/login
  -> Next.js /auth/telegram
  -> Telegram OIDC
  -> FastAPI /auth/telegram/callback
  -> verified Telegram identity
  -> existing user or new user + personal workspace
  -> one-time apg_ login grant
  -> Next.js /auth/complete
  -> revocable aps_ session in HttpOnly cookie
```

Долгоживущий `aps_` token не попадает в URL, React state, browser history или referrer. Callback выдаёт только короткоживущий одноразовый `apg_` grant, в БД хранится только его hash.

Пользователь с доступом к нескольким workspace переключает активный workspace без перевыпуска session token. Logout сначала отзывает backend session, затем удаляет cookie.

### Operator session CLI

Операторский bridge остаётся для внутренних сценариев:

```bash
autoposter-session members --workspace-id 1
autoposter-session set-role --workspace-id 1 --user-id 2 --role editor
autoposter-session issue --workspace-id 1 --user-id 2 --days 30
autoposter-session revoke 'aps_...'
```

## Social Connections

Manual encrypted connection configuration поддерживается для Telegram, VK, Instagram и TikTok.

One-click OAuth подключён для:

- TikTok;
- Instagram Login.

OAuth state шифруется, ограничен по времени и привязан к workspace + provider. Начать OAuth может только `admin+`; callback остаётся provider-facing endpoint и проверяет encrypted state.

VK пока сохраняет manual access-token fallback. VK ID flow стоит включать как основной только после проверки, что выбранный token type совместим с используемыми posting/media API.

## Media

Web загружает image/video через streaming BFF. Production может хранить draft media в private S3-compatible storage. Площадка получает либо временный presigned URL, либо временный локальный файл — в зависимости от требований publisher.

## PostgreSQL queue

Scheduled workers используют PostgreSQL row locking для атомарного claim due publications. Это позволяет масштабировать workers горизонтально без выдачи одной публикации двум consumers одновременно.

## Analytics + operations

`analytics-worker` собирает append-only performance snapshots по milestone-окнам, а `/api/v1/operations/overview` даёт workspace-scoped operational health: queue lag, retries, provider processing, quarantined outcomes, attempt failure rate и analytics freshness.

Текущий data-loop:

```text
Master -> Variant -> Publication -> Provider tracking -> Final post -> Performance snapshots
```

Instagram уже имеет live collector. Для TikTok публикация после `init` остаётся в `processing`: отдельный reconciliation worker проверяет provider status по `publish_id` и только после подтверждения сохраняет final post identity.

## Legacy Telegram admin bot

Legacy команды продолжают поддерживать операционную работу:

- `/whoami`
- `/accounts`
- `/delete_account <id>`
- `/vk_token_status`
- `/set_vk_token <token> [expires_at_iso]`
- `/instagram_token_status`
- `/set_instagram_token <token> [expires_at_iso]`
- `/test_text`
- `/test_photo`
- `/test_video`

## Базовая установка

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -e .
```

API / worker / operator tools:

```bash
autoposter-api
autoposter-worker
autoposter-reconciler
autoposter-workspace --help
autoposter-session --help
```

## Near-term roadmap

1. posting-compatible VK connection flow;
2. analytics collectors для TikTok / VK / Telegram там, где provider API даёт метрики;
3. webhook-driven reconciliation как дополнение к polling;
4. orphan media lifecycle cleanup;
5. audit/notification layer для operational incidents и team actions;
6. дополнительные native platforms, включая YouTube;
7. derived insights + recommendation layer на данных `content -> variant -> publication -> performance`.
