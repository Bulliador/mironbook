import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "quest_bot.sqlite3")


def db_connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db_connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS message_routes (
                admin_message_id INTEGER PRIMARY KEY,
                user_chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.commit()


def save_route(admin_message_id: int, user_chat_id: int):
    with db_connect() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO message_routes
            (admin_message_id, user_chat_id, created_at)
            VALUES (?, ?, ?)
            """,
            (
                admin_message_id,
                user_chat_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()


def get_user_chat_id(admin_message_id: int):
    with db_connect() as con:
        row = con.execute(
            "SELECT user_chat_id FROM message_routes WHERE admin_message_id = ?",
            (admin_message_id,),
        ).fetchone()
    return row[0] if row else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id == ADMIN_ID:
        await update.effective_message.reply_text(
            "🛠 Режим администратора.\n\n"
            "Когда кто-то напишет боту, его сообщение появится здесь.\n"
            "Чтобы ответить от имени бота — нажми Reply на сообщение пользователя "
            "и отправь текст, фото, стикер или другой поддерживаемый тип сообщения."
        )
        return

    await update.effective_message.reply_text(
        "Привет 👀\n\n"
        "Похоже, ты нашла начало этой истории.\n"
        "Когда захочешь продолжить — просто напиши мне сюда."
    )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Твой Telegram ID: {update.effective_user.id}"
    )


async def user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Copy a player's message to the admin and remember where replies must go."""
    if ADMIN_ID == 0:
        await update.effective_message.reply_text(
            "Бот пока находится в режиме настройки."
        )
        return

    msg = update.effective_message
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Small header so the admin knows who wrote.
    name = user.full_name or "Без имени"
    username = f"@{user.username}" if user.username else "без username"

    header = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📨 Новое сообщение\n"
            f"От: {name} ({username})\n"
            f"ID: {user.id}"
        ),
    )
    save_route(header.message_id, chat_id)

    # Copy instead of forward: the recipient does not see a "forwarded from" label.
    copied = await context.bot.copy_message(
        chat_id=ADMIN_ID,
        from_chat_id=chat_id,
        message_id=msg.message_id,
    )
    save_route(copied.message_id, chat_id)


async def admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send an admin's Reply back to the original player."""
    msg = update.effective_message

    if not msg.reply_to_message:
        await msg.reply_text(
            "Чтобы отправить ответ пользователю от имени бота, "
            "нажми Reply на его сообщение."
        )
        return

    replied_to_id = msg.reply_to_message.message_id
    user_chat_id = get_user_chat_id(replied_to_id)

    if not user_chat_id:
        await msg.reply_text(
            "Не нашёл адресата для этого сообщения. "
            "Ответь Reply именно на сообщение/карточку пользователя."
        )
        return

    try:
        await context.bot.copy_message(
            chat_id=user_chat_id,
            from_chat_id=ADMIN_ID,
            message_id=msg.message_id,
        )
        await msg.reply_text("✅ Отправлено.")
    except Exception as exc:
        await msg.reply_text(f"❌ Не удалось отправить: {exc}")


async def post_init(application: Application):
    if ADMIN_ID:
        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🤖 Бот запущен и готов принимать сообщения."
            )
        except Exception:
            pass


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не указан BOT_TOKEN. Создай файл .env по примеру .env.example."
        )

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))

    if ADMIN_ID:
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=ADMIN_ID) & ~filters.COMMAND,
                admin_message,
            )
        )

    app.add_handler(
        MessageHandler(
            ~filters.COMMAND,
            user_message,
        )
    )

    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
