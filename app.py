import os
import ccxt
import pandas as pd
import ta

from telegram.ext import Application

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID"))

exchange = ccxt.binance()

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# تحليل بسيط
def analyze(symbol):
    ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=100)
    df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["signal"] = macd.macd_signal()

    last = df.iloc[-1]

    if last["rsi"] < 35 and last["macd"] > last["signal"]:
        return "BUY"
    elif last["rsi"] > 65 and last["macd"] < last["signal"]:
        return "SELL"
    return None

# إرسال إشارات
async def send_signals(app):
    for symbol in SYMBOLS:
        try:
            sig = analyze(symbol)
            if sig:
                msg = f"📊 {symbol}\nSignal: {sig}"
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            print(e)

# تشغيل تلقائي
async def job(context):
    await send_signals(context.application)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # كل ساعة
    app.job_queue.run_repeating(job, interval=3600, first=10)

    print("🔥 AUTO SIGNAL BOT STARTED")
    app.run_polling()

if __name__ == "__main__":
    main()
