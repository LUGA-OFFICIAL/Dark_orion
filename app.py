import os
import asyncio
import json
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
from telegram.ext import Application

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

klines = defaultdict(lambda: deque(maxlen=200))

# ================= ANALYSIS =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        k5 = list(klines[f"{symbol}_5"])

        if len(k1) < 80 or len(k5) < 80:
            return None

        df1 = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        df5 = pd.DataFrame(k5, columns=["t","o","h","l","c","v"])

        df5["ema50"] = ta.trend.EMAIndicator(df5["c"], 50).ema_indicator()
        df5["ema200"] = ta.trend.EMAIndicator(df5["c"], 200).ema_indicator()

        trend = df5.iloc[-1]["ema50"] > df5.iloc[-1]["ema200"]

        r = df1.iloc[-1]
        price = r["c"]

        breakout = price > df1["h"].rolling(20).max().iloc[-2]

        if not (trend and breakout):
            return None

        return {
            "symbol": symbol.upper(),
            "entry": price
        }

    except:
        return None

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
                        "kline.1.BTCUSDT","kline.5.BTCUSDT",
                        "kline.1.ETHUSDT","kline.5.ETHUSDT"
                    ]
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    topic = data.get("topic", "")
                    tf = "1" if ".1." in topic else "5"

                    for k in data["data"]:
                        symbol = k.get("symbol").lower()
                        key = f"{symbol}_{tf}"

                        klines[key].append([
                            k.get("start"),
                            float(k.get("open", 0)),
                            float(k.get("high", 0)),
                            float(k.get("low", 0)),
                            float(k.get("close", 0)),
                            float(k.get("volume", 0))
                        ])

                        if tf == "1":
                            res = analyze(symbol)
                            if res:
                                await app.bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=f"🚀 {res['symbol']} BUY @ {res['entry']:.4f}"
                                )

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    async def startup(app):
        print("🔥 BOT STARTED")
        asyncio.create_task(ws_loop(app))

    app.post_init = startup

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
