# Video Core Phase 0 Spec

## Цель

Phase 0 должен подготовить БД под новый video publishing core без изменения текущего поведения.

Обязательные ограничения:
- не менять `autoposter_bot/db.py` в части текущих таблиц и существующих CRUD-методов, кроме добавления новых таблиц и индексов;
- не менять `autoposter_bot/service.py`;
- не менять `autoposter_bot/scheduler.py`;
- не менять `autoposter_bot/admin_bot.py`;
- не менять `autoposter_bot/publishers/*`;
- не подключать новые таблицы к текущему flow публикации;
- не мигрировать существующие `jobs`, `job_media`, `job_targets` в новый слой.

## Что я проверил в коде

Текущее состояние репозитория подтверждает, что:
- текущая публикация опирается на `jobs`, `job_media`, `job_targets` и `publish_events`;
- `PostJob`, `MediaItem`, `Target`, `OAuthConnection` уже используются как контракт между `db.py`, `service.py`, `scheduler.py` и publisher-ами;
- OAuth идет через `SpgUtilsClient` и синхронизацию `oauth_connections`;
- Cloudinary используется как текущий helper для загрузки медиа, особенно для Instagram и как fallback-ветка в TikTok;
- scheduler уже обрабатывает DB-очередь через `get_due_jobs()` и оставляет legacy file queue без изменений.

Вывод: новые таблицы должны быть строго параллельными, а не заменой существующих сущностей.

## Что в аудит-документах нужно считать неточным или слишком сильным

### `docs/autoposter_current_architecture.md`
- Описание текущих таблиц в целом совпадает с кодом.
- Но формулировка про статусы job-ов неполная: в коде сейчас реально используются `pending`, `published`, `processing`, `failed`.
- Утверждение, что `job_media` уже хранит отдельную полноценную video-мета-модель, неверно. Сейчас там только `source`, `media_type`, `order_index`, `options_json`.
- Формулировка про worker storage layer вроде `Cloudflare KV + D1` не проверяется из этого репозитория. Для текущей спецификации это надо считать внешним предположением, а не подтвержденным фактом.

### `docs/video_publishing_core_plan.md`
- Документ в текущем виде описывает не Phase 0, а будущую замену текущего слоя.
- Слова `replacing / extending current jobs table` и `replacing / extending current job_media table` конфликтуют с требованием не ломать текущую публикацию.
- `MediaPost`, `MediaAsset`, `PublicationTarget`, `PublicationAttempt` дублируют текущие `PostJob`, `MediaItem`, `Target` на уровне смысла, если их пытаться ввести как replacements.
- Показанный plan можно внедрять только как отдельный additive core, а не как миграцию текущих сущностей.

### `docs/platform_requirements_draft.md`
- Это рабочий draft, а не источник истины.
- Большая часть лимитов помечена `TODO: verify` и должна считаться неподтвержденной до сверки с официальной документацией.
- Для Phase 0 этот документ нельзя использовать как жесткую спецификацию БД.

## Минимальный безопасный состав Phase 0

Phase 0 должен содержать только:
- новые таблицы;
- новые индексы;
- миграцию через текущий стиль `db.py`;
- тесты на наличие таблиц и индексов;
- тест-регрессию, что старые таблицы и текущая DB-публикация остаются рабочими.

Phase 0 не должен содержать:
- изменения Telegram UI;
- изменения flow создания поста;
- изменения OAuth;
- изменения publisher-адаптеров;
- изменения scheduler logic;
- backfill/перенос существующих jobs в новый слой.

## Рекомендуемые таблицы

Ниже описан минимальный additive-набор, который можно создать отдельно от текущих таблиц.

### 1. `video_posts`

Назначение: отдельная сущность поста для video core, не связанная с текущим `jobs`.

Поля:
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `owner_user_id` INTEGER NULL
- `external_post_id` TEXT NOT NULL UNIQUE
- `title` TEXT NULL
- `text` TEXT NOT NULL
- `status` TEXT NOT NULL
- `scheduled_at` TEXT NULL
- `published_at` TEXT NULL
- `metadata_json` TEXT NOT NULL DEFAULT '{}'
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Рекомендуемые статусы `video_posts.status`:
- `draft`
- `ready`
- `scheduled`
- `queued`
- `publishing`
- `published`
- `failed`
- `cancelled`

### 2. `video_assets`

Назначение: медиа-слоты для поста, включая видео-метаданные.

Поля:
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `post_id` INTEGER NOT NULL
- `source` TEXT NOT NULL
- `media_type` TEXT NOT NULL
- `order_index` INTEGER NOT NULL DEFAULT 0
- `original_filename` TEXT NULL
- `file_size` INTEGER NULL
- `mime_type` TEXT NULL
- `duration_seconds` REAL NULL
- `width` INTEGER NULL
- `height` INTEGER NULL
- `aspect_ratio` TEXT NULL
- `cloudinary_public_id` TEXT NULL
- `cloudinary_url` TEXT NULL
- `processed` INTEGER NOT NULL DEFAULT 0
- `options_json` TEXT NOT NULL DEFAULT '{}'
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Рекомендуемые значения `video_assets.media_type`:
- `image`
- `video`
- `auto`

### 3. `video_targets`

Назначение: привязка видео-поста к target/account, отдельная от текущего `job_targets`.

Поля:
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `post_id` INTEGER NOT NULL
- `account_id` INTEGER NOT NULL
- `platform` TEXT NOT NULL
- `destination` TEXT NULL
- `options_json` TEXT NOT NULL DEFAULT '{}'
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Рекомендуемые ограничения:
- `UNIQUE(post_id, account_id)`

### 4. `video_publication_attempts`

Назначение: журнал попыток публикации по target.

Поля:
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `post_id` INTEGER NOT NULL
- `target_id` INTEGER NOT NULL
- `platform` TEXT NOT NULL
- `attempt_number` INTEGER NOT NULL
- `status` TEXT NOT NULL
- `external_id` TEXT NULL
- `error_code` TEXT NULL
- `error_detail` TEXT NULL
- `retry_at` TEXT NULL
- `started_at` TEXT NULL
- `finished_at` TEXT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Рекомендуемые статусы `video_publication_attempts.status`:
- `pending`
- `publishing`
- `success`
- `failed`
- `retry_scheduled`

## Индексы

Ниже индексы, которые стоит добавить сразу, чтобы Phase 0 был пригоден для будущих выборок:

- `CREATE UNIQUE INDEX IF NOT EXISTS idx_video_posts_external_post_id ON video_posts(external_post_id)`
- `CREATE INDEX IF NOT EXISTS idx_video_posts_owner_status_schedule ON video_posts(owner_user_id, status, scheduled_at)`
- `CREATE UNIQUE INDEX IF NOT EXISTS idx_video_assets_post_order ON video_assets(post_id, order_index)`
- `CREATE INDEX IF NOT EXISTS idx_video_assets_post_type ON video_assets(post_id, media_type)`
- `CREATE UNIQUE INDEX IF NOT EXISTS idx_video_targets_post_account ON video_targets(post_id, account_id)`
- `CREATE INDEX IF NOT EXISTS idx_video_targets_post ON video_targets(post_id)`
- `CREATE INDEX IF NOT EXISTS idx_video_targets_account ON video_targets(account_id)`
- `CREATE UNIQUE INDEX IF NOT EXISTS idx_video_attempts_target_attempt ON video_publication_attempts(target_id, attempt_number)`
- `CREATE INDEX IF NOT EXISTS idx_video_attempts_status_retry ON video_publication_attempts(status, retry_at)`
- `CREATE INDEX IF NOT EXISTS idx_video_attempts_post_target ON video_publication_attempts(post_id, target_id)`

## Как добавить миграцию в текущем стиле `db.py`

Для Phase 0 достаточно текущего шаблона:

1. Добавить `CREATE TABLE IF NOT EXISTS ...` для всех новых таблиц в строку `SCHEMA`.
2. Добавить `CREATE INDEX IF NOT EXISTS ...` в тот же `SCHEMA`, рядом с соответствующими таблицами.
3. В `Database.init_schema()` ничего не менять по логике кроме выполнения расширенного `executescript(SCHEMA)`.
4. Не добавлять сложные `ALTER TABLE`-миграции, потому что это новые таблицы, а не расширение существующих.

Если позже появятся новые поля, которые нужно будет добавить к этим таблицам, тогда уже использовать тот же стиль, который сейчас есть для `users`, `job_media`, `oauth_connections` и `jobs`: `PRAGMA table_info(...)` + `ALTER TABLE ... ADD COLUMN ...`.

## Что можно добавить позже, но не нужно в Phase 0

Можно отложить до следующего этапа:
- `create_video_post(...)`
- `list_video_posts(...)`
- `get_video_post(...)`
- `add_video_asset(...)`
- `list_video_assets(post_id)`
- `create_video_target(...)`
- `list_video_targets(post_id)`
- `create_publication_attempt(...)`
- `mark_publication_attempt_success(...)`
- `mark_publication_attempt_failed(...)`
- `schedule_publication_retry(...)`
- `list_due_publication_retries(...)`

Это полезно для следующего этапа, но в Phase 0 не обязательно.

## Тесты для Phase 0

### 1. Тест на создание новых таблиц

Новый тест должен:
- создать временную SQLite БД;
- вызвать `db.init_schema()`;
- проверить наличие `video_posts`, `video_assets`, `video_targets`, `video_publication_attempts` через `sqlite_master`;
- проверить, что у таблиц есть нужные колонки через `PRAGMA table_info(...)`.

### 2. Тест на индексы

Новый тест должен:
- проверить `PRAGMA index_list(video_posts)`, `PRAGMA index_list(video_assets)`, `PRAGMA index_list(video_targets)`, `PRAGMA index_list(video_publication_attempts)`;
- убедиться, что уникальные индексы реально созданы.

### 3. Тест регрессии старого flow

Новый тест должен показать, что старый flow не сломан:
- создать `user`;
- создать `account`;
- создать старый `job` через `db.create_job(...)`;
- вызвать `db.get_due_jobs(...)`;
- убедиться, что `PostJob`, `MediaItem`, `Target` собираются как раньше;
- при необходимости прогнать `process_due_db_jobs(..., dry_run=True)` с заглушкой service, чтобы убедиться, что таблицы video core не мешают текущей очереди.

### 4. Существующие тесты

Наличие новых таблиц не должно ломать:
- `tests/test_database_product.py`;
- `tests/test_spgutils_integration.py`;
- `tests/test_oauth_worker_flow.py`;
- `tests/test_tiktok_publisher.py`;
- `tests/test_instagram_publisher.py`.

## Как убедиться, что старая публикация не сломалась

Нужны 3 уровня проверки:

1. Unit smoke:
- `db.create_job(...)` по-прежнему пишет в `jobs`, `job_media`, `job_targets`.

2. Scheduler smoke:
- `db.get_due_jobs(...)` по-прежнему возвращает `PostJob` с `media_items` и `targets`.

3. Publisher smoke:
- `service.publish_job(...)` продолжает принимать `PostJob` без знания о новых таблицах.

Критерий успешного Phase 0:
- новые таблицы создаются;
- новые индексы создаются;
- старые тесты и текущий flow не меняют поведение.

## Рекомендуемый набор файлов для следующего этапа

Если Phase 0 будут внедрять кодом, следующий этап должен трогать только:
- `autoposter_bot/db.py`
- `tests/test_database_product.py`
- `tests/test_video_core_schema.py` или аналогичный новый тестовый файл

И только если появится необходимость в явном API для нового слоя:
- `autoposter_bot/models.py`
- `autoposter_bot/service.py`
- `autoposter_bot/scheduler.py`

Но для Phase 0 это не требуется.

## Итоговое решение по Phase 0

Да, проект готов к созданию новых таблиц, если Phase 0 будет строго additive:
- без замены старых таблиц;
- без переноса существующих jobs;
- без изменений публикационного flow;
- с отдельными тестами на схему и регрессию.

