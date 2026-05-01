import os
import asyncio
import json
import time
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
from telegram.ext import Application

print("🚨 BOT FILE LOADED")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

klines = defaultdict(lambda: deque(maxlen=200))
last_sent = {}
open_trades = {}

# ================= ANALYSIS =================
def analyze(symbol):
    try:
        k1 = list(klines[f"{symbol}_1"])
        k5 = list(klines[f"{symbol}_5"])

        if len(k1) < 80 or len(k5) < 80:
            return None

        df1 = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        df5 = pd.DataFrame(k5, columns=["t","o","h","l","c","v"])

        # سيولة
        vol_usd = (df1["c"] * df1["v"]).rolling(30).mean().iloc[-1]
        if vol_usd < 1_000_000:
            return None

        # ترند
        df5["ema50"] = ta.trend.EMAIndicator(df5["c"], 50).ema_indicator()
        df5["ema200"] = ta.trend.EMAIndicator(df5["c"], 200).ema_indicator()
        trend = df5.iloc[-1]["ema50"] > df5.iloc[-1]["ema200"]

        # زخم
        df1["rsi"] = ta.momentum.RSIIndicator(df1["c"]).rsi()
        df1["atr"] = ta.volatility.AverageTrueRange(df1["h"], df1["l"], df1["c"]).average_true_range()

        r = df1.iloc[-1]
        prev = df1.iloc[-2]
        price = r["c"]

        breakout = price > df1["h"].rolling(20).max().iloc[-2]
        vol_spike = r["v"] > df1["v"].rolling(20).mean().iloc[-2] * 1.8
        momentum = r["rsi"] > 52 and price > prev["c"]

        score = 0
        score += 30 if trend else 0
        score += 30 if breakout else 0
        score += 20 if vol_spike else 0
        score += 20 if momentum else 0

        if score < 75:
            return None

        atr = r["atr"]

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


# ================= MESSAGE =================
def build_msg(s):
    return f"""
━━━━━━━━━━━━━━━
📊 {s['symbol']}

🔥 Smart Signal

💰 Entry: {s['entry']:.4f}

🎯 TP1: {s['tp1']:.4f}
🎯 TP2: {s['tp2']:.4f}

🛑 SL: {s['sl']:.4f}

📌 عند TP1:
- اقفل 50%
- حرّك الوقف لنقطة الدخول

🧠 Score: {s['score']}%
━━━━━━━━━━━━━━━
"""


# ================= SEND =================
async def send_signal(app, s):
    try:
        now = time.time()

        if s["symbol"] in last_sent:
            if now - last_sent[s["symbol"]] < 3600:
                return

        await app.bot.send_message(chat_id=CHAT_ID, text=build_msg(s))

        last_sent[s["symbol"]] = now
        open_trades[s["symbol"]] = {"tp1": s["tp1"]}

    except Exception as e:
        print("SEND ERROR:", e)


# ================= WS =================
async def ws_loop(app):
    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
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
                            result = analyze(symbol)
                            if result:
                                await send_signal(app, result)

                            trade = open_trades.get(symbol.upper())
                            if trade:
                                price = float(k.get("close", 0))
                                if price >= trade["tp1"]:
                                    await app.bot.send_message(
                                        chat_id=CHAT_ID,
                                        text=f"📈 {symbol.upper()} وصل TP1 — حرّك الوقف لنقطة الدخول"
                                    )
                                    del open_trades[symbol.upper()]

        except asyncio.CancelledError:
            print("🛑 WS STOPPED")
            return

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)


# ================= MAIN =================
def main():
    print("🚀 STARTING BOT...")

    app = Application.builder().token(BOT_TOKEN).build()

    async def on_startup(app):
        print("🔥 BOT STARTED")
        app.ws_task = asyncio.create_task(ws_loop(app))

    async def on_shutdown(app):
        print("🛑 SHUTDOWN...")
        if hasattr(app, "ws_task"):
            app.ws_task.cancel()
            try:
                await app.ws_task
            except:
                pass

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    print("⚡ RUNNING POLLING...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
