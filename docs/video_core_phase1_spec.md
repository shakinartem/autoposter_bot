# Video Core Phase 1 Spec

## Цель

Phase 1 добавляет Python-модели, enum-статусы и безопасный repository/CRUD-слой для новых video-таблиц, созданных в Phase 0.

Phase 1 НЕ подключает video core к Telegram UI и НЕ запускает реальную публикацию.

## Ограничения (не менялись)

- Telegram UI не изменялся
- OAuth не изменялся
- publisher-адаптеры не изменялись
- scheduler не изменялся
- старые таблицы `jobs`, `job_media`, `job_targets`, `publish_events` не изменялись
- новая video-схема не подключена к реальной публикации
- никакой миграции старых jobs в новые таблицы нет

## Добавленные Enum/Status

### `VideoPostStatus` (`autoposter_bot/video_models.py`)

| Значение           | Описание                         |
|--------------------|----------------------------------|
| `DRAFT`            | Черновик                         |
| `READY`            | Готов к отправке                 |
| `SCHEDULED`        | Запланирован                     |
| `QUEUED`           | В очереди                        |
| `PUBLISHING`       | Публикуется                      |
| `PUBLISHED`        | Опубликован                      |
| `PARTIALLY_FAILED` | Частично не удался               |
| `FAILED`           | Не удался                        |
| `CANCELLED`        | Отменён                          |

### `VideoTargetStatus` (`autoposter_bot/video_models.py`)

| Значение    | Описание                  |
|-------------|---------------------------|
| `PENDING`   | Ожидает                   |
| `QUEUED`    | В очереди                 |
| `PUBLISHING`| Публикуется               |
| `PUBLISHED` | Опубликован               |
| `FAILED`    | Не удался                 |
| `CANCELLED` | Отменён                   |
| `SKIPPED`   | Пропущен                  |

### `VideoAttemptStatus` (`autoposter_bot/video_models.py`)

| Значение         | Описание                         |
|------------------|----------------------------------|
| `STARTED`        | Попытка начата                   |
| `SUCCEEDED`      | Попытка успешна                  |
| `FAILED`         | Попытка не удалась               |
| `RETRY_SCHEDULED`| Запланирован повтор              |

### `VideoMediaType` (`autoposter_bot/video_models.py`)

| Значение    | Описание                |
|-------------|-------------------------|
| `VIDEO`     | Видео                   |
| `IMAGE`     | Изображение             |
| `THUMBNAIL` | Превью                  |
| `COVER`     | Обложка                 |

## Добавленные Dataclass-модели (`autoposter_bot/video_models.py`)

- `VideoPost` — модель для таблицы `video_posts`
- `VideoAsset` — модель для таблицы `video_assets`
- `VideoTarget` — модель для таблицы `video_targets`
- `VideoPublicationAttempt` — модель для таблицы `video_publication_attempts`

Поля моделей соответствуют колонкам таблиц из Phase 0. JSON-поля имеют property-аксессоры `metadata` / `options` для автоматического `json.dumps/loads`.

## Добавленные CRUD-методы (`autoposter_bot/db.py`)

### `video_posts`

| Метод                        | Описание                                   |
|------------------------------|--------------------------------------------|
| `create_video_post(...)`     | Создать новый video post                   |
| `get_video_post(post_id)`    | Получить post по id                         |
| `update_video_post_status(post_id, status, updated_at=None)` | Обновить статус post |
| `list_video_posts_by_status(status, limit=50)` | Список posts по статусу       |

### `video_assets`

| Метод                               | Описание                              |
|-------------------------------------|---------------------------------------|
| `create_video_asset(...)`           | Создать новый video asset             |
| `list_video_assets(post_id)`        | Список asset-ов для post              |

### `video_targets`

| Метод                                               | Описание                              |
|-----------------------------------------------------|---------------------------------------|
| `create_video_target(...)`                          | Создать новый video target            |
| `list_video_targets(post_id)`                       | Список target-ов для post             |
| `update_video_target_status(target_id, status, error_message=None)` | Обновить статус target |

### `video_publication_attempts`

| Метод                                                 | Описание                                  |
|-------------------------------------------------------|-------------------------------------------|
| `create_video_publication_attempt(...)`               | Создать новую попытку публикации          |
| `list_video_publication_attempts(target_id)`          | Список попыток для target                 |

## Особенности реализации

- Все методы используют текущий стиль подключения к SQLite (`with self.connect() as connection`)
- timestamps хранятся в ISO-формате (`datetime.utcnow().isoformat()`)
- JSON-поля хранятся как TEXT с `json.dumps(..., ensure_ascii=False)`
- id генерируются через `AUTOINCREMENT` (SQLite)
- `update_video_target_status` с `error_message` сохраняет ошибку в `options_json["last_error"]`
- Новые CRUD-методы размещены между старыми методами и `create_job`, чтобы не нарушать существующий код

## Добавленные тесты

Файл: `tests/test_video_core_repository.py`

Кейсы:
- `test_create_and_get_video_post` — создание и чтение video post
- `test_update_video_post_status` — обновление статуса post
- `test_update_video_post_status_nonexistent` — обновление несуществующего post
- `test_list_video_posts_by_status` — фильтрация posts по статусу
- `test_list_video_posts_by_status_limit` — лимит выборки
- `test_create_and_list_video_assets` — создание нескольких assets и получение по post_id
- `test_list_video_assets_empty` — пустой список
- `test_create_and_list_video_targets` — создание targets и получение по post_id
- `test_update_video_target_status` — обновление статуса target
- `test_update_video_target_status_with_error` — обновление с сохранением ошибки
- `test_create_and_list_attempts` — создание attempts и получение по target_id
- `test_list_attempts_empty` — пустой список attempts
- `test_reinit_schema_preserves_data` — повторная инициализация БД не ломает CRUD
- `test_create_job_still_works` — старые таблицы продолжают работать
- `test_get_due_jobs_still_works` — старый scheduler продолжает работать

## Что не сделано в Phase 1

- Video core не подключён к Telegram UI
- Video core не подключён к OAuth
- publisher-адаптеры не изменялись
- scheduler не изменялся
- Старые jobs не мигрируются в новые таблицы
- Нет validation service для video posts

## Следующий этап: validation service

- Создание `VideoPostValidationService` для проверки полей перед созданием video post
- Валидация обязательных полей, форматов, лимитов
- Возможно: подключение к Telegram UI в качестве read-only просмотра