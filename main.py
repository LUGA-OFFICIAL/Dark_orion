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

print("🔥 BINANCE BEAST MODE")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

COOLDOWN = 1800

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
    "filusdt"
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

        if len(data) < 20:
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

        vol_avg = df["v"].rolling(20).mean().iloc[-1]

        recent_move = (
            df["c"].pct_change()
            .tail(3)
            .sum()
        )

        # ================= SCORE =================
        score = 0
        sniper = False

        # TREND
        if df["ema9"].iloc[-1] > df["ema21"].iloc[-1]:
            score += 20
        else:
            score += 5

        # RSI
        if 45 <= rsi <= 65:
            score += 20

        if rsi < 40:
            score += 15

        # VOLUME
        if vol_now > vol_avg:
            score += 20

        if vol_now > vol_avg * 2:
            score += 25
            sniper = True

        # MOMENTUM
        if recent_move > 0:
            score += 20

        if recent_move > 0.015:
            score += 25
            sniper = True

        # CANDLE
        last_candle = (
            df["c"].iloc[-1]
            - df["o"].iloc[-1]
        )

        if last_candle > 0:
            score += 15

        # ================= GRADES =================
        if sniper and score >= 90:
            grade = "🐋 SNIPER"

        elif score >= 75:
            grade = "🔥 HIGH"

        elif score >= 55:
            grade = "⚡ MEDIUM"

        elif score >= 35:
            grade = "🎯 SCALP"

        else:
            return None

        # ================= ATR =================
        atr = ta.volatility.AverageTrueRange(
            df["h"],
            df["l"],
            df["c"]
        ).average_true_range().iloc[-1]

        tp1 = price + atr * 1.2
        tp2 = price + atr * 2.0
        sl = price - atr

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

# ================= SIGNAL MESSAGE =================
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

# ================= TP/SL CHECK =================
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

                    # ================= CHECK TRADE =================
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
        text="🔥 Binance Beast Mode Activated"
    )

    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
