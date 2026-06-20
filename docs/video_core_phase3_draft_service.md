# Video Core Phase 3 — Draft Creation Service

## Цель

Phase 3 добавляет `VideoDraftCreationService` — сервис, который создаёт и собирает `VideoPost`, `VideoAsset` и `VideoTarget` из входного payload или из объектов моделей.

Сервис **не публикует контент** и **не подключается к Telegram UI**.

## Ограничения (не менялись)

- Telegram UI не изменялся
- OAuth не изменялся
- publisher-адаптеры не изменялись
- scheduler не изменялся
- текущий flow публикации не изменялся
- старые таблицы `jobs`, `job_media`, `job_targets`, `publish_events` не изменялись
- никакой миграции старых jobs в новые таблицы нет
- новые зависимости не добавляются

## Архитектура

```
VideoDraftCreationService
├── repository (duck-typed: Database из db.py)
├── validation (VideoPostValidationService)
└── методы:
    ├── create_draft()
    ├── create_draft_from_payload()
    ├── add_asset()
    ├── add_target()
    ├── clone_draft()
    └── get_draft()
```

Сервис:

- **Stateless** — вся персистентность делегирована repository.
- **Изолирован** — не импортирует scheduler, publishers, admin_bot, service.
- **Не вызывает БД напрямую** — только через repository-методы.

## Добавленные классы и исключения

### `VideoDraftBundle` (dataclass)

| Поле | Тип | Описание |
|---|---|---|
| `post` | VideoPost | Основной пост |
| `assets` | list[VideoAsset] | Список медиа-слотов |
| `targets` | list[VideoTarget] | Список target-ов |

### `VideoDraftError` (Exception)

Базовое исключение для ошибок создания черновика.

### `VideoDraftValidationError(VideoDraftError)`

Выбрасывается, если валидация не пройдена перед сохранением.

Содержит поле `issues: list[ValidationIssue]`.

### `VideoDraftNotFoundError(VideoDraftError)`

Выбрасывается, если post_id не найден.

## Добавленные методы

### `create_draft(**kwargs) → VideoPost`

1. Строит временный `VideoPost`, `VideoAsset`-ы, `VideoTarget`-ы.
2. Запускает валидацию через `VideoPostValidationService.validate_post()`.
3. Если валидация не пройдена — выбрасывает `VideoDraftValidationError` **без сохранения**.
4. Если OK — создаёт post, assets, targets через repository.
5. Возвращает `VideoPost` с заполненным id.

Параметры:

| Параметр | Тип | Описание |
|---|---|---|
| `owner_user_id` | int \| None | Владелец поста |
| `title` | str \| None | Заголовок |
| `caption` | str | Текст/подпись |
| `assets` | list[VideoAsset] \| None | Список asset-ов |
| `targets` | list[VideoTarget] \| None | Список target-ов |
| `metadata` | dict \| None | Дополнительные метаданные |

### `create_draft_from_payload(payload: dict) → VideoPost`

Создаёт draft из словаря:

```python
{
    "owner_user_id": 123,
    "title": "Title",
    "caption": "Caption",
    "assets": [
        {
            "source": "https://.../video.mp4",
            "media_type": "video",
            "original_filename": "video.mp4",
            "mime_type": "video/mp4",
            "duration_seconds": 30.0,
            "width": 1920,
            "height": 1080,
            "file_size": 10_000_000,
        },
    ],
    "targets": [
        {
            "platform": "tiktok",
            "account_id": 100,
        },
    ],
    "metadata": {},
}
```

### `add_asset(post_id, asset: VideoAsset) → VideoAsset`

Добавляет asset к существующему draft.

- Проверяет, что post существует и имеет статус `draft`.
- Автоматически вычисляет `order_index` (max + 1).
- Не запускает публикацию.

### `add_target(post_id, target: VideoTarget) → VideoTarget`

Добавляет target к существующему draft.

- Проверяет, что post существует и имеет статус `draft`.
- Не запускает публикацию.

### `clone_draft(source_post_id, *, owner_user_id, title) → VideoPost`

Клонирует существующий draft:

1. Загружает оригинальный post, assets, targets.
2. Создаёт новый VideoPost с новым `external_post_id`.
3. Копирует все assets.
4. Копирует все targets.
5. Возвращает новый draft со статусом `draft`.

### `get_draft(post_id) → VideoDraftBundle`

Возвращает полный bundle: post + assets + targets.

## Жизненный цикл draft

```
create_draft() → статус "draft"
    ↓
add_asset() / add_target()  (опционально, пока статус "draft")
    ↓
(в будущем: promote to "ready" → queue → publish)
```

## Валидация

Перед сохранением `create_draft()` вызывает `VideoPostValidationService.validate_post()`.

Если есть ERROR-issues:

- данные **не сохраняются**
- выбрасывается `VideoDraftValidationError` с полным списком issues

`add_asset()` и `add_target()` **не вызывают валидацию** — они только проверяют статус поста.

## Тесты (`tests/test_video_draft_service.py`)

| Класс | Тест | Описание |
|---|---|---|
| `TestCreateDraft` | `test_create_draft` | Создание draft |
| | `test_create_draft_via_payload` | Создание через payload |
| | `test_create_draft_no_assets_fails_validation` | Ошибка при отсутствии assets |
| | `test_create_draft_no_targets_fails_validation` | Ошибка при отсутствии targets |
| | `test_validation_error_does_not_save` | Ничего не сохраняется при ошибке |
| `TestAddAsset` | `test_add_asset` | Добавление asset |
| | `test_add_asset_to_nonexistent_post_raises` | Исключение для несуществующего post |
| `TestAddTarget` | `test_add_target` | Добавление target |
| | `test_add_target_to_nonexistent_post_raises` | Исключение для несуществующего post |
| `TestCloneDraft` | `test_clone_draft` | Клонирование draft |
| | `test_clone_draft_creates_new_id` | Новый id после клонирования |
| | `test_clone_draft_nonexistent_raises` | Исключение для несуществующего post |
| `TestGetDraft` | `test_get_draft_returns_bundle` | Возврат полного bundle |
| | `test_get_draft_nonexistent_raises` | Исключение для несуществующего post |
| `TestServiceIsolation` | (5 тестов) | Сервис не импортирует scheduler/publishers/admin_bot |
| `TestValidationIntegration` | `test_validation_called_on_create` | Валидация вызывается при создании |
| `TestErrorHierarchy` | (2 теста) | Иерархия исключений |

## Что не сделано в Phase 3

- Draft не подключён к Telegram UI
- Draft не публикует контент
- Нет перехода draft → ready
- Нет очереди публикации
- Нет работы с расписанием
- Нет интеграции с OAuth

## Точки интеграции с будущими этапами

### Telegram UI

- `create_draft_from_payload()` — готов принимать данные из формы бота.
- `get_draft()` — готов отдавать данные для отображения.
- `add_asset()` / `add_target()` — готовы для пошагового редактирования.

### Queue Service

После валидации draft может быть переведён в статус `ready`, после чего Queue Service может подхватить его и начать публикацию.

## Следующий этап

**Queue service** — сервис, который:

- Получает `VideoPost` со статусом `ready`
- Ставит target-ы в очередь
- Учитывает расписание и лимиты
- Запускает publisher-адаптеры
- Логирует попытки через `video_publication_attempts`