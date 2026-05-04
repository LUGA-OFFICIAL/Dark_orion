print("🔥 BOT STARTING...")

import os
import asyncio
import json
import time
from collections import defaultdict, deque
from aiohttp import web

import pandas as pd
import ta
import websockets
from telegram.ext import Application

print("🚨 BOT FILE LOADED")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
PORT      = int(os.getenv("PORT", "8080"))

klines = defaultdict(lambda: deque(maxlen=200))

# ================= STATE =================
last_signal_time = {}
open_trades = {}

# ================= HEALTH =================
async def health(request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print("✅ Health server running on port", PORT)

# ================= ANALYSIS =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        k5 = list(klines[f"{symbol}_5"])

        if len(k1) < 80 or len(k5) < 80:
            return None

        df1 = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        df5 = pd.DataFrame(k5, columns=["t","o","h","l","c","v"])

        # Trend
        df5["ema50"] = ta.trend.EMAIndicator(df5["c"], 50).ema_indicator()
        df5["ema200"] = ta.trend.EMAIndicator(df5["c"], 200).ema_indicator()
        trend = df5.iloc[-1]["ema50"] > df5.iloc[-1]["ema200"]

        # RSI
        df1["rsi"] = ta.momentum.RSIIndicator(df1["c"]).rsi()
        momentum = df1.iloc[-1]["rsi"] > 52

        # Volume
        vol_now = df1.iloc[-1]["v"]
        vol_avg = df1["v"].rolling(20).mean().iloc[-2]
        volume_spike = vol_now > vol_avg * 1.7

        # Breakout
        price = df1.iloc[-1]["c"]
        breakout = price > df1["h"].rolling(15).max().iloc[-2]

        # ATR
        df1["atr"] = ta.volatility.AverageTrueRange(
            df1["h"], df1["l"], df1["c"]
        ).average_true_range()

        atr = df1.iloc[-1]["atr"]

        # Score
        score = 0
        score += 30 if trend else 0
        score += 25 if breakout else 0
        score += 25 if volume_spike else 0
        score += 20 if momentum else 0

        if score < 70:
            return None

        return {
            "symbol": symbol.upper(),
            "entry": price,
            "tp1": price + atr * 1.5,
            "tp2": price + atr * 3,
            "sl": price - atr * 1.2,
            "score": score
        }

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return None

# ================= WEBSOCKET =================
async def ws_loop(bot):
    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=30
            ) as ws:

                print("🔥 WS CONNECTED")

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        "kline.1.BTCUSDT","kline.5.BTCUSDT",
                        "kline.1.ETHUSDT","kline.5.ETHUSDT",
                        "kline.1.SOLUSDT","kline.5.SOLUSDT"
                    ]
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    topic = data.get("topic", "")
                    tf = "1" if ".1." in topic else "5"

                    for k in data["data"]:
                        symbol = k.get("symbol")
                        if not symbol:
                            continue

                        symbol = symbol.lower()

                        klines[f"{symbol}_{tf}"].append([
                            k.get("start"),
                            float(k.get("open", 0)),
                            float(k.get("high", 0)),
                            float(k.get("low", 0)),
                            float(k.get("close", 0)),
                            float(k.get("volume", 0)),
                        ])

                        # SIGNAL
                        if tf == "1":
                            res = analyze(symbol)
                            if res:
                                now = time.time()

                                if symbol in last_signal_time:
                                    if now - last_signal_time[symbol] < 300:
                                        continue

                                last_signal_time[symbol] = now

                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=(
                                        f"🚀 {res['symbol']}\n\n"
                                        f"💰 Entry: {res['entry']:.4f}\n"
                                        f"🎯 TP1: {res['tp1']:.4f}\n"
                                        f"🎯 TP2: {res['tp2']:.4f}\n"
                                        f"🛑 SL: {res['sl']:.4f}\n\n"
                                        f"🧠 Score: {res['score']}%"
                                    )
                                )

                                open_trades[res["symbol"]] = res

                        # TP1 CHECK
                        trade = open_trades.get(symbol.upper())
                        if trade:
                            price_now = float(k.get("close", 0))

                            if price_now >= trade["tp1"]:
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=f"📈 {symbol.upper()} hit TP1 — move SL to entry"
                                )
                                del open_trades[symbol.upper()]

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= MAIN =================
async def main():
    await start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()
    await app.initialize()
    await app.start()

    print("🔥 BOT RUNNING")

    asyncio.create_task(ws_loop(app.bot))

    await asyncio.Event().wait()

# ================= RUN =================
if __name__ == "__main__":
    asyncio.run(main())
