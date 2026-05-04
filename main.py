print("🔥 BEAST MODE BOT STARTING...")

import os
import asyncio
import json
import time
from collections import defaultdict, deque
from aiohttp import web
import aiohttp

import pandas as pd
import ta
import websockets
from telegram.ext import Application

print("🚨 BOT FILE LOADED")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
PORT      = int(os.getenv("PORT", "8080"))

# ================= STORAGE =================
klines = defaultdict(lambda: deque(maxlen=300))
last_signal_time = {}   # per symbol cooldown
active_symbols = []     # current subscribed list

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
    print("✅ Health server running on port", PORT)

# ================= NEWS / NOISE FILTER =================
def news_filter():
    # بسيط: تجنب أول 5 دقائق من كل ساعة (تقلبات/سيولة مضللة)
    minute = time.gmtime().tm_min
    return minute > 5

# ================= SYMBOL DISCOVERY =================
async def get_top_symbols(limit=20, min_turnover=1_000_000):
    """
    يجيب أفضل العملات حسب turnover24h ويفلتر الضعيف.
    """
    url = "https://api.bybit.com/v5/market/tickers?category=spot"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                data = await resp.json()
                coins = data.get("result", {}).get("list", [])

                # فلترة سيولة
                filtered = []
                for c in coins:
                    try:
                        if float(c.get("turnover24h", 0)) >= min_turnover:
                            filtered.append(c)
                    except:
                        continue

                # ترتيب حسب السيولة
                filtered = sorted(
                    filtered,
                    key=lambda x: float(x.get("turnover24h", 0)),
                    reverse=True
                )

                # خذ أفضل N
                top = filtered[:limit]

                symbols = [f"kline.1.{c['symbol']}" for c in top]
                print(f"🔥 Selected {len(symbols)} symbols")
                return symbols
    except Exception as e:
        print("SYMBOL FETCH ERROR:", e)
        return []

# ================= ANALYSIS CORE =================
def analyze(symbol):
    """
    Multi-type:
    - ⚡ Fast (EMA9>EMA21 + RSI + Volume)
    - 🚀 Breakout (range break)
    - 🐋 Smart Money (simple liquidity sweep)
    Returns dict or None
    """
    try:
        k1 = list(klines[f"{symbol}_1"])
        if len(k1) < 120:
            return None

        df = pd.DataFrame(k1, columns=["t","o","h","l","c","v"])
        price = df.iloc[-1]["c"]

        # ===== Indicators =====
        df["ema9"]  = ta.trend.EMAIndicator(df["c"], 9).ema_indicator()
        df["ema21"] = ta.trend.EMAIndicator(df["c"], 21).ema_indicator()
        df["ema50"] = ta.trend.EMAIndicator(df["c"], 50).ema_indicator()
        df["rsi"]   = ta.momentum.RSIIndicator(df["c"], 14).rsi()

        # ===== Volume =====
        vol_now = df.iloc[-1]["v"]
        vol_avg = df["v"].rolling(20).mean().iloc[-2]
        volume_spike = vol_now > vol_avg * 2

        # ===== Candle sanity (تجنب شموع مبالغ فيها) =====
        candle = df.iloc[-1]
        candle_size = candle["h"] - candle["l"]
        avg_size = (df["h"] - df["l"]).rolling(20).mean().iloc[-2]
        not_extreme = candle_size < avg_size * 2.5

        # ===== ATR =====
        atr = ta.volatility.AverageTrueRange(
            df["h"], df["l"], df["c"]
        ).average_true_range().iloc[-1]

        # ================= TYPES =================

        # ⚡ FAST (سكالب سريع)
        fast = (
            df.iloc[-1]["ema9"] > df.iloc[-1]["ema21"] and
            50 < df.iloc[-1]["rsi"] < 70 and
            volume_spike and
            not_extreme
        )

        # 🚀 BREAKOUT
        recent_high = df["h"].rolling(20).max().iloc[-2]
        breakout = price > recent_high and volume_spike

        # 🐋 SMART MONEY (liquidity sweep بسيط)
        swing_high = recent_high
        sweep_up = df.iloc[-1]["h"] > swing_high and price < swing_high
        smart = sweep_up

        # ================= SCORE =================
        score = 0
        if fast: score += 25
        if smart: score += 30
        if breakout: score += 25
        if volume_spike: score += 20

        if score < 60:
            return None

        # ================= TYPE =================
        if smart:
            signal_type = "🐋 Smart Money"
        elif breakout:
            signal_type = "🚀 Breakout"
        elif fast:
            signal_type = "⚡ Fast Trade"
        else:
            signal_type = "📊 Standard"

        # ================= TARGETS =================
        tp1 = price + atr * 1.2
        tp2 = price + atr * 2.4
        sl  = price - atr * 1.0

        # 🆕 نشاط غير عادي (تقريب)
        new_active = vol_avg < 1000 and vol_now > vol_avg * 3

        return {
            "symbol": symbol.upper(),
            "entry": price,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "score": score,
            "type": signal_type,
            "new": new_active
        }

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return None

# ================= MESSAGE =================
def format_message(res):
    if "Fast" in res["type"]:
        explanation = "⚡ صفقة سريعة: زخم عالي + دخول مبكر"
    elif "Smart" in res["type"]:
        explanation = "🐋 حركة حيتان: سحب سيولة + انعكاس محتمل"
    elif "Breakout" in res["type"]:
        explanation = "🚀 اختراق قوي: كسر مقاومة مع حجم"
    else:
        explanation = "📊 فرصة قياسية"

    if res["new"]:
        explanation += "\n🆕 نشاط غير طبيعي: مخاطرة أعلى"

    return (
        f"{res['type']} SIGNAL\n\n"
        f"{explanation}\n\n"
        f"💰 Entry: {res['entry']:.4f}\n"
        f"🎯 TP1: {res['tp1']:.4f}\n"
        f"🎯 TP2: {res['tp2']:.4f}\n"
        f"🛑 SL: {res['sl']:.4f}\n\n"
        f"🧠 Confidence: {res['score']}%"
    )

# ================= WEBSOCKET LOOP =================
async def ws_loop(bot):
    global active_symbols

    url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                print("🔥 WS CONNECTED")

                # أول تحميل للرموز
                active_symbols = await get_top_symbols(limit=20)
                if not active_symbols:
                    await asyncio.sleep(5)
                    continue

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": active_symbols
                }))

                last_update = time.time()

                async for msg in ws:
                    data = json.loads(msg)

                    # تحديث اللائحة كل 15 دقيقة
                    if time.time() - last_update > 900:
                        new_symbols = await get_top_symbols(limit=20)
                        if new_symbols:
                            # إعادة الاشتراك (بسيط)
                            await ws.send(json.dumps({
                                "op": "subscribe",
                                "args": new_symbols
                            }))
                            active_symbols = new_symbols
                            print("🔄 Symbols updated")
                        last_update = time.time()

                    if "data" not in data:
                        continue

                    for k in data["data"]:
                        symbol = k.get("symbol")
                        if not symbol:
                            continue

                        symbol_l = symbol.lower()

                        klines[f"{symbol_l}_1"].append([
                            k.get("start"),
                            float(k.get("open", 0)),
                            float(k.get("high", 0)),
                            float(k.get("low", 0)),
                            float(k.get("close", 0)),
                            float(k.get("volume", 0)),
                        ])

                        res = analyze(symbol_l)
                        if res and news_filter():

                            now = time.time()
                            # cooldown 3 دقائق لكل عملة
                            if symbol_l in last_signal_time and now - last_signal_time[symbol_l] < 180:
                                continue

                            last_signal_time[symbol_l] = now

                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=format_message(res)
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

    print("🔥 BOT RUNNING (BEAST MODE)")

    asyncio.create_task(ws_loop(app.bot))

    await asyncio.Event().wait()

# ================= RUN =================
if __name__ == "__main__":
    asyncio.run(main())
