from telegram.ext import Updater, CommandHandler
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

def start(update, context):
    update.message.reply_text("🔥 شغال")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))

    print("🔥 RUNNING NEW FILE")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
print("FORCE NEW DEPLOY")
