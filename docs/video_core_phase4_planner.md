# Video Core Phase 4 — Video Publication Planning Service

## Цель

Phase 4 добавляет `VideoPublicationPlanner` — сервис, который строит план публикации для video post.

Сервис **не публикует контент**, не пишет в БД, не меняет статусы, не создаёт попытки публикации и не вызывает publisher-адаптеры.

## Ограничения (не менялись)

- Telegram UI не изменялся
- OAuth не изменялся
- publisher-адаптеры не изменялись
- scheduler не изменялся
- текущий flow публикации не изменялся
- старые таблицы не изменялись
- никакой миграции старых jobs нет
- новые зависимости не добавляются

## Жизненный цикл публикации (решения Planner)

```
Draft → WAITING
  ↓ (promote to ready)
READY → validation ok → READY
  ↓                    ↓ validation fails → BLOCKED
  ↓ scheduled_at в будущем → SCHEDULED
  ↓ target уже published → ALREADY_PUBLISHED
  ↓ target failed → RETRY
```

## Добавленные классы

### `PublicationDecisionType` (Enum)

| Значение | Описание |
|---|---|
| `READY` | Target готов к публикации |
| `SCHEDULED` | Публикация запланирована в будущем |
| `WAITING` | Пост ещё в статусе draft |
| `RETRY` | Предыдущая попытка не удалась, возможен повтор |
| `BLOCKED` | Валидация не прошла |
| `ALREADY_PUBLISHED` | Target уже опубликован |

### `PublicationDecision` (dataclass)

| Поле | Тип | Описание |
|---|---|---|
| `target_id` | int | id target-а |
| `platform` | str | Платформа |
| `decision` | PublicationDecisionType | Тип решения |
| `reason` | str | Человекочитаемая причина |
| `scheduled_at` | str \| None | Время публикации (для SCHEDULED) |
| `retry_at` | str \| None | Время повтора (для RETRY) |
| `metadata` | dict \| None | Дополнительные данные |

### `PublicationPlan` (dataclass)

| Поле/Метод | Описание |
|---|---|
| `post_id` | id поста |
| `created_at` | Время создания плана |
| `decisions` | Список PublicationDecision |
| `ready_targets()` | Только READY решения |
| `blocked_targets()` | Только BLOCKED решения |
| `retry_targets()` | Только RETRY решения |

### `VideoPublicationPlanner`

| Метод | Описание |
|---|---|
| `build_plan(bundle: VideoDraftBundle) → PublicationPlan` | Построить полный план |
| `build_target_decision(post, target, bundle) → PublicationDecision` | Решение для одного target |

## Логика принятия решений (в порядке приоритета)

1. **ALREADY_PUBLISHED**: target.status == `published`
2. **BLOCKED**: post не draft, валидация не проходит
3. **WAITING**: post.status == `draft`
4. **SCHEDULED**: post.scheduled_at в будущем
5. **RETRY**: target.status == `failed`
6. **READY**: во всех остальных случаях

## Особенности реализации

- Planner **stateless** — не хранит состояние между вызовами
- Использует `VideoPostValidationService.validate_for_platform()` только для non-draft постов
- Не пишет в БД, не меняет статусы
- Не требует repository/database объект
- Все решения принимаются на основе данных из `VideoDraftBundle`

## Тесты (`tests/test_video_planner.py`)

| Класс | Тест | Описание |
|---|---|---|
| `TestPublicationDecisionType` | `test_all_values_present` | Все значения Enum |
| `TestPublicationPlanHelpers` | (3 теста) | ready_targets, blocked_targets, retry_targets |
| `TestReadyDecision` | (2 теста) | READY для ready+pending, build_plan |
| `TestScheduledDecision` | (2 теста) | SCHEDULED для будущей даты, READY для прошлой |
| `TestWaitingDecision` | (1 тест) | WAITING для draft |
| `TestAlreadyPublishedDecision` | (1 тест) | ALREADY_PUBLISHED |
| `TestBlockedDecision` | (2 теста) | BLOCKED при ошибке валидации, draft не блокируется |
| `TestRetryDecision` | (2 теста) | RETRY для failed, READY для pending |
| `TestPlannerIsolation` | (4 теста) | Не импортирует scheduler/publishers/admin_bot/БД |
| `TestBuildTargetDecision` | (1 тест) | Индивидуальное решение |

## Что не сделано в Phase 4

- Planner не публикует контент
- Planner не пишет в БД
- Planner не меняет статусы
- Нет интеграции с scheduler
- Нет интеграции с publisher-ами

## Следующий этап

**Queue service** — сервис, который:

- Использует Planner для принятия решений
- Ставит READY target-ы в очередь
- Учитывает расписание и лимиты
- Запускает publisher-адаптеры
- Логирует попытки через `video_publication_attempts`