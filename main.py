from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "توکن_ربات"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! ربات فعاله ❤️")

app = Application.builder().token(9080072355:AAGH1IMh6aZfPGteQydTz8u7bnkQhRsOFoc).build()

app.add_handler(CommandHandler("start", start))

print("Bot is running...")
app.run_polling()
