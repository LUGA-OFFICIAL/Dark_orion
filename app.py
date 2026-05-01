import os
import asyncio
import json
import time
import math
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets

from telegram.ext import Application

print("🚨 BOT IS RUNNING NOW")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

klines = defaultdict(lambda: deque(maxlen=120))
last_sent = {}

# ===== تحليل بسيط =====
def ai_score(f):
    score = sum(f.values())
    return int((1 / (1 + math.exp(-score))) * 100)

def analyze(symbol):
    data = list(klines[symbol])
    if len(data) < 50:
        return None

    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["macd_s"] = macd.macd_signal()

    r = df.iloc[-1]
    prev = df.iloc[-2]

    price = r["c"]
    change = (price - prev["c"]) / prev["c"] * 100

    features = {
        "rsi": 1 if r["rsi"] < 40 else -1,
        "macd": 1 if r["macd"] > r["macd_s"] else -1,
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

def build_msg(s):
    return f"""
📊 {s['symbol']}
🚀 شراء

💰 دخول: {s['entry']:.4f}
🎯 هدف: {s['tp']:.4f}
🛑 وقف: {s['sl']:.4f}

🧠 قوة: {s['ai']}%
"""

async def send_signal(app, s):
    now = time.time()
    if s["symbol"] in last_sent and now - last_sent[s["symbol"]] < 1800:
        return
    await app.bot.send_message(chat_id=CHAT_ID, text=build_msg(s))
    last_sent[s["symbol"]] = now

# ===== WebSocket =====
async def ws_loop(app):
    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print("🔥 WS CONNECTED")

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        "kline.1.BTCUSDT",
                        "kline.1.ETHUSDT"
                    ]
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    for k in data["data"]:
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
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ===== MAIN =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    async def start(app):
        print("🔥 BOT STARTED")
        asyncio.create_task(ws_loop(app))

    app.post_init = start

    print("⚡ RUNNING POLLING...")
    app.run_polling()

if __name__ == "__main__":
    main()
