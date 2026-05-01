import os
import asyncio
import ccxt
import pandas as pd
import ta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))

exchange = ccxt.binance({"enableRateLimit": True})

SYMBOLS = ["BTC/USDT","ETH/USDT","SOL/USDT"]

# ================= تحليل =================
def get_data(symbol):
    data = exchange.fetch_ohlcv(symbol, "1h", limit=100)
    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])
    return df

def analyze(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["macd_s"] = macd.macd_signal()

    r = df.iloc[-1]

    if r["rsi"] < 35 and r["macd"] > r["macd_s"]:
        return "BUY"
    elif r["rsi"] > 65 and r["macd"] < r["macd_s"]:
        return "SELL"

    return None

# ================= إرسال إشارات =================
async def scan(context):
    for s in SYMBOLS:
        try:
            df = get_data(s)
            sig = analyze(df)

            if sig:
                price = df.iloc[-1]["c"]

                msg = f"""📊 {s}
Signal: {sig}
Price: {price:.2f}"""

                await context.bot.send_message(chat_id=CHAT_ID, text=msg)

        except Exception as e:
            print("force rebuild")

# ================= أوامر =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 البوت شغال مع التحليل")

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري التحليل...")
    await scan(context)

# ================= تشغيل =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan_cmd))

    # تشغيل تلقائي كل ساعة
    app.job_queue.run_repeating(scan, interval=3600, first=10)

    print("🔥 BOT STARTED WITH ANALYSIS")
    app.run_polling()

if __name__ == "__main__":
    main()
