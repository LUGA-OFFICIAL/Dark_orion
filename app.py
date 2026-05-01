from telegram.ext import Updater, CommandHandler
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

def start(update, context):
    update.message.reply_text("🔥 شغال")

def main():
    updater = Updater(BOT_TOKEN)  # ❌ حذف use_context
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))

    print("🔥 RUNNING CLEAN")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
