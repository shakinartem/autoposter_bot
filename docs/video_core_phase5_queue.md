# Video Core Phase 5 — Video Queue Service

## Цель

Phase 5 добавляет `VideoQueueService` — in-memory очередь публикации для video core.

Очередь **не публикует контент**, не пишет в БД, не меняет статусы, не запускает publisher-адаптеры.

## Ограничения (не менялись)

- Telegram UI не изменялся
- OAuth не изменялся
- scheduler не изменялся
- service.py не изменялся
- publisher-адаптеры не изменялись
- старый publish flow не изменялся
- старые таблицы не изменялись
- никакой миграции старых jobs нет
- очередь работает только в памяти (пока нет персистенции)

## Архитектура

```
VideoQueueService
├── planner: VideoPublicationPlanner (делегирует решения)
├── dispatcher: PublisherDispatcherProtocol (заглушка пока)
└── _batches: dict[int, QueueBatch] (in-memory)
```

## Добавленные классы

### `QueueItemStatus` (Enum)

| Значение | Описание |
|---|---|
| `PENDING` | В очереди, ожидает обработки |
| `QUEUED` | В очереди, готов к dispatch |
| `PROCESSING` | Обрабатывается в данный момент |
| `COMPLETED` | Успешно завершён |
| `FAILED` | Не удался |
| `SKIPPED` | Пропущен |

### `QueueItem` (dataclass)

| Поле | Тип | Описание |
|---|---|---|
| `post_id` | int | id video post |
| `target_id` | int | id target |
| `platform` | str | Платформа |
| `status` | str | QueueItemStatus |
| `scheduled_at` | str \| None | Время публикации |
| `created_at` | str | Время создания элемента |
| `metadata` | dict \| None | Дополнительные данные |

Свойство `is_active` — возвращает True, если статус PENDING/QUEUED/PROCESSING.

### `QueueBatch` (dataclass)

| Поле/Метод | Описание |
|---|---|
| `created_at` | Время создания батча |
| `items` | Список QueueItem |
| `pending_items()` | Только PENDING |
| `ready_items()` | Только QUEUED |
| `failed_items()` | Только FAILED |
| `scheduled_items()` | Только с scheduled_at |

### `PublisherDispatcherProtocol`

Интерфейс для будущего publisher dispatcher:

```python
class PublisherDispatcherProtocol:
    def dispatch(self, queue_item: QueueItem) -> dict[str, Any]: ...
```

### `NullPublisherDispatcher`

Заглушка, возвращающая mock-result без реальной публикации.

### `VideoQueueService`

| Метод | Описание |
|---|---|
| `build_queue(bundle) → QueueBatch` | Построить батч из PublicationPlan |
| `get_ready_items(bundle) → list[QueueItem]` | Только QUEUED элементы |
| `enqueue(bundle)` | Добавить READY targets как PENDING |
| `dequeue() → QueueItem \| None` | Достать следующий QUEUED, отметить PROCESSING |
| `mark_processing(item)` | Отметить PROCESSING |
| `mark_completed(item)` | Отметить COMPLETED |
| `mark_failed(item)` | Отметить FAILED |

## Логика `build_queue`

Использует `PublicationPlan` от Planner:

| PublicationDecisionType | Действие в очереди |
|---|---|
| `READY` | Добавить как `QUEUED` |
| `RETRY` | Добавить как `PENDING` с `scheduled_at = retry_at` |
| `BLOCKED` | Пропустить |
| `WAITING` | Пропустить |
| `SCHEDULED` | Пропустить (будет обработан позже) |
| `ALREADY_PUBLISHED` | Пропустить |

## Жизненный цикл элемента очереди

```
READY → QUEUED → PROCESSING → COMPLETED
                          → FAILED → (RETRY в Planner) → PENDING → QUEUED
```

## Тесты (`tests/test_video_queue.py`)

| Класс | Тестов | Описание |
|---|---|---|
| `TestQueueDecisions` | 6 | READY→QUEUED, RETRY→PENDING, BLOCKED/WAITING/SCHEDULED/ALREADY_PUBLISHED пропуски |
| `TestQueueOperations` | 5 | enqueue, dequeue, mark_completed, mark_failed, multiple targets |
| `TestQueueBatchHelpers` | 4 | pending/ready/failed/scheduled helpers |
| `TestDispatcherProtocol` | 2 | NullDispatcher, Protocol exists |
| `TestQueueIsolation` | 4 | Нет scheduler/publishers/admin_bot/service импортов |

## Что не сделано в Phase 5

- Нет персистенции очереди в БД
- Нет интеграции с scheduler
- Нет реальной публикации
- Нет вызова publisher-адаптеров
- Нет изменения статусов в БД

## Следующий этап

**Queue persistence + integration** — добавить:
- Запись QueueItem в БД (новая таблица `video_queue_items`)
- Интеграцию с scheduler для автоматического dequeue
- Реальный PublisherDispatcher для TikTok/Instagram/YouTube
- Логирование publication_attempts