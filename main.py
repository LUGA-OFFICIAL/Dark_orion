print("🔥 BEAST MODE FINAL STARTING...")

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
PORT      = int(os.getenv("PORT", "8080"))

# ================= STORAGE =================
klines = defaultdict(lambda: deque(maxlen=300))
scoreboard = defaultdict(float)
last_signal_time = {}

ALL_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","MATICUSDT","ARBUSDT","OPUSDT",
    "INJUSDT","SUIUSDT","SEIUSDT","APTUSDT","FTMUSDT","NEARUSDT",
    "ATOMUSDT","TRXUSDT","LTCUSDT","ETCUSDT","FILUSDT","ICPUSDT"
]

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

# ================= TEST =================
async def send_test(bot):
    await bot.send_message(chat_id=CHAT_ID, text="🔥 BEAST MODE ACTIVE")

# ================= SMART SELECTION =================
def select_top_symbols(limit=25):
    sorted_symbols = sorted(
        ALL_SYMBOLS,
        key=lambda s: scoreboard[s],
        reverse=True
    )
    selected = sorted_symbols[:limit]
    print("🧠 SELECTED:", selected)
    return [f"kline.1.{s}" for s in selected]

# ================= ANALYSIS =================
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
        volume_spike = vol_now > vol_avg * 1.2

        recent = df["c"].pct_change().tail(3).sum()
        pump_early = recent > 0.01

        recent_high = df["h"].rolling(20).max().iloc[-2]
        sweep = df.iloc[-1]["h"] > recent_high and price < recent_high

        # منع الشموع الغبية
        candle = df.iloc[-1]
        size = candle["h"] - candle["l"]
        avg = (df["h"] - df["l"]).rolling(20).mean().iloc[-2]
        if size > avg * 2.5:
            return None

        score = 0
        if volume_spike: score += 25
        if pump_early: score += 25
        if sweep: score += 25
        if price > df.iloc[-1]["ema50"]: score += 25

        # ================= GRADE =================
        if score >= 70:
            grade = "🔥 HIGH QUALITY"
        elif score >= 50:
            grade = "⚡ MEDIUM"
        elif score >= 35:
            grade = "🎯 SCALP"
        else:
            return None

        # تحديث الأداء
        scoreboard[symbol.upper()] += score * 0.1

        atr = ta.volatility.AverageTrueRange(
            df["h"], df["l"], df["c"]
        ).average_true_range().iloc[-1]

        if sweep:
            sig_type = "🐋 Smart Money"
        elif pump_early:
            sig_type = "🔥 Early Pump"
        else:
            sig_type = "⚡ Momentum"

        return {
            "symbol": symbol.upper(),
            "entry": price,
            "tp1": price + atr * (1.5 if score >= 70 else 1.0),
            "tp2": price + atr * (2.5 if score >= 70 else 1.5),
            "sl": price - atr,
            "score": score,
            "type": sig_type,
            "grade": grade
        }

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return None

# ================= MESSAGE =================
def format_msg(r):
    if "HIGH" in r["grade"]:
        explanation = "💎 فرصة قوية — توافق مؤشرات + سيولة"
    elif "MEDIUM" in r["grade"]:
        explanation = "⚡ فرصة متوسطة — زخم جيد"
    else:
        explanation = "🎯 سكالب سريع — مخاطرة أعلى"

    return (
        f"{r['grade']} | {r['type']}\n\n"
        f"{explanation}\n\n"
        f"💰 Entry: {r['entry']:.4f}\n"
        f"🎯 TP1: {r['tp1']:.4f}\n"
        f"🎯 TP2: {r['tp2']:.4f}\n"
        f"🛑 SL: {r['sl']:.4f}\n\n"
        f"🧠 Confidence: {r['score']}%"
    )

# ================= WS =================
async def ws_loop(bot):
    global active_symbols
    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print("🔥 WS CONNECTED")

                active_symbols = select_top_symbols()

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": active_symbols
                }))

                last_update = time.time()

                async for msg in ws:
                    data = json.loads(msg)

                    # 🔄 تغيير العملات كل 10 دقائق
                    if time.time() - last_update > 600:
                        new = select_top_symbols()

                        await ws.send(json.dumps({
                            "op": "unsubscribe",
                            "args": active_symbols
                        }))

                        await ws.send(json.dumps({
                            "op": "subscribe",
                            "args": new
                        }))

                        active_symbols = new
                        last_update = time.time()
                        print("🔄 SYMBOL ROTATION")

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

                        if res:
                            now = time.time()
                            if s in last_signal_time and now - last_signal_time[s] < 90:
                                continue

                            last_signal_time[s] = now

                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=format_msg(res)
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

    await send_test(app.bot)

    asyncio.create_task(ws_loop(app.bot))

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
