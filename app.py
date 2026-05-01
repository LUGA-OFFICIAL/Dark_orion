import os, asyncio, json, time, math, requests
from collections import deque, defaultdict

import pandas as pd
import ta
import websockets
import ccxt

from telegram.ext import Application

print("🔥 FILE STARTED")

# ================= إعدادات =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))

MAX_SYMBOLS = 30
TOP_K = 3
COOLDOWN_SEC = 1800

# ================= Exchange (Bybit) =================
exchange = ccxt.bybit({"enableRateLimit": True})

klines = defaultdict(lambda: deque(maxlen=120))
last_sent = {}

# ================= AI =================
def ai_score(f):
    s = f["rsi"] + f["macd"] + f["trend"] + f["volume"] + f["momentum"]
    prob = 1 / (1 + math.exp(-s))
    return int(prob * 100)

# ================= جلب العملات =================
def get_symbols():
    tickers = exchange.fetch_tickers()
    pairs = []

    for s, d in tickers.items():
        if "/USDT" in s and d.get("quoteVolume"):
            pairs.append((s.replace("/", "").lower(), d["quoteVolume"]))

    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:MAX_SYMBOLS]]

# ================= تحليل =================
def analyze(symbol):
    data = list(klines[symbol])
    if len(data) < 60:
        return None

    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["macd_s"] = macd.macd_signal()

    df["ema50"] = ta.trend.EMAIndicator(df["c"], 50).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["c"], 200).ema_indicator()

    df["vma"] = df["v"].rolling(20).mean()

    r = df.iloc[-1]
    prev = df.iloc[-2]

    price = r["c"]

    change = (r["c"] - prev["c"]) / prev["c"] * 100
    vol_spike = r["v"] > r["vma"] * 2 if pd.notna(r["vma"]) else False
    pump = change > 1.5 and vol_spike

    trend_up = r["ema50"] > r["ema200"]
    trend = "صاعد 📈" if trend_up else "هابط 📉"

    recent_high = df["h"].rolling(10).max().iloc[-1]
    pullback = pump and price < recent_high * 0.995

    features = {
        "rsi": 1 if r["rsi"] < 40 else -1,
        "macd": 1 if r["macd"] > r["macd_s"] else -1,
        "trend": 1 if trend_up else -1,
        "volume": 1 if vol_spike else -1,
        "momentum": 1 if change > 0 else -1
    }

    ai = ai_score(features)

    if not (trend_up and pullback and r["macd"] > r["macd_s"] and ai >= 70):
        return None

    return {
        "symbol": symbol.upper(),
        "entry": price,
        "tp1": price * 1.015,
        "tp2": price * 1.03,
        "sl": price * 0.97,
        "ai": ai,
        "trend": trend,
        "pump": pump
    }

# ================= رسالة =================
def build_msg(x):
    pump_text = "\n🚨 حركة قوية" if x["pump"] else ""

    return f"""━━━━━━━━━━━━━━━
📊 {x['symbol']}

🔥 شراء ذكي 🚀
📈 الاتجاه: {x['trend']}

💰 الدخول:
{x['entry']:.4f}

🎯 الأهداف:
➤ {x['tp1']:.4f}
➤ {x['tp2']:.4f}

🛑 وقف الخسارة:
{x['sl']:.4f}

━━━━━━━━━━━━━━━
🧠 قوة الذكاء: {x['ai']}%{pump_text}

━━━━━━━━━━━━━━━
⚠️ إدارة رأس المال مهمة
"""

# ================= إرسال =================
async def send_top(app, signals):
    signals.sort(key=lambda x: x["ai"], reverse=True)

    now = time.time()
    sent = 0

    for s in signals:
        if s["symbol"] in last_sent:
            if now - last_sent[s["symbol"]] < COOLDOWN_SEC:
                continue

        await app.bot.send_message(chat_id=CHAT_ID, text=build_msg(s))
        last_sent[s["symbol"]] = now

        sent += 1
        if sent >= TOP_K:
            break

# ================= WebSocket (Bybit) =================
async def ws_loop(app):
    while True:
        try:
            symbols = get_symbols()
            print("📊 Symbols loaded")

            # Bybit public kline
            streams = [f"kline.1.{s.upper()}" for s in symbols]

            url = "wss://stream.bybit.com/v5/public/spot"

            async with websockets.connect(url) as ws:
                print("🔥 WS CONNECTED")

                # subscribe
                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": streams
                }))

                buffer = []

                async for msg in ws:
                    data = json.loads(msg)

                    if "data" not in data:
                        continue

                    for k in data["data"]:
                        symbol = k["symbol"].lower()

                        t = k["start"]
                        o = float(k["open"])
                        h = float(k["high"])
                        l = float(k["low"])
                        c = float(k["close"])
                        v = float(k["volume"])

                        klines[symbol].append([t,o,h,l,c,v])

                        res = analyze(symbol)
                        if res:
                            buffer.append(res)

                    if len(buffer) >= 10:
                        await send_top(app, buffer)
                        buffer.clear()

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= تشغيل =================
def main():
    print("🚀 STARTING BOT...")

    # حل مشكلة Telegram Conflict
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")

    app = Application.builder().token(BOT_TOKEN).build()

    async def start(app):
        print("🔥 BOT STARTED")
        asyncio.create_task(ws_loop(app))

    app.post_init = start

    print("⚡ RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
