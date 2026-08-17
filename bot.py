import os
import re
import sqlite3
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "clipboard.db")

CATS = {
    "url":   {"label": "Link",   "emoji": "🔗"},
    "email": {"label": "Email",  "emoji": "📧"},
    "phone": {"label": "Phone",  "emoji": "📱"},
    "code":  {"label": "Code",   "emoji": "💻"},
    "number":{"label": "Number", "emoji": "🔢"},
    "text":  {"label": "Text",   "emoji": "📝"},
}

CODE_HINTS = re.compile(r"[{};]|=>|function\s|const\s|let\s|import\s|def\s|class\s|</?\w+>")


def categorize(raw: str) -> str:
    t = raw.strip()
    if not t:
        return "text"
    if re.match(r"^(https?://|www\.)\S+$", t, re.I):
        return "url"
    if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", t):
        return "email"
    if re.match(r"^\+?[\d\s\-()]{7,16}$", t):
        return "phone"
    if re.match(r"^-?\d+(\.\d+)?$", t):
        return "number"
    if CODE_HINTS.search(t) and (("\n" in t) or len(t) < 300):
        return "code"
    return "text"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    return conn


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Clipboard Sync Bot\n\n"
        "Jo bhi text, link, email, phone number ya code mujhe bhejoge (ya forward karoge), "
        "main use apne aap category me save kar lunga.\n\n"
        "Commands:\n"
        "/list - saari saved entries dekho\n"
        "/list <category> - ek category dekho (url, email, phone, code, number, text)\n"
        "/stats - kitni entries kis category me hain\n"
        "/clear - sab delete karo"
    )


async def save_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text or msg.caption
    if not text:
        return
    user_id = update.effective_user.id
    category = categorize(text)
    conn = db()
    conn.execute(
        "INSERT INTO entries (user_id, content, category, created_at) VALUES (?, ?, ?, ?)",
        (user_id, text, category, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    c = CATS[category]
    preview = text if len(text) <= 120 else text[:117] + "..."
    await msg.reply_text(f"{c['emoji']} Saved as *{c['label']}*\n\n{preview}", parse_mode="Markdown")


def category_keyboard():
    buttons = [
        InlineKeyboardButton(f"{v['emoji']} {v['label']}", callback_data=f"list:{k}")
        for k, v in CATS.items()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("📋 All", callback_data="list:all")])
    return InlineKeyboardMarkup(rows)


async def list_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Category chuno:", reply_markup=category_keyboard())
        return
    category = args[0].lower()
    await send_list(update.effective_chat.id, update.effective_user.id, category, context)


async def send_list(chat_id, user_id, category, context):
    conn = db()
    if category == "all":
        rows = conn.execute(
            "SELECT content, category, created_at FROM entries WHERE user_id=? ORDER BY id DESC LIMIT 25",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT content, category, created_at FROM entries WHERE user_id=? AND category=? ORDER BY id DESC LIMIT 25",
            (user_id, category),
        ).fetchall()
    conn.close()

    if not rows:
        await context.bot.send_message(chat_id, "Is category me abhi kuch nahi hai.")
        return

    lines = []
    for content, cat, created_at in rows:
        c = CATS.get(cat, CATS["text"])
        preview = content if len(content) <= 80 else content[:77] + "..."
        lines.append(f"{c['emoji']} {preview}")

    text = "\n\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n\n...(aur bhi hain, upar 25 tak dikhaye ja rahe hain)"
    await context.bot.send_message(chat_id, text)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    await send_list(query.message.chat_id, query.from_user.id, category, context)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = db()
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM entries WHERE user_id=? GROUP BY category", (user_id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Abhi kuch bhi saved nahi hai.")
        return
    counts = dict(rows)
    lines = [f"{CATS[k]['emoji']} {CATS[k]['label']}: {v}" for k, v in counts.items()]
    total = sum(counts.values())
    await update.message.reply_text(f"📊 Total: {total}\n\n" + "\n".join(lines))


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = db()
    conn.execute("DELETE FROM entries WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑️ Sab clear kar diya.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable set nahi hai.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_entries))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, save_message))

    logger.info("Bot starting (polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
