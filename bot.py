import os, re, sqlite3, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "quest_bot.sqlite3")
PORT = int(os.getenv("PORT", "8080"))
STARTED_AT = time.time()

def format_uptime(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d} д")
    if h or d: parts.append(f"{h} ч")
    if m or h or d: parts.append(f"{m} мин")
    parts.append(f"{s} сек")
    return " ".join(parts)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}' if self.path == "/health" else b"MironBook Bot is running."
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8" if self.path == "/health" else "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        return

def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health server is running on port {PORT}")

def db_connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    with db_connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS message_routes (
            admin_message_id INTEGER PRIMARY KEY,
            user_chat_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")
        con.commit()

def save_route(admin_message_id, user_chat_id):
    with db_connect() as con:
        con.execute("""INSERT OR REPLACE INTO message_routes
            (admin_message_id, user_chat_id, created_at) VALUES (?, ?, ?)""",
            (admin_message_id, user_chat_id, datetime.now(timezone.utc).isoformat()))
        con.commit()

def get_user_chat_id(admin_message_id):
    with db_connect() as con:
        row = con.execute("SELECT user_chat_id FROM message_routes WHERE admin_message_id = ?", (admin_message_id,)).fetchone()
    return row[0] if row else None

def extract_user_id_from_admin_card(message):
    text = message.text or message.caption or ""
    match = re.search(r"(?:^|\n)ID:\s*(\d+)(?:\s|$)", text)
    return int(match.group(1)) if match else None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id == ADMIN_ID:
        await update.effective_message.reply_text(
            "🛠 Режим администратора.\n\nКогда кто-то напишет боту, его сообщение появится здесь.\n"
            "Чтобы ответить от имени бота — нажми Reply на сообщение пользователя или на его карточку."
        )
    else:
        await update.effective_message.reply_text(
            "Одна книга нашла тебя.\n"
            "Когда захочешь узнать, где следующая книга, просто напиши сюда любое сообщение"
        )

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Твой Telegram ID: {update.effective_user.id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = format_uptime(time.time() - STARTED_AT)
    if update.effective_user.id == ADMIN_ID:
        await update.effective_message.reply_text(
            f"✅ MironBook работает\nUptime: {uptime}\nDB: {DB_PATH}\nHealth endpoint: /health"
        )
    else:
        await update.effective_message.reply_text("✅ Бот работает.")

async def user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID == 0:
        await update.effective_message.reply_text("Бот пока находится в режиме настройки.")
        return
    msg, user = update.effective_message, update.effective_user
    chat_id = update.effective_chat.id
    name = user.full_name or "Без имени"
    username = f"@{user.username}" if user.username else "без username"
    header = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📨 Новое сообщение\nОт: {name} ({username})\nID: {user.id}"
    )
    save_route(header.message_id, chat_id)
    copied = await context.bot.copy_message(chat_id=ADMIN_ID, from_chat_id=chat_id, message_id=msg.message_id)
    save_route(copied.message_id, chat_id)

async def admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Чтобы отправить ответ пользователю, нажми Reply на его сообщение или карточку.")
        return
    replied = msg.reply_to_message
    user_chat_id = get_user_chat_id(replied.message_id) or extract_user_id_from_admin_card(replied)
    if not user_chat_id:
        await msg.reply_text(
            "Не нашёл адресата. Если это старое сообщение до постоянной базы, "
            "ответь Reply на карточку над ним, где указано «ID: ...»."
        )
        return
    try:
        await context.bot.copy_message(chat_id=user_chat_id, from_chat_id=ADMIN_ID, message_id=msg.message_id)
        await msg.reply_text("✅ Отправлено.")
    except Exception as exc:
        await msg.reply_text(f"❌ Не удалось отправить: {exc}")

async def post_init(application: Application):
    if ADMIN_ID:
        try:
            await application.bot.send_message(chat_id=ADMIN_ID, text=f"🤖 Бот запущен.\nБаза: {DB_PATH}")
        except Exception:
            pass

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN.")
    init_db()
    start_health_server()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("status", status))
    if ADMIN_ID:
        app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_ID) & ~filters.COMMAND, admin_message))
    app.add_handler(MessageHandler(~filters.COMMAND, user_message))
    print(f"Database path: {DB_PATH}")
    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
