print("🚨 BOT IS RUNNING NOW")
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
            async with websockets.connect(url) as ws:
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

                    for k in data["data"]:
                        symbol = k["symbol"].lower()

                        klines[symbol].append([
                            k["start"],
                            float(k["open"]),
                            float(k["high"]),
                            float(k["low"]),
                            float(k["close"]),
                            float(k["volume"])
                        ])

                        result = analyze(symbol)
                        if result:
                            await send_signal(app, result)

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= MAIN =================
def main():
    print("🚀 STARTING BOT...")

    # تنظيف أي webhook قديم
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")

    app = Application.builder().token(BOT_TOKEN).build()

    async def start(app):
        print("🔥 BOT STARTED")
        asyncio.create_task(ws_loop(app))

    app.post_init = start

    print("⚡ RUNNING WEBHOOK...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
