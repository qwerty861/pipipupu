# Hoplite Telegram bot

Пересылает ваши сообщения из Telegram агенту Hoplite через [Hoplite API](https://hoplite.sh/docs/api) и присылает обратно его ответы.

- Один Telegram-чат соответствует одному треду Hoplite. Следующие сообщения продолжают тот же тред, поэтому агент помнит контекст.
- `/new` начинает новый тред, `/thread` показывает ID текущего.
- Пока агент работает, бот показывает «печатает…». Если агент ждёт подтверждения или вопроса, бот сообщит об этом.
- Отвечает только пользователям из `ALLOWED_TELEGRAM_USER_IDS`, потому что каждое сообщение тратит кредиты Hoplite.

## Настройка

1. **Токен бота.** В Telegram откройте @BotFather, выполните `/newbot` и скопируйте токен.
2. **Ваш Telegram ID.** Его покажет, например, @userinfobot.
3. **Ключ Hoplite.** Settings → API keys → *Create key* (ключ начинается с `hop_`).
4. **ID проекта Hoplite** (`proj_…`):
   ```bash
   curl -s -H "Authorization: Bearer $HOPLITE_API_KEY" https://api.hoplite.sh/api/projects
   ```
5. Скопируйте `.env.example` в `.env` и заполните.

## Запуск

```bash
cd telegram_bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
set -a && . ./.env && set +a
python bot.py
```

Бот работает через long polling, поэтому публичный URL не нужен. Подойдёт любой постоянно работающий сервер (VPS, systemd, Docker). Привязки «чат → тред» хранятся в `threads.json`.

## Тесты

```bash
pip install -r requirements-dev.txt && python -m pytest -q
```
