import os
import asyncio
import json
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
from telegram.ext import Application

print("🚨 BOT FILE LOADED")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))
APP_URL = os.getenv("APP_URL")  # https://your-app.up.railway.app

klines = defaultdict(lambda: deque(maxlen=200))


# ================= ANALYSIS =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        k5 = list(klines[f"{symbol}_5"])

        if len(k1) < 50 or len(k5) < 50:
            return None

        df1 = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        df5 = pd.DataFrame(k5, columns=["t","o","h","l","c","v"])

        df5["ema50"] = ta.trend.EMAIndicator(df5["c"], 50).ema_indicator()
        df5["ema200"] = ta.trend.EMAIndicator(df5["c"], 200).ema_indicator()

        trend = df5.iloc[-1]["ema50"] > df5.iloc[-1]["ema200"]
        price = df1.iloc[-1]["c"]
        breakout = price > df1["h"].rolling(20).max().iloc[-2]

        if not (trend and breakout):
            return None

        return {"symbol": symbol.upper(), "entry": price}

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return None


# ================= WS =================
async def ws_loop(app, stop_event):
    url = "wss://stream.bybit.com/v5/public/spot"

    while not stop_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print("🔥 WS CONNECTED")

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        "kline.1.BTCUSDT","kline.5.BTCUSDT",
                        "kline.1.ETHUSDT","kline.5.ETHUSDT"
                    ]
                }))

                async for msg in ws:
                    if stop_event.is_set():
                        break

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

        except asyncio.CancelledError:
            print("🛑 WS STOPPED")
            return
        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)


# ================= MAIN =================
def main():
    if not APP_URL:
        raise RuntimeError("APP_URL env is required")

    app = Application.builder().token(BOT_TOKEN).build()
    stop_event = asyncio.Event()

    async def on_startup(app):
        print("🔥 BOT STARTED")
        app.ws_task = asyncio.create_task(ws_loop(app, stop_event))

    async def on_shutdown(app):
        print("🛑 SHUTDOWN...")
        stop_event.set()
        if hasattr(app, "ws_task"):
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
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
