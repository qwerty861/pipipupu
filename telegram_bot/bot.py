"""Telegram bot that relays messages to a Hoplite agent thread and sends back its replies."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from hoplite_client import HopliteClient, HopliteError

log = logging.getLogger("hoplite-telegram")

TELEGRAM_LIMIT = 4096
STATUS_NOTES = {
    "failed": "⚠️ Запуск агента завершился ошибкой.",
    "waiting": "⏸ Агент ждёт ответа или подтверждения. Ответьте здесь или откройте тред в Hoplite.",
    "blocked": "⛔ Агент заблокирован. Откройте тред в Hoplite.",
    "timeout": "⌛ Агент всё ещё работает. Пришлю ответ на следующее сообщение или смотрите тред в Hoplite.",
}
# Prepended to the first message of each thread so replies suit a phone screen.
CHAT_PREAMBLE = (
    "Ты общаешься со мной через Telegram-бота. Отвечай на языке моего сообщения, "
    "коротко, обычным текстом, удобным для чтения с телефона, без Markdown-таблиц.\n\n"
)


class ThreadStore:
    """Persists the Telegram chat -> Hoplite thread mapping in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, chat_id: int) -> str | None:
        return self.data.get(str(chat_id))

    def set(self, chat_id: int, thread_id: str | None) -> None:
        if thread_id is None:
            self.data.pop(str(chat_id), None)
        else:
            self.data[str(chat_id)] = thread_id
        self.path.write_text(json.dumps(self.data, indent=2))


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def parse_allowed(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed = parse_allowed(os.environ.get("ALLOWED_TELEGRAM_USER_IDS", ""))
    if not allowed:
        raise SystemExit("Set ALLOWED_TELEGRAM_USER_IDS: the bot spends Hoplite credits on every message.")

    hoplite = HopliteClient(
        api_key=os.environ["HOPLITE_API_KEY"],
        project_id=os.environ["HOPLITE_PROJECT_ID"],
        base_url=os.environ.get("HOPLITE_API_URL", "https://api.hoplite.sh"),
        model=os.environ.get("HOPLITE_MODEL", "claude-opus-5-5") or None,
    )
    timeout = float(os.environ.get("HOPLITE_RUN_TIMEOUT_SECONDS", "3600"))
    store = ThreadStore(Path(os.environ.get("THREAD_STORE_PATH", "threads.json")))
    locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def authorized(update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id in allowed)

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        await update.message.reply_text(
            "Пишите задачу — я передам её агенту Hoplite.\n/new — начать новый тред\n/thread — текущий тред"
        )

    async def new_thread(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        store.set(update.effective_chat.id, None)
        await update.message.reply_text("Ок, следующее сообщение начнёт новый тред.")

    async def show_thread(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        thread_id = store.get(update.effective_chat.id)
        await update.message.reply_text(f"Текущий тред: {thread_id}" if thread_id else "Треда пока нет.")

    async def keep_typing(chat_id: int, bot) -> None:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            log.warning("Ignored message from user %s", update.effective_user and update.effective_user.id)
            return
        chat_id = update.effective_chat.id
        async with locks[chat_id]:
            thread_id = store.get(chat_id)
            text = update.message.text if thread_id else CHAT_PREAMBLE + update.message.text
            typing = asyncio.create_task(keep_typing(chat_id, context.bot))
            try:
                result = await hoplite.ask(thread_id, text, timeout=timeout)
            except HopliteError as exc:
                log.exception("Hoplite request failed")
                await update.message.reply_text(f"Ошибка Hoplite API: {exc}"[:TELEGRAM_LIMIT])
                return
            finally:
                typing.cancel()
            store.set(chat_id, result.thread_id)

        parts = list(result.replies)
        if note := STATUS_NOTES.get(result.status):
            parts.append(f"{note}\nТред: {result.thread_id}")
        if not parts:
            parts = ["Агент закончил без текстового ответа."]
        for part in parts:
            for chunk in split_message(part):
                await update.message.reply_text(chunk)

    async def on_shutdown(_: Application) -> None:
        await hoplite.aclose()

    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_thread))
    app.add_handler(CommandHandler("thread", show_thread))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
