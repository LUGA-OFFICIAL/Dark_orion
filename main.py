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

print("🔥 BEAST MODE STARTING")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

COOLDOWN = 1800

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
    "INJUSDT",
    "APTUSDT",
    "SUIUSDT"
]

klines = defaultdict(lambda: deque(maxlen=300))
last_signal = {}

# ================= HEALTH =================
async def health(request):
    return web.Response(text="OK")

async def start_health():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"✅ Health server on {PORT}")

# ================= FORMAT =================
def fmt(x):
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.8f}"

# ================= ANALYSIS =================
def analyze(symbol):

    try:
        data = list(klines[symbol])

        if len(data) < 40:
            return None

        df = pd.DataFrame(
            data,
            columns=["t","o","h","l","c","v"]
        )

        price = df["c"].iloc[-1]

        # ================= INDICATORS =================
        df["ema9"] = ta.trend.EMAIndicator(df["c"], 9).ema_indicator()
        df["ema21"] = ta.trend.EMAIndicator(df["c"], 21).ema_indicator()

        rsi = ta.momentum.RSIIndicator(df["c"], 14).rsi().iloc[-1]

        vol_now = df["v"].iloc[-1]
        vol_avg = df["v"].rolling(20).mean().iloc[-1]

        recent_move = df["c"].pct_change().tail(3).sum()

        score = 0

        # ================= TREND =================
        if df["ema9"].iloc[-1] > df["ema21"].iloc[-1]:
            score += 25

        # ================= RSI =================
        if rsi < 35:
            score += 25
        elif rsi < 50:
            score += 15

        # ================= VOLUME =================
        if vol_now > vol_avg * 1.3:
            score += 25

        # ================= MOMENTUM =================
        if recent_move > 0.008:
            score += 25

        # ================= GRADE =================
        if score >= 70:
            grade = "🔥 HIGH"
        elif score >= 50:
            grade = "⚡ MEDIUM"
        elif score >= 35:
            grade = "🎯 SCALP"
        else:
            return None

        atr = ta.volatility.AverageTrueRange(
            df["h"],
            df["l"],
            df["c"]
        ).average_true_range().iloc[-1]

        tp1 = price + atr * 1.2
        tp2 = price + atr * 2.0
        sl = price - atr

        return {
            "symbol": symbol,
            "grade": grade,
            "price": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "score": score,
            "rsi": round(rsi, 1)
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
        f"📊 Score: {r['score']}%\n"
        f"📈 RSI: {r['rsi']}"
    )

# ================= WEBSOCKET =================
async def ws_loop(bot):

    url = "wss://stream.bybit.com/v5/public/spot"

    while True:

        try:

            async with websockets.connect(
                url,
                ping_interval=20
            ) as ws:

                print("🔥 WS CONNECTED")

                await bot.send_message(
                    chat_id=CHAT_ID,
                    text="🔥 Market Connected"
                )

                args = []

                for s in SYMBOLS:
                    args.append(f"kline.1.{s}")

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": args
                }))

                async for raw in ws:

                    data = json.loads(raw)

                    if "data" not in data:
                        continue

                    for k in data["data"]:

                        symbol = k.get("symbol")

                        if not symbol:
                            continue

                        klines[symbol].append([
                            k.get("start"),
                            float(k.get("open", 0)),
                            float(k.get("high", 0)),
                            float(k.get("low", 0)),
                            float(k.get("close", 0)),
                            float(k.get("volume", 0)),
                        ])

                        res = analyze(symbol)

                        if not res:
                            continue

                        now = time.time()

                        if symbol in last_signal:
                            if now - last_signal[symbol] < COOLDOWN:
                                continue

                        last_signal[symbol] = now

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

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text="🔥 Beast Mode Activated"
    )

    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
