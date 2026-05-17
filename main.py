import os
import asyncio
import json
import time
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
from aiohttp import web
from telegram.ext import Application

print("🔥 ORION DAILY ENGINE")

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

klines = defaultdict(lambda: deque(maxlen=120))
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

# ================= ANALYZE =================
def analyze(symbol):

    try:

        data = list(klines[symbol])

        if len(data) < 12:
            return None

        df = pd.DataFrame(
            data,
            columns=["t", "o", "h", "l", "c", "v"]
        )

        df = df.astype(float)

        df = df.dropna()

        if len(df) < 12:
            return None

        price = df["c"].iloc[-1]

        prev = df["c"].iloc[-2]

        move = ((price - prev) / prev) * 100

        volume = df["v"].iloc[-1]

        # EMA
        ema9 = ta.trend.EMAIndicator(
            df["c"],
            9
        ).ema_indicator().iloc[-1]

        ema21 = ta.trend.EMAIndicator(
            df["c"],
            21
        ).ema_indicator().iloc[-1]

        # RSI
        rsi = ta.momentum.RSIIndicator(
            df["c"],
            14
        ).rsi().iloc[-1]

        # AVG VOL
        vol_avg = (
            df["v"]
            .rolling(10)
            .mean()
            .iloc[-1]
        )

        # ================= FILTERS =================

        if ema9 <= ema21:
            return None

        if rsi > 78 or rsi < 30:
            return None

        if volume < vol_avg * 0.5:
            return None

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
            "symbol": symbol.upper(),
            "grade": grade,
            "price": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "score": round(abs(move), 3),
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
        f"🚨 ORION SMART SIGNAL\n\n"

        f"🪙 Coin: {r['symbol']}\n"
        f"📊 Type: {r['grade']}\n\n"

        f"💰 Entry Zone:\n"
        f"{fmt(r['price'])}\n\n"

        f"🎯 Targets:\n"
        f"TP1 → {fmt(r['tp1'])}\n"
        f"TP2 → {fmt(r['tp2'])}\n\n"

        f"🛑 Stop Loss:\n"
        f"{fmt(r['sl'])}\n\n"

        f"📈 Move:\n"
        f"{r['score']}%\n\n"

        f"📦 Volume:\n"
        f"{r['volume']}\n\n"

        f"📊 RSI:\n"
        f"{r['rsi']}\n\n"

        f"⚠️ Risk Management Required"
    )

# ================= CHECK TP/SL =================
async def check_trade(bot, symbol, price):

    if symbol not in open_trades:
        return

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
                f"💰 Price: {fmt(price)}\n\n"
                f"🛡 Move SL To Entry"
            )
        )

    # TP2
    if price >= trade["tp2"]:

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🚀 FINAL TARGET HIT\n\n"
                f"🪙 {symbol}\n"
                f"💰 Price: {fmt(price)}\n\n"
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
                f"💰 Price: {fmt(price)}\n\n"
                f"⚠️ Trade Closed"
            )
        )

        del open_trades[symbol]

# ================= WEBSOCKET =================
async def ws_loop(bot):

    url = "wss://stream.bybit.com/v5/public/spot"

    while True:

        try:

            async with websockets.connect(
                url,
                ping_interval=20
            ) as ws:

                print("🔥 BYBIT CONNECTED")

                await bot.send_message(
                    chat_id=CHAT_ID,
                    text="🔥 Orion Daily Engine Activated"
                )

                args = []

                for s in SYMBOLS:
                    args.append(f"kline.1.{s}")

                # IMPORTANT
                await ws.send(json.dumps({
                    "req_id": "orion",
                    "op": "subscribe",
                    "args": args
                }))

                print("✅ SUBSCRIBED")

                while True:

                    raw = await ws.recv()

                    print(raw)

                    data = json.loads(raw)

                    # Ignore non-topic packets
                    if "topic" not in data:
                        continue

                    if "kline" not in data["topic"]:
                        continue

                    if "data" not in data:
                        continue

                    topic = data.get("topic", "")

                    print("TOPIC:", topic)

                    symbol = (
                        topic
                        .replace("kline.1.", "")
                        .lower()
                    )

                    for k in data["data"]:

                        print("PROCESSING DATA")

                        close_price = float(
                            k.get("close") or 0
                        )

                        open_price = float(
                            k.get("open") or 0
                        )

                        high_price = float(
                            k.get("high") or 0
                        )

                        low_price = float(
                            k.get("low") or 0
                        )

                        volume = float(
                            k.get("volume") or 0
                        )

                        klines[symbol].append([
                            time.time(),
                            open_price,
                            high_price,
                            low_price,
                            close_price,
                            volume
                        ])

                        print(
                            "CANDLES:",
                            symbol,
                            len(klines[symbol])
                        )

                        # TP/SL check
                        await check_trade(
                            bot,
                            symbol.upper(),
                            close_price
                        )

                        print("ANALYZING:", symbol)

                        # Analyze
                        res = analyze(symbol)

                        if not res:
                            continue

                        now = time.time()

                        if symbol in last_signal:

                            if (
                                now - last_signal[symbol]
                                < COOLDOWN
                            ):
                                continue

                        last_signal[symbol] = now

                        open_trades[
                            symbol.upper()
                        ] = res

                        await bot.send_message(
                            chat_id=CHAT_ID,
                            text=signal_message(res)
                        )

        except Exception as e:

            print("WS ERROR:", e)

            await asyncio.sleep(5)

# ================= MAIN =================
async def main():

    await start_health()

    app = Application.builder().token(BOT_TOKEN).build()

    await app.initialize()

    await app.start()

    print("✅ BOT RUNNING")

    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
