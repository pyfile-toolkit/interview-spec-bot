import os
import sys
import tempfile
from typing import Any

from telegram import InputFile, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import pdf as pdf_mod
from . import store
from .questions import BY_ID, next_question

DEFAULT_BASE_URL = "https://api.telegram.org/bot"


def bot_token_or_die() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print(
            "Ошибка: не найден env TELEGRAM_BOT_TOKEN (нужен токен для Telegram-бота).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def base_url() -> str:
    return os.environ.get("TELEGRAM_BASE_URL") or DEFAULT_BASE_URL


def _format_question_text(qid: str) -> str:
    q = BY_ID[qid]
    return f"{q.text}\n\nПодсказка: {q.hint}\n(минимум {q.min_len} символов)"


def _ensure_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if update.effective_user is None:
        raise RuntimeError("Нет effective_user")

    sid_key = "interview_sid"
    if sid_key not in context.user_data:
        context.user_data[sid_key] = store.create_session()
    return context.user_data[sid_key]


def _validate_answer(q: Any, text: str) -> tuple[bool, str | None]:
    if not text:
        return False, "Пустой ответ: напишите хотя бы одно предложение"
    if len(text) < q.min_len:
        return False, f"Слишком короткий ответ: нужно минимум {q.min_len} символов"
    return True, None


async def start_over(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.effective_chat is None:
        return

    context.user_data["interview_sid"] = store.create_session()
    q = next_question({})
    assert q is not None

    await update.effective_chat.send_message(
        text=_format_question_text(q.id),
        disable_notification=True,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_over(update, context)


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_over(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.effective_chat is None:
        return
    await update.effective_chat.send_message(
        text="Ок, отменено. Если хотите пройти демо заново, нажмите /start.",
        disable_notification=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    await update.effective_chat.send_message(
        text=(
            "Демо «интервью → ТЗ».\n\n"
            "Команды:\n"
            "/start или /restart: начать заново\n"
            "/cancel: отменить\n"
            "/help: помощь\n\n"
            "Отвечайте обычными текстовыми сообщениями."
        ),
        disable_notification=True,
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    sid = _ensure_session(update, context)
    session = store.get_session(sid)
    if session is None:
        context.user_data.clear()
        await update.effective_chat.send_message(
            text="Сессия потерялась. Начните заново через /start.",
            disable_notification=True,
        )
        return

    q = next_question(session["answers"])
    text = (update.message.text or "").strip() if update.message else ""

    if q is None:
        await update.effective_chat.send_message(
            text="Интервью уже завершено. Нажмите /restart, чтобы пройти заново.",
            disable_notification=True,
        )
        return

    ok, err = _validate_answer(q, text)
    if not ok:
        await update.effective_chat.send_message(
            text=err or "Некорректный ответ.",
            disable_notification=True,
        )
        return

    store.save_answer(sid, q.id, text)

    # пересчитать вопрос
    session2 = store.get_session(sid)
    assert session2 is not None

    q2 = next_question(session2["answers"])
    if q2 is not None:
        await update.effective_chat.send_message(
            text=_format_question_text(q2.id),
            disable_notification=True,
        )
        return

    # завершено
    store.mark_finished(sid)
    body = pdf_mod.build_pdf(session2["answers"], sid)

    tmpdir = tempfile.gettempdir()
    fname = f"spec-{sid[:8]}.pdf"
    path = os.path.join(tmpdir, fname)
    with open(path, "wb") as f:
        f.write(body)

    first_line = lambda key: (session2["answers"].get(key, "").splitlines()[0][:80] if session2["answers"].get(key) else "")

    await update.effective_chat.send_document(
        document=InputFile(path, filename=fname),
        filename=fname,
        caption=(
            "ТЗ готово. Короткое резюме:\n\n"
            f"• Про продукт: {first_line('product')}\n"
            f"• ЦА: {first_line('audience')}\n"
            f"• Функции: {first_line('features')}\n"
            f"• Готовность: {first_line('done')}"
        ),
        disable_notification=True,
    )


def build_application(token: str | None = None):
    """Собирает приложение бота. Токен и база Bot API — только из env/аргумента.

    Базу подменяем на этапе сборки: у `Bot.base_url` нет сеттера (свойство только
    на чтение), поэтому присваивание в него молча роняло бы бота в тестах.
    """
    token = token or bot_token_or_die()
    return Application.builder().token(token).base_url(base_url()).build()


def register_handlers(application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("restart", cmd_restart))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


async def _post_init(application) -> None:
    """best-effort: стенд может не поддерживать setMyCommands.

    Вызываем именно здесь: set_my_commands — корутина, и в синхронном main()
    вызов без await только порождал RuntimeWarning и ничего не регистрировал.
    """
    try:
        await application.bot.set_my_commands(
            commands=[
                ("start", "начать заново"),
                ("help", "помощь"),
                ("cancel", "отменить"),
                ("restart", "перезапуск"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — стенд может не уметь этот метод
        print(f"setMyCommands недоступен: {exc}", file=sys.stderr)


def main() -> None:
    app = build_application()
    register_handlers(app)
    app.post_init = _post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES, poll_interval=0.2)


if __name__ == "__main__":
    main()
