import os
import asyncio
import json
import time
from collections import defaultdict, deque

import pandas as pd
import ta
import websockets
from aiohttp import web
from telegram.ext import Application

print("🔥 FAST BEAST MODE")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

# تقليل الانتظار بين الإشارات
COOLDOWN = 900

# العملات
SYMBOLS = [
    "btcusdt",
    "ethusdt",
    "solusdt",
    "xrpusdt",
    "dogeusdt",
    "adausdt",
    "bnbusdt",
    "avaxusdt",
    "linkusdt",
    "maticusdt",
    "arbusdt",
    "opusdt",
    "injusdt",
    "aptusdt",
    "suiusdt",
    "seiusdt",
    "nearusdt",
    "atomusdt",
    "ltcusdt",
    "filusdt",
    "dotusdt",
    "uniusdt",
    "aaveusdt",
    "algousdt",
    "hbarusdt"
]

klines = defaultdict(lambda: deque(maxlen=300))
last_signal = {}
open_trades = {}

# ================= HEALTH =================
async def health(request):
    return web.Response(text="OK")

async def start_health():

    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(f"✅ Health server on {PORT}")

# ================= FORMAT =================
def fmt(x):

    if x >= 1000:
        return f"{x:,.2f}"

    if x >= 1:
        return f"{x:.4f}"

    return f"{x:.8f}"

# ================= ANALYZE =================
def analyze(symbol):

    try:

        data = list(klines[symbol])

        # يبدأ بسرعة
        if len(data) < 10:
            return None

        df = pd.DataFrame(
            data,
            columns=["t","o","h","l","c","v"]
        )

        price = df["c"].iloc[-1]

        # ================= INDICATORS =================
        df["ema9"] = ta.trend.EMAIndicator(
            df["c"],
            9
        ).ema_indicator()

        df["ema21"] = ta.trend.EMAIndicator(
            df["c"],
            21
        ).ema_indicator()

        rsi = ta.momentum.RSIIndicator(
            df["c"],
            14
        ).rsi().iloc[-1]

        vol_now = df["v"].iloc[-1]

        vol_avg = (
            df["v"]
            .rolling(10)
            .mean()
            .iloc[-1]
        )

        recent_move = (
            df["c"]
            .pct_change()
            .tail(3)
            .sum()
        )

        score = 0
        sniper = False

        # ================= TREND =================
        if df["ema9"].iloc[-1] > df["ema21"].iloc[-1]:
            score += 20
        else:
            score += 10

        # ================= RSI =================
        if 35 <= rsi <= 70:
            score += 20

        if rsi < 40:
            score += 10

        # ================= VOLUME =================
        if vol_now > vol_avg * 0.8:
            score += 20

        if vol_now > vol_avg * 1.5:
            score += 25
            sniper = True

        # ================= MOMENTUM =================
        if recent_move > -0.002:
            score += 20

        if recent_move > 0.015:
            score += 25
            sniper = True

        # ================= CANDLE =================
        candle = (
            df["c"].iloc[-1]
            - df["o"].iloc[-1]
        )

        if candle > 0:
            score += 15

        # ================= GRADES =================
        if sniper and score >= 85:
            grade = "🐋 SNIPER"

        elif score >= 70:
            grade = "🔥 HIGH"

        elif score >= 50:
            grade = "⚡ MEDIUM"

        elif score >= 25:
            grade = "🎯 SCALP"

        else:
            return None

        # ================= ATR =================
        atr = ta.volatility.AverageTrueRange(
            df["h"],
            df["l"],
            df["c"]
        ).average_true_range().iloc[-1]

        tp1 = price + atr * 1.0
        tp2 = price + atr * 1.8
        sl = price - atr * 0.8

        return {
            "symbol": symbol.upper(),
            "grade": grade,
            "price": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "score": score,
            "rsi": round(rsi, 1),
            "tp1_hit": False
        }

    except Exception as e:

        print("ANALYZE ERROR:", e)

        return None

# ================= SIGNAL =================
def signal_message(r):

    return (
        f"{r['grade']} SIGNAL\n\n"
        f"🪙 {r['symbol']}\n\n"
        f"💰 Entry: {fmt(r['price'])}\n"
        f"🎯 TP1: {fmt(r['tp1'])}\n"
        f"🎯 TP2: {fmt(r['tp2'])}\n"
        f"🛑 SL: {fmt(r['sl'])}\n\n"
        f"📊 Score: {r['score']}%\n"
        f"📈 RSI: {r['rsi']}"
    )

# ================= TP/SL =================
async def check_trade(bot, symbol, price):

    if symbol not in open_trades:
        return

    trade = open_trades[symbol]

    # TP1
    if (
        not trade["tp1_hit"]
        and price >= trade["tp1"]
    ):

        trade["tp1_hit"] = True

        trade["sl"] = trade["price"]

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🎯 TP1 HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}\n\n"
                f"🛡 SL moved to entry"
            )
        )

    # TP2
    if price >= trade["tp2"]:

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🚀 TP2 HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}\n\n"
                f"✅ Trade completed"
            )
        )

        del open_trades[symbol]

    # SL
    elif price <= trade["sl"]:

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🛑 STOP LOSS HIT\n\n"
                f"{symbol}\n"
                f"Price: {fmt(price)}"
            )
        )

        del open_trades[symbol]

# ================= WEBSOCKET =================
async def ws_loop(bot):

    streams = "/".join(
        [f"{s}@kline_1m" for s in SYMBOLS]
    )

    url = (
        "wss://stream.binance.com:9443/stream?streams="
        + streams
    )

    while True:

        try:

            async with websockets.connect(
                url,
                ping_interval=20
            ) as ws:

                print("🔥 BINANCE CONNECTED")

                await bot.send_message(
                    chat_id=CHAT_ID,
                    text="🔥 Binance Market Connected"
                )

                async for raw in ws:

                    data = json.loads(raw)

                    if "data" not in data:
                        continue

                    k = data["data"]["k"]

                    symbol = k["s"].lower()

                    close_price = float(k["c"])

                    klines[symbol].append([
                        k["t"],
                        float(k["o"]),
                        float(k["h"]),
                        float(k["l"]),
                        close_price,
                        float(k["v"])
                    ])

                    # ================= CHECK TRADES =================
                    await check_trade(
                        bot,
                        symbol.upper(),
                        close_price
                    )

                    # ================= ANALYZE =================
                    res = analyze(symbol)

                    if not res:
                        continue

                    now = time.time()

                    if symbol in last_signal:

                        if (
                            now - last_signal[symbol]
                            < COOLDOWN
                        ):
                            continue

                    last_signal[symbol] = now

                    open_trades[
                        symbol.upper()
                    ] = res

                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=signal_message(res)
                    )

        except Exception as e:

            print("WS ERROR:", e)

            await asyncio.sleep(5)

# ================= MAIN =================
async def main():

    await start_health()

    app = Application.builder().token(BOT_TOKEN).build()

    await app.initialize()
    await app.start()

    print("✅ BOT RUNNING")

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text="🔥 Fast Beast Mode Activated"
    )

    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
