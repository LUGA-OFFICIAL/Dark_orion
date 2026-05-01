import os
import asyncio
import json
import time
import math
import requests
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
import ccxt

from telegram.ext import Application

print("🚨 BOT IS RUNNING NOW")
print("🔥 FILE STARTED")

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

APP_URL = "https://worker-production-5ac8.up.railway.app"
PORT = int(os.getenv("PORT", 8080))

exchange = ccxt.bybit({"enableRateLimit": True})

klines = defaultdict(lambda: deque(maxlen=120))
last_sent = {}

# ================= AI =================
def ai_score(f):
    score = sum(f.values())
    return int((1 / (1 + math.exp(-score))) * 100)

# ================= ANALYSIS =================
def analyze(symbol):
    data = list(klines[symbol])
    if len(data) < 50:
        return None

    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["macd_s"] = macd.macd_signal()

    df["ema50"] = ta.trend.EMAIndicator(df["c"], 50).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["c"], 200).ema_indicator()

    r = df.iloc[-1]
    prev = df.iloc[-2]

    price = r["c"]
    change = (price - prev["c"]) / prev["c"] * 100

    features = {
        "rsi": 1 if r["rsi"] < 40 else -1,
        "macd": 1 if r["macd"] > r["macd_s"] else -1,
        "trend": 1 if r["ema50"] > r["ema200"] else -1,
        "momentum": 1 if change > 0 else -1
    }

    ai = ai_score(features)

    if ai < 70:
        return None

    return {
        "symbol": symbol.upper(),
        "entry": price,
        "tp": price * 1.02,
        "sl": price * 0.97,
        "ai": ai
    }

# ================= MESSAGE =================
def build_msg(s):
    return f"""
━━━━━━━━━━━━━━━
📊 {s['symbol']}

🚀 توصية شراء

💰 دخول: {s['entry']:.4f}
🎯 هدف: {s['tp']:.4f}
🛑 وقف: {s['sl']:.4f}

🧠 قوة: {s['ai']}%
━━━━━━━━━━━━━━━
"""

# ================= SEND =================
async def send_signal(app, s):
    now = time.time()

    if s["symbol"] in last_sent:
        if now - last_sent[s["symbol"]] < 1800:
            return

    await app.bot.send_message(chat_id=CHAT_ID, text=build_msg(s))
    last_sent[s["symbol"]] = now

# ================= WS =================
async def ws_loop(app):
    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20
            ) as ws:

                print("🔥 WS CONNECTED")

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        "kline.1.BTCUSDT",
                        "kline.1.ETHUSDT",
                        "kline.1.SOLUSDT"
                    ]
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    for k in data.get("data", []):
                        try:
                            symbol = k.get("symbol")
                            if not symbol:
                                continue

                            symbol = symbol.lower()

                            klines[symbol].append([
                                k.get("start"),
                                float(k.get("open", 0)),
                                float(k.get("high", 0)),
                                float(k.get("low", 0)),
                                float(k.get("close", 0)),
                                float(k.get("volume", 0))
                            ])

                            result = analyze(symbol)
                            if result:
                                await send_signal(app, result)

                        except Exception as e:
                            print("PARSE ERROR:", e)

        except asyncio.CancelledError:
            print("🛑 WS STOPPED")
            break

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= MAIN =================
def main():
    print("🚀 STARTING BOT...")

    # حذف webhook قديم
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")

    app = Application.builder().token(BOT_TOKEN).build()

    app.ws_task = None

    async def on_startup(app):
        print("🔥 BOT STARTED")
        app.ws_task = asyncio.create_task(ws_loop(app))

    async def on_shutdown(app):
        print("🛑 SHUTDOWN...")
        if app.ws_task:
            app.ws_task.cancel()
            try:
                await app.ws_task
            except:
                pass

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    print("⚡ RUNNING WEBHOOK...")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
