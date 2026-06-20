# Video Core Phase 2 Validation Spec

## Цель

Phase 2 добавляет изолированный сервис валидации `VideoPostValidationService`, который проверяет готовность video post к публикации на выбранные площадки, но НЕ публикует контент и НЕ подключается к Telegram UI.

## Ограничения (не менялись)

- Telegram UI не изменялся
- OAuth не изменялся
- publisher-адаптеры не изменялись
- scheduler не изменялся
- текущий flow публикации не изменялся
- старые таблицы `jobs`, `job_media`, `job_targets`, `publish_events` не изменялись
- никакой миграции старых jobs в новые таблицы нет
- новые зависимости не добавляются
- валидация работает только по имеющимся metadata в таблицах video core

## Добавленные модели результата валидации (`autoposter_bot/video_validation.py`)

### `ValidationSeverity`

| Значение     | Описание                |
|--------------|-------------------------|
| `ERROR`      | Ошибка — блокирует публикацию |
| `WARNING`    | Предупреждение — не блокирует |
| `INFO`       | Информационное сообщение  |

### `ValidationIssue`

| Поле           | Тип               | Описание                                    |
|----------------|-------------------|---------------------------------------------|
| `code`         | str               | Короткий идентификатор проблемы             |
| `message`      | str               | Человекочитаемое описание                   |
| `severity`     | ValidationSeverity| Уровень важности                            |
| `field`        | str | None         | Поле сущности, к которому относится issue   |
| `platform`     | str | None         | Платформа, если issue относится к конкретной|
| `details`      | dict | None       | Дополнительные структурированные данные     |

### `ValidationResult`

| Свойство/Метод   | Тип              | Описание                                    |
|------------------|------------------|---------------------------------------------|
| `ok`             | bool             | True, если нет ERROR-issues                 |
| `issues`         | list[ValidationIssue]| Все найденные проблемы                 |
| `has_errors()`   | -> bool          | Есть ли хотя бы одна ошибка                 |
| `errors()`       | -> list[ValidationIssue]| Только ошибки                     |
| `warnings()`     | -> list[ValidationIssue]| Только предупреждения               |
| `infos()`        | -> list[ValidationIssue]| Только информационные сообщения    |

## Добавленные классы конфигурации (`autoposter_bot/video_validation.py`)

### `PlatformVideoProfile`

Описание известных требований/лимитов для видео-платформы. Поля со значением `None` означают, что лимит неизвест или не применим.

| Поле                           | Тип               | Описание                                    |
|--------------------------------|-------------------|---------------------------------------------|
| `platform`                     | str               | Идентификатор платформы (например, `tiktok`) |
| `required_media_type`          | str | None       | Требуемый тип медиа (по умолчанию `video`)  |
| `allowed_mime_types`           | list[str] | None   | Разрешённые MIME-типы                       |
| `allowed_file_extensions`      | list[str] | None   | Разрешённые расширения файлов               |
| `min_duration_seconds`         | float | None     | Минимальная длительность в секундах         |
| `max_duration_seconds`         | float | None     | Максимальная длительность в секундах        |
| `min_width`                    | int | None       | Минимальная ширина в пикселях               |
| `min_height`                   | int | None       | Минимальная высота в пикселях               |
| `max_width`                    | int | None       | Максимальная ширина в пикселях              |
| `max_height`                   | int | None       | Максимальная высота в пикселях              |
| `max_file_size_bytes`          | int | None       | Максимальный размер файла в байтах          |
| `caption_max_length`           | int | None       | Максимальная длина подписи/текста поста     |
| `title_max_length`             | int | None       | Максимальная длина заголовка                |
| `requires_account_id`          | bool              | Требуется ли account_id в target            |
| `requires_cloudinary_url`      | bool              | Требуется ли cloudinary_url в asset         |
| `notes`                        | str | None       | Примечания, например, `TODO: verify...`     |

## Добавленные начальные профили

| Платформа | Проверяемые ограничения                                                                                      |
|-----------|--------------------------------------------------------------------------------------------------------------|
| `tiktok`  | max_duration_seconds=180, max_file_size=500 MB, caption_max_length=2200, разрешенные MIME/расширения         |
| `instagram`| max_duration_seconds=90, требует cloudinary_url, caption_max_length=2200, allowed_mime_types=["video/mp4"]   |
| `youtube` | max_duration_seconds=60 (Shorts), caption_max_length=5000, title_max_length=100, широкий набор форматов      |

Все лимиты отмечены как **осторожные, non-authoritative**.  
Если значение неизвестно — оставлено `None` и добавлено в `notes` пометка `TODO: verify with official platform docs`.

## Реализация `VideoPostValidationService`

### Методы

* `validate_post(post: VideoPost, assets: list[VideoAsset], targets: list[VideoTarget]) -> ValidationResult` — сквозная валидация по всем targets.
* `validate_for_platform(post: VideoPost, assets: list[VideoAsset], target: VideoTarget) -> ValidationResult` — валидация под конкретную платформу/target.

### Проверки общего уровня

1. **Post**
   - `post.id` существует и > 0
   - `post.status` в допустимых значениях (`draft`, `ready`, `scheduled`, `queued`, `publishing`, `published`, `partially_failed`, `failed`, `cancelled`)
2. **Assets**
   - хотя бы один asset существует
   - хотя бы один asset имеет `media_type == VideoMediaType.VIDEO.value` (primary video)
   - у каждого asset есть источник: `source` не пустой **или** `cloudinary_url` не пустой
   - JSON‑поля `metadata_json` / `options_json` не ломаются при парсинге (в модели уже есть property‑аксессоры)
3. **Targets**
   - хотя бы один target существует
   - у каждого target `status` в допустимых значениях (`pending`, `queued`, `publishing`, `published`, `failed`, `cancelled`, `skipped`)
   - `target.post_id` совпадает с `post.id` (если post.id известен)

### Проверки target‑специфичные (через `PlatformVideoProfile`)

* `platform` поддерживается (из списка `tiktok`, `instagram`, `youtube` — легко расширяется)
* если `profile.requires_account_id` → `target.account_id` существует и > 0
* `target.status` допустимый
* для каждого asset:
  - `asset.media_type` соответствует `profile.required_media_type` (иначе — **warning**)
  - если задано → `asset.mime_type` ∈ `profile.allowed_mime_types` (warning)
  - если задано → расширение `asset.original_filename` ∈ `profile.allowed_file_extensions` (warning)
  - если задано → `asset.duration_seconds` в `[min_duration_seconds, max_duration_seconds]` (error)
  - если задано → `asset.width`/`asset.height` в пределах профиля (warning)
  - если задано → `asset.file_size` ≤ `profile.max_file_size_bytes` (error)
  - если `profile.requires_cloudinary_url` → `asset.cloudinary_url` не пустой (error)

### Проверки заголовка/подписи

* если `profile.caption_max_length` задано → длина `post.text` ≤ лимита (error)
* если `profile.title_max_length` задано → длина `post.title` ≤ лимита (error)

## Особенности реализации

- Сервис полностью **изолирован**: не импортирует и не вызывает `autoposter_bot.db`, не обращается к базе данных, не вызывает publishers.
- Все данные передаются через аргументы (модели из `video_models.py`).
- JSON‑поля валидируются через существующие property‑аксессоры `metadata` / `options` — при ошибочном JSON будет исключение `json.JSONDecodeError`, но в текущей реализации модели оно уже происходит при доступе к свойству; сервис не ловит его намеренно, полагая, что повреждённые данные должны быть отловлены на уровне ввода.
- Лёгкая кастомизация: можно передать собственный словарь профилей в конструктор `VideoPostValidationService(profiles=...)`.
- Нет новых зависимостей — только стандартная библиотека Python (`dataclasses`, `enum`, `pathlib`, `typing`).

## Что не сделано в Phase 2

- Video core не подключён к Telegram UI
- Video core не подключён к OAuth
- publisher‑адаптеры не изменялись
- scheduler не изменялся
- Никакая запись в БД не производится валидатором
- Validation service не подключён к реальной очереди публикации
- Нет автоматического исправления проблем (только сообщение об ошибках)

## Следующий этап: draft creation service или queue service

- **Draft creation service**: сервис, который создаёт черновик видео‑поста в Telegram‑боте на основе валидированного VideoPost.
- **Queue service**: сервис, который ставит валидированный post в очередь на публикацию, учитывая расписание и лимиты аккаунта.

Оба сервиса могут использовать validation service как входной контроль перед тем, как передать данные дальше по конвейеру.