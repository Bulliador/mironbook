import asyncio
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMIN_ID = int(os.getenv('ADMIN_ID', '0') or 0)
DB_PATH = os.getenv('DB_PATH', 'quest_bot.sqlite3')
PORT = int(os.getenv('PORT', '8080'))
LOCAL_TZ = ZoneInfo(os.getenv('LOCAL_TZ', 'Europe/Warsaw'))
STARTED_AT = time.time()

# ===== РЕДАКТИРУЙ ПРИВЕТСТВИЕ ЗДЕСЬ =====
START_MESSAGES = [
    (0, "Одна книга нашла тебя.\nКогда захочешь узнать, где следующая книга, просто напиши сюда любое сообщение"),
    (3,"<blockquote><b>UPD 13.09.2026</b>\n\n"
        "Книга оставлена давно. Продолжения квеста не будет.\n"
    "Странно и немного больно видеть, как вещи, которые я делал с теплом и заботой — просто чтобы порадовать тебя, поддержать или сделать твой день лучше, — так быстро обесценились и в какой-то момент начали восприниматься совсем иначе.\n"
    "🇩🇪 Беглецу привет.</blockquote>"),
]
QUEST_FINISHED_TEXT = 'Done'
# ==========================================


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def now_local_text():
    return datetime.now(LOCAL_TZ).strftime('%d.%m.%Y %H:%M:%S')


def format_uptime(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f'{days} д')
    if hours or days:
        parts.append(f'{hours} ч')
    if minutes or hours or days:
        parts.append(f'{minutes} мин')
    parts.append(f'{seconds} сек')
    return ' '.join(parts)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = b'{"status":"ok"}'
            content_type = 'application/json; charset=utf-8'
        else:
            body = b'Book Bot is running.'
            content_type = 'text/plain; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(('0.0.0.0', PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'Health server is running on port {PORT}')


def db_connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with db_connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS message_routes (
            admin_message_id INTEGER PRIMARY KEY,
            user_chat_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS start_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            full_name TEXT,
            username TEXT,
            started_at TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS user_state (
            user_id INTEGER PRIMARY KEY,
            first_started_at TEXT NOT NULL
        )''')
        con.execute('''INSERT OR IGNORE INTO user_state (user_id, first_started_at)
            SELECT user_id, MIN(started_at)
            FROM start_events
            GROUP BY user_id''')
        con.commit()


def save_route(admin_message_id, user_chat_id):
    with db_connect() as con:
        con.execute('''INSERT OR REPLACE INTO message_routes
            (admin_message_id, user_chat_id, created_at)
            VALUES (?, ?, ?)''', (admin_message_id, user_chat_id, now_utc_iso()))
        con.commit()


def get_user_chat_id(admin_message_id):
    with db_connect() as con:
        row = con.execute('SELECT user_chat_id FROM message_routes WHERE admin_message_id = ?',
                          (admin_message_id,)).fetchone()
    return row[0] if row else None


def register_start(user_id, chat_id, full_name, username):
    timestamp = now_utc_iso()
    with db_connect() as con:
        existed = con.execute('SELECT 1 FROM user_state WHERE user_id = ?', (user_id,)).fetchone() is not None
        if not existed:
            con.execute('INSERT INTO user_state (user_id, first_started_at) VALUES (?, ?)',
                        (user_id, timestamp))
        con.execute('''INSERT INTO start_events
            (user_id, chat_id, full_name, username, started_at)
            VALUES (?, ?, ?, ?, ?)''',
            (user_id, chat_id, full_name, username, timestamp))
        con.commit()
    return not existed


def reset_start_state(user_id):
    with db_connect() as con:
        cur = con.execute('DELETE FROM user_state WHERE user_id = ?', (user_id,))
        con.commit()
    return cur.rowcount > 0


def get_recent_starts(limit=20):
    with db_connect() as con:
        return con.execute('''SELECT user_id, full_name, username, started_at
            FROM start_events ORDER BY id DESC LIMIT ?''', (limit,)).fetchall()


def extract_user_id_from_admin_card(message):
    text = message.text or message.caption or ''
    match = re.search(r'(?:^|\n)ID:\s*(\d+)(?:\s|$)', text)
    return int(match.group(1)) if match else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if chat_id == ADMIN_ID:
        await update.effective_message.reply_text(
            '🛠 Режим администратора.\n\n'
            'Для повторного теста первого запуска используй:\n'
            '/resetstart TELEGRAM_ID'
        )
        return

    full_name = user.full_name or 'Без имени'
    username_raw = user.username or ''
    username_text = f'@{username_raw}' if username_raw else 'без username'

    is_first_start = register_start(user.id, chat_id, full_name, username_raw)

    if ADMIN_ID:
        status_text = '🆕 первый запуск' if is_first_start else '🔁 повторный запуск'
        admin_notice = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                '▶️ Кто-то нажал /start\n'
                f'Статус: {status_text}\n'
                f'Имя: {full_name}\n'
                f'Username: {username_text}\n'
                f'ID: {user.id}\n'
                f'Время: {now_local_text()}'
            ),
        )
        save_route(admin_notice.message_id, chat_id)

    if not is_first_start:
        await update.effective_message.reply_text(QUEST_FINISHED_TEXT)
        return

    for delay, text in START_MESSAGES:
        if delay > 0:
            await asyncio.sleep(delay)

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
        )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f'Твой Telegram ID: {update.effective_user.id}')


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = format_uptime(time.time() - STARTED_AT)
    if update.effective_user.id == ADMIN_ID:
        await update.effective_message.reply_text(
            f'✅ Book работает\nUptime: {uptime}\nDB: {DB_PATH}\nHealth endpoint: /health'
        )
    else:
        await update.effective_message.reply_text('✅ Бот работает.')


async def starts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    rows = get_recent_starts(20)
    if not rows:
        await update.effective_message.reply_text('Пока никто не нажимал /start.')
        return
    lines = ['▶️ Последние нажатия /start:']
    for user_id, full_name, username, started_at in rows:
        dt_local = datetime.fromisoformat(started_at).astimezone(LOCAL_TZ)
        when = dt_local.strftime('%d.%m %H:%M')
        user_display = f'@{username}' if username else 'без username'
        lines.append(f'\n{when} — {full_name} ({user_display})\nID: {user_id}')
    await update.effective_message.reply_text('\n'.join(lines))


async def resetstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.effective_message.reply_text('Использование:\n/resetstart TELEGRAM_ID')
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text('ID должен быть числом.')
        return
    changed = reset_start_state(user_id)
    if changed:
        await update.effective_message.reply_text(
            f'✅ Состояние /start для ID {user_id} сброшено.\n'
            'Следующий /start снова покажет всю первую цепочку сообщений.'
        )
    else:
        await update.effective_message.reply_text(
            f'ℹ️ Для ID {user_id} активного состояния не было. Следующий /start и так будет первым.'
        )


async def user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID == 0:
        await update.effective_message.reply_text('Бот пока находится в режиме настройки.')
        return
    msg = update.effective_message
    user = update.effective_user
    chat_id = update.effective_chat.id
    full_name = user.full_name or 'Без имени'
    username_text = f'@{user.username}' if user.username else 'без username'

    header = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f'📨 Новое сообщение\nОт: {full_name} ({username_text})\nID: {user.id}',
    )
    save_route(header.message_id, chat_id)

    copied = await context.bot.copy_message(
        chat_id=ADMIN_ID,
        from_chat_id=chat_id,
        message_id=msg.message_id,
    )
    save_route(copied.message_id, chat_id)


async def admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text('Чтобы отправить ответ пользователю, нажми Reply на его сообщение или карточку.')
        return

    replied = msg.reply_to_message
    user_chat_id = get_user_chat_id(replied.message_id) or extract_user_id_from_admin_card(replied)
    if not user_chat_id:
        await msg.reply_text(
            'Не нашёл адресата. Если это старое сообщение до постоянной базы, '
            'ответь Reply на карточку над ним, где указано «ID: ...».'
        )
        return

    try:
        await context.bot.copy_message(
            chat_id=user_chat_id,
            from_chat_id=ADMIN_ID,
            message_id=msg.message_id,
        )
        await msg.reply_text('✅ Отправлено.')
    except Exception as exc:
        await msg.reply_text(f'❌ Не удалось отправить: {exc}')


async def post_init(application: Application):
    if ADMIN_ID:
        try:
            await application.bot.send_message(chat_id=ADMIN_ID, text=f'🤖 Бот запущен.\nБаза: {DB_PATH}')
        except Exception:
            pass


def main():
    if not BOT_TOKEN:
        raise RuntimeError('Не указан BOT_TOKEN.')
    init_db()
    start_health_server()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('id', show_id))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('starts', starts))
    app.add_handler(CommandHandler('resetstart', resetstart))

    if ADMIN_ID:
        app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_ID) & ~filters.COMMAND, admin_message))

    app.add_handler(MessageHandler(~filters.COMMAND, user_message))

    print(f'Database path: {DB_PATH}')
    print('Bot is running...')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
