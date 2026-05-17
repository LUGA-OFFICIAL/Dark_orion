import os
import asyncio
import requests
import pandas as pd
import ta

from aiohttp import web
from telegram.ext import Application

print("🔥 ORION BINANCE ENGINE")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

COOLDOWN = 900

# ================= COINS =================
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "BNBUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "MATICUSDT",
    "ARBUSDT",
    "OPUSDT",
    "APTUSDT",
    "INJUSDT",
    "SUIUSDT",
    "SEIUSDT",
    "NEARUSDT",
    "ATOMUSDT",
    "FILUSDT",
    "LTCUSDT",
    "TRXUSDT",
    "AAVEUSDT",
    "UNIUSDT",
    "ETCUSDT",
    "ICPUSDT"
]

last_signal = {}
open_trades = {}

# ================= HEALTH =================
async def health(request):
    return web.Response(text="OK")

async def start_health():

    app = web.Application()

    app.router.add_get("/", health)

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(f"✅ Health server on {PORT}")

# ================= FORMAT =================
def fmt(x):

    if x >= 1000:
        return f"{x:,.2f}"

    if x >= 1:
        return f"{x:.4f}"

    return f"{x:.8f}"

# ================= GET KLINES =================
def get_klines(symbol):

    try:

        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}"
            f"&interval=1m"
            f"&limit=50"
        )

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        r = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        data = r.json()

        df = pd.DataFrame(data)

        df = df.iloc[:, :6]

        df.columns = [
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        df = df.astype(float)

        return df

    except Exception as e:

        print("DATA ERROR:", e)

        return None

# ================= ANALYZE =================
def analyze(symbol):

    try:

        df = get_klines(symbol)

        if df is None or len(df) < 20:
            return None

        price = df["close"].iloc[-1]

        prev = df["close"].iloc[-2]

        move = ((price - prev) / prev) * 100

        volume = df["volume"].iloc[-1]

        # ================= EMA =================
        ema9 = ta.trend.EMAIndicator(
            df["close"],
            9
        ).ema_indicator().iloc[-1]

        ema21 = ta.trend.EMAIndicator(
            df["close"],
            21
        ).ema_indicator().iloc[-1]

        # ================= RSI =================
        rsi = ta.momentum.RSIIndicator(
            df["close"],
            14
        ).rsi().iloc[-1]

        # ================= AVG VOL =================
        vol_avg = (
            df["volume"]
            .rolling(10)
            .mean()
            .iloc[-1]
        )

        # ================= FILTERS =================

        # اتجاه
        if ema9 <= ema21:
            return None

        # RSI
        if rsi > 78 or rsi < 30:
            return None

        # حجم
        if volume < vol_avg * 0.5:
            return None

        # حركة
        if move < 0.05:
            return None

        # ================= SIGNAL LEVEL =================

        if move > 2:

            grade = "🐋 SNIPER"

            tp1 = price * 1.02
            tp2 = price * 1.05
            sl = price * 0.99

        elif move > 1:

            grade = "🔥 HIGH"

            tp1 = price * 1.015
            tp2 = price * 1.03
            sl = price * 0.992

        elif move > 0.3:

            grade = "⚡ MEDIUM"

            tp1 = price * 1.008
            tp2 = price * 1.015
            sl = price * 0.994

        else:

            grade = "🎯 SCALP"

            tp1 = price * 1.003
            tp2 = price * 1.006
            sl = price * 0.997

        return {
            "symbol": symbol,
            "grade": grade,
            "price": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "move": round(move, 3),
            "volume": round(volume, 2),
            "rsi": round(rsi, 1),
            "tp1_hit": False
        }

    except Exception as e:

        print("ANALYZE ERROR:", e)

        return None

# ================= MESSAGE =================
def signal_message(r):

    return (
        f"🚨 ORION SIGNAL\n\n"

        f"🪙 {r['symbol']}\n"
        f"📊 {r['grade']}\n\n"

        f"💰 Entry: {fmt(r['price'])}\n"
        f"🎯 TP1: {fmt(r['tp1'])}\n"
        f"🎯 TP2: {fmt(r['tp2'])}\n"
        f"🛑 SL: {fmt(r['sl'])}\n\n"

        f"📈 Move: {r['move']}%\n"
        f"📦 Volume: {r['volume']}\n"
        f"📊 RSI: {r['rsi']}"
    )

# ================= TP/SL =================
async def check_trade(bot, symbol):

    if symbol not in open_trades:
        return

    try:

        df = get_klines(symbol)

        if df is None:
            return

        price = df["close"].iloc[-1]

        trade = open_trades[symbol]

        # TP1
        if (
            not trade["tp1_hit"]
            and price >= trade["tp1"]
        ):

            trade["tp1_hit"] = True

            trade["sl"] = trade["price"]

            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🎯 TARGET 1 HIT\n\n"
                    f"🪙 {symbol}\n"
                    f"💰 {fmt(price)}\n\n"
                    f"🛡 SL moved to entry"
                )
            )

        # TP2
        if price >= trade["tp2"]:

            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🚀 FINAL TARGET HIT\n\n"
                    f"🪙 {symbol}\n"
                    f"💰 {fmt(price)}\n\n"
                    f"✅ Trade Completed"
                )
            )

            del open_trades[symbol]

        # SL
        elif price <= trade["sl"]:

            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🛑 STOP LOSS HIT\n\n"
                    f"🪙 {symbol}\n"
                    f"💰 {fmt(price)}"
                )
            )

            del open_trades[symbol]

    except Exception as e:

        print("CHECK ERROR:", e)

# ================= LOOP =================
async def signal_loop(bot):

    while True:

        try:

            for symbol in SYMBOLS:

                print("CHECKING:", symbol)

                # CHECK TP/SL
                await check_trade(
                    bot,
                    symbol
                )

                # ANALYZE
                res = analyze(symbol)

                if not res:
                    continue

                now = asyncio.get_event_loop().time()

                if symbol in last_signal:

                    if (
                        now - last_signal[symbol]
                        < COOLDOWN
                    ):
                        continue

                last_signal[symbol] = now

                open_trades[symbol] = res

                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=signal_message(res)
                )

                print("SIGNAL:", symbol)

                await asyncio.sleep(2)

        except Exception as e:

            print("LOOP ERROR:", e)

        # كل دقيقة
        await asyncio.sleep(60)

# ================= MAIN =================
async def main():

    await start_health()

    app = Application.builder().token(BOT_TOKEN).build()

    await app.initialize()

    await app.start()

    print("✅ BOT RUNNING")

    bot = app.bot

    await bot.send_message(
        chat_id=CHAT_ID,
        text="🔥 ORION BINANCE ENGINE ACTIVATED"
    )

    asyncio.create_task(
        signal_loop(bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
