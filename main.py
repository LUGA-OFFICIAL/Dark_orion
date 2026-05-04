print("🔥 BOT STARTING...")

import os
import asyncio
import json
import time
from collections import defaultdict, deque
from aiohttp import web
import aiohttp

import pandas as pd
import ta
import websockets
from telegram.ext import Application

print("🚨 BOT FILE LOADED")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
PORT      = int(os.getenv("PORT", "8080"))

klines = defaultdict(lambda: deque(maxlen=300))
last_signal_time = {}
active_symbols = []

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
    print("✅ Health server running")

# ================= TEST MESSAGE =================
async def send_test(bot):
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🔥 البوت شغال الآن — Test Message"
        )
        print("✅ TEST MESSAGE SENT")
    except Exception as e:
        print("❌ TELEGRAM ERROR:", e)

# ================= NEWS FILTER =================
def news_filter():
    return time.gmtime().tm_min > 5

# ================= GET SYMBOLS =================
async def get_top_symbols(limit=15):
    url = "https://api.bybit.com/v5/market/tickers?category=spot"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                coins = data["result"]["list"]

                coins = sorted(
                    coins,
                    key=lambda x: float(x["turnover24h"]),
                    reverse=True
                )

                symbols = []
                for c in coins:
                    if float(c["turnover24h"]) < 1_000_000:
                        continue
                    if "USDT" not in c["symbol"]:
                        continue

                    symbols.append(f"kline.1.{c['symbol']}")

                    if len(symbols) >= limit:
                        break

                print("🔥 SYMBOLS:", symbols)
                return symbols

    except Exception as e:
        print("SYMBOL ERROR:", e)
        return []

# ================= ANALYZE =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        if len(k1) < 100:
            return None

        df = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        price = df.iloc[-1]["c"]

        df["ema9"] = ta.trend.EMAIndicator(df["c"], 9).ema_indicator()
        df["ema21"] = ta.trend.EMAIndicator(df["c"], 21).ema_indicator()
        df["ema50"] = ta.trend.EMAIndicator(df["c"], 50).ema_indicator()
        df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()

        vol_now = df.iloc[-1]["v"]
        vol_avg = df["v"].rolling(20).mean().iloc[-2]

        volume_spike = vol_now > vol_avg * 2
        trend = price > df.iloc[-1]["ema50"]
        momentum = 50 < df.iloc[-1]["rsi"] < 70

        recent_high = df["h"].rolling(20).max().iloc[-2]
        breakout = price > recent_high * 0.998

        # فلتر Pump
        change = abs(price - df.iloc[-2]["c"]) / df.iloc[-2]["c"]
        if change > 0.05:
            return None

        score = 0
        score += 25 if volume_spike else 0
        score += 25 if trend else 0
        score += 25 if momentum else 0
        score += 25 if breakout else 0

        if score < 60:
            return None

        atr = ta.volatility.AverageTrueRange(
            df["h"], df["l"], df["c"]
        ).average_true_range().iloc[-1]

        return {
            "symbol": symbol.upper(),
            "entry": price,
            "tp1": price + atr * 1.2,
            "tp2": price + atr * 2.5,
            "sl": price - atr * 1.0,
            "score": score
        }

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return None

# ================= MESSAGE =================
def format_message(res):
    return (
        f"⚡ SIGNAL {res['symbol']}\n\n"
        f"💰 Entry: {res['entry']:.4f}\n"
        f"🎯 TP1: {res['tp1']:.4f}\n"
        f"🎯 TP2: {res['tp2']:.4f}\n"
        f"🛑 SL: {res['sl']:.4f}\n\n"
        f"🧠 Confidence: {res['score']}%"
    )

# ================= WS =================
async def ws_loop(bot):
    global active_symbols

    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(url) as ws:
                print("🔥 WS CONNECTED")

                active_symbols = await get_top_symbols()

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": active_symbols
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    for k in data["data"]:
                        symbol = k.get("symbol")
                        if not symbol:
                            continue

                        s = symbol.lower()

                        klines[f"{s}_1"].append([
                            k.get("start"),
                            float(k.get("open",0)),
                            float(k.get("high",0)),
                            float(k.get("low",0)),
                            float(k.get("close",0)),
                            float(k.get("volume",0)),
                        ])

                        res = analyze(s)
                        if res and news_filter():

                            now = time.time()
                            if s in last_signal_time and now - last_signal_time[s] < 180:
                                continue

                            last_signal_time[s] = now

                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=format_message(res)
                            )

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

    # ✅ رسالة تأكيد التشغيل
    await send_test(app.bot)

    asyncio.create_task(ws_loop(app.bot))

    await asyncio.Event().wait()

# ================= RUN =================
if __name__ == "__main__":
    asyncio.run(main())
