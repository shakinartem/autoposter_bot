# Autoposter Bot

Локальный Python-бот для автопостинга в несколько соцсетей с базой аккаунтов, расписанием, Telegram-админкой, Cloudinary для Instagram-медиа и контролем срока жизни токенов VK/Instagram.

## Что умеет

- `Telegram`: текст, фото, видео, альбомы
- `VK`: текст, фото, видео на стену
- `Instagram`: фото-пост, видео, карусель, stories через Graph API
- `TikTok`: видео через Content Posting API
- `Cloudinary`: загрузка локальных фото и видео с получением публичного URL для Instagram
- `SQLite`: аккаунты, задания, медиа и расписание
- `Telegram admin bot`: добавление аккаунтов в БД, тестовые публикации и обновление токенов
- `Token warning`: напоминание по VK за 1 час и по Instagram за 24 часа до истечения

## Установка

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -e .
```

## Что заполнить в `.env`

Минимум:

- `TELEGRAM_BOT_TOKEN`
- `VK_TOKEN`
- `INSTAGRAM_IG_USER_ID`
- `INSTAGRAM_ACCESS_TOKEN`
- `TIKTOK_ACCESS_TOKEN`

These are legacy fallback values. The primary flow is now Telegram OAuth linking through `api.spgutils.ru`.

Для Telegram-админки:

- `TELEGRAM_ADMIN_USER_IDS`
- `TOKEN_WARNING_CHAT_ID` или `TELEGRAM_DEFAULT_DESTINATION`

Для Cloudinary:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `CLOUDINARY_FOLDER`

Для VK OAuth-ссылки и напоминаний:

- `VK_TOKEN_LIFETIME_SECONDS`
- `VK_CLIENT_ID`
- `VK_REDIRECT_URI`
- `VK_SCOPE`

Для Instagram OAuth-ссылки и напоминаний:

- `INSTAGRAM_TOKEN_LIFETIME_SECONDS`
- `INSTAGRAM_APP_ID`
- `INSTAGRAM_REDIRECT_URI`
- `INSTAGRAM_SCOPE`

## Как запустить

```powershell
autoposter init-db
autoposter admin-bot
```

## OAuth Flow via Worker

The preferred account-linking flow now goes through `api.spgutils.ru`:

1. In Telegram, open `Аккаунты` and press `Connect TikTok` or `Connect Meta / Instagram`.
2. The bot calls `POST /api/link/start` on the Worker.
3. The Worker returns an `auth_url`.
4. The user authorizes in the browser and the Worker redirects back to Telegram with `oauth_done_<link_token>`.
5. The bot resolves the link result, stores the connection locally, and uses the Worker token API before publishing.

Manual `INSTAGRAM_ACCESS_TOKEN` / `TIKTOK_ACCESS_TOKEN` values are still supported as a fallback, but they are no longer the primary linking path.

## Telegram-админка

Напишите боту:

- `/whoami` — покажет ваш Telegram `user id`
- `/help` — список команд
- `/accounts` — список аккаунтов в БД
- `/delete_account <id>` — удалить аккаунт из БД
- `/vk_token_status` — статус VK токена
- `/set_vk_token <token> [expires_at_iso]` — обновить `VK_TOKEN`; если дата не указана, бот посчитает её от времени сообщения + `VK_TOKEN_LIFETIME_SECONDS`
- `/instagram_token_status` — статус Instagram токена
- `/set_instagram_token <token> [expires_at_iso]` — обновить `INSTAGRAM_ACCESS_TOKEN`; если дата не указана, бот посчитает её от времени сообщения + `INSTAGRAM_TOKEN_LIFETIME_SECONDS`

### Примеры

```text
/set_vk_token vk1.a.your_token
/set_vk_token vk1.a.your_token 2026-04-18T12:00:00
/vk_token_status

/set_instagram_token EA...
/set_instagram_token EA... 2026-06-16T12:00:00
/instagram_token_status
```

### Тестовые публикации

```text
/test_text 2 Привет, это тест
/test_photo 2 C:\media\photo.jpg
/test_photo 2 C:\media\photo.jpg Подпись
/test_video 3 C:\media\video.mp4
```

Для `Instagram`:

- если передаёте локальный путь, бот сам загружает файл в `Cloudinary`
- если передаёте `https://...`, бот использует URL напрямую

## Напоминания по токенам

- `VK`: бот проверяет `VK_TOKEN_EXPIRES_AT` раз в минуту и присылает предупреждение за `1 час`
- `Instagram`: бот проверяет `INSTAGRAM_TOKEN_EXPIRES_AT` раз в минуту и присылает предупреждение за `24 часа`
- в предупреждении приходит готовая OAuth-ссылка
- предупреждение отправляется один раз на каждую дату истечения

## Важные требования

- `Telegram`: бот должен быть админом канала
- `VK`: токен должен иметь права на стену и медиа
- `Instagram`: нужен Business/Creator аккаунт и Graph API access token
- `TikTok`: нужен валидный access token и права на публикацию видео
