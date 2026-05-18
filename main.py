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

print("🔥 LIVE SIGNAL MODE")

BOT_TOKEN = os.getenv("BOT_TOKEN")

# الخاص
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

# الجروب
GROUP_CHAT_ID = int(
    os.getenv("GROUP_CHAT_ID", "0")
)

PORT = int(os.getenv("PORT", "8080"))

COOLDOWN = 180

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
    "MATICUSDT"
]

klines = defaultdict(lambda: deque(maxlen=50))

last_signal = {}

open_trades = {}

# ================= SEND =================
async def send_all(bot, text):

    try:

        if CHAT_ID != 0:

            await bot.send_message(
                chat_id=CHAT_ID,
                text=text
            )

    except Exception as e:

        print("PRIVATE SEND ERROR:", e)

    try:

        if GROUP_CHAT_ID != 0:

            await bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=text
            )

    except Exception as e:

        print("GROUP SEND ERROR:", e)

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

        if len(data) < 8:
            return None

        df = pd.DataFrame(
            data,
            columns=["t", "o", "h", "l", "c", "v"]
        )

        df = df.astype(float)

        df = df.dropna()

        if len(df) < 8:
            return None

        price = df["c"].iloc[-1]

        prev = df["c"].iloc[-2]

        move = ((price - prev) / prev) * 100

        volume = df["v"].iloc[-1]

        # EMA FILTER
        ema9 = ta.trend.EMAIndicator(
            df["c"],
            window=9
        ).ema_indicator().iloc[-1]

        ema21 = ta.trend.EMAIndicator(
            df["c"],
            window=21
        ).ema_indicator().iloc[-1]

        # اتجاه صاعد فقط
        if ema9 <= ema21:
            return None

        # تجاهل الحركات الصغيرة
        if abs(move) < 0.08:
            return None

        # تجاهل الدخول المتأخر
        if move > 1.2:
            return None

        # ================= GRADES =================

        if move > 0.8:

            grade = "🐋 SNIPER"

            tp1 = price * 1.02
            tp2 = price * 1.04
            sl = price * 0.992

        elif move > 0.35:

            grade = "🔥 HIGH"

            tp1 = price * 1.015
            tp2 = price * 1.03
            sl = price * 0.994

        elif move > 0.12:

            grade = "⚡ MEDIUM"

            tp1 = price * 1.008
            tp2 = price * 1.015
            sl = price * 0.996

        else:

            return None

        return {
            "symbol": symbol.upper(),
            "grade": grade,
            "price": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "score": round(abs(move), 3),
            "volume": round(volume, 2),
            "tp1_hit": False
        }

    except Exception as e:

        print("ANALYZE ERROR:", e)

        return None

# ================= MESSAGE =================
def signal_message(r):

    return (
        f"{r['grade']} SIGNAL\n\n"

        f"🪙 {r['symbol']}\n\n"

        f"💰 Entry: {fmt(r['price'])}\n"
        f"🎯 TP1: {fmt(r['tp1'])}\n"
        f"🎯 TP2: {fmt(r['tp2'])}\n"
        f"🛑 SL: {fmt(r['sl'])}\n\n"

        f"📊 Move: {r['score']}%\n"
        f"📦 Volume: {r['volume']}"
    )

# ================= CHECK TRADE =================
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

        await send_all(
            bot,
            (
                f"🎯 TP1 HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}\n\n"
                f"🛡 SL moved to entry"
            )
        )

    # TP2
    if price >= trade["tp2"]:

        await send_all(
            bot,
            (
                f"🚀 TP2 HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}"
            )
        )

        del open_trades[symbol]

    # SL
    elif price <= trade["sl"]:

        await send_all(
            bot,
            (
                f"🛑 SL HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}"
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

                await send_all(
                    bot,
                    "🚀 Orion Signals Engine Online"
                )

                args = []

                for s in SYMBOLS:

                    args.append(f"kline.1.{s}")

                sub = {
                    "op": "subscribe",
                    "args": args
                }

                await ws.send(json.dumps(sub))

                print("✅ SUBSCRIBED")

                while True:

                    raw = await ws.recv()

                    data = json.loads(raw)

                    if "data" not in data:
                        continue

                    topic = data.get("topic", "")

                    symbol = (
                        topic
                        .replace("kline.1.", "")
                        .lower()
                    )

                    for k in data["data"]:

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
                            "DATA:",
                            symbol,
                            len(klines[symbol])
                        )

                        # CHECK TRADE
                        await check_trade(
                            bot,
                            symbol.upper(),
                            close_price
                        )

                        # ANALYZE
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

                        await send_all(
                            bot,
                            signal_message(res)
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

    # ================= GROUP TEST =================
    try:

        await app.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="✅ GROUP TEST"
        )

        print("GROUP OK")

    except Exception as e:

        print("GROUP ERROR:", e)

    # ================= START WS =================
    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
