import os
import asyncio
import json
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

# ================= HEALTH CHECK =================
async def health(request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Health server يعمل على port {PORT}")

# ================= ANALYSIS =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        k5 = list(klines[f"{symbol}_5"])

        if len(k1) < 50 or len(k5) < 50:
            return None

        df1 = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        df5 = pd.DataFrame(k5, columns=["t","o","h","l","c","v"])

        df5["ema50"]  = ta.trend.EMAIndicator(df5["c"], 50).ema_indicator()
        df5["ema200"] = ta.trend.EMAIndicator(df5["c"], 200).ema_indicator()

        trend    = df5.iloc[-1]["ema50"] > df5.iloc[-1]["ema200"]
        price    = df1.iloc[-1]["c"]
        breakout = price > df1["h"].rolling(20).max().iloc[-2]

        if not (trend and breakout):
            return None

        return {
            "symbol": symbol.upper(),
            "entry": price
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
                        "kline.1.BTCUSDT", "kline.5.BTCUSDT",
                        "kline.1.ETHUSDT", "kline.5.ETHUSDT",
                        "kline.1.SOLUSDT", "kline.5.SOLUSDT",
                    ]
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    topic = data.get("topic", "")
                    tf    = "1" if ".1." in topic else "5"

                    for k in data["data"]:
                        symbol = k.get("symbol")
                        if not symbol:
                            continue

                        symbol = symbol.lower()

                        klines[f"{symbol}_{tf}"].append([
                            k.get("start"),
                            float(k.get("open",   0)),
                            float(k.get("high",   0)),
                            float(k.get("low",    0)),
                            float(k.get("close",  0)),
                            float(k.get("volume", 0)),
                        ])

                        if tf == "1":
                            res = analyze(symbol)
                            if res:
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=(
                                        f"🚀 *{res['symbol']}* BUY\n"
                                        f"💰 Entry: `{res['entry']:.4f}`"
                                    ),
                                    parse_mode="Markdown"
                                )

        except Exception as e:
            print(f"WS ERROR: {e} — إعادة الاتصال بعد 5 ثواني...")
            await asyncio.sleep(5)

# ================= MAIN =================
async def main():
    # Health server (مهم لـ Railway)
    await start_health_server()

    # Telegram bot
    app = Application.builder().token(BOT_TOKEN).build()
    await app.initialize()
    await app.start()

    print("✅ البوت يعمل!")

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text="✅ البوت يعمل ويراقب السوق!"
    )

    # WebSocket كـ background task
    asyncio.create_task(ws_loop(app.bot))

    # خليه شغال دائمًا
    await asyncio.Event().wait()

# ================= RUN =================
if __name__ == "__main__":
    asyncio.run(main())
