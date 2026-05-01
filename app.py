import os, asyncio, json, time, math
from collections import deque, defaultdict

import pandas as pd
import ta
import websockets
import ccxt

from telegram.ext import Application

# ================= إعدادات =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))

MAX_SYMBOLS = 30
TOP_K = 3
COOLDOWN_SEC = 1800

# ================= اتصال =================
ex = ccxt.binance({"enableRateLimit": True})

klines = defaultdict(lambda: deque(maxlen=120))
last_sent = {}

# ================= AI =================
def ai_score(features):
    score = (
        features["rsi"] +
        features["macd"] +
        features["trend"] +
        features["volume"] +
        features["momentum"]
    )
    prob = 1 / (1 + math.exp(-score))
    return int(prob * 100)

# ================= العملات =================
def get_symbols():
    tickers = ex.fetch_tickers()
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

    # ===== Pump =====
    change = (r["c"] - prev["c"]) / prev["c"] * 100
    vol_spike = r["v"] > r["vma"] * 2
    pump = change > 1.5 and vol_spike

    # ===== Trend =====
    trend_up = r["ema50"] > r["ema200"]
    trend = "صاعد 📈" if trend_up else "هابط 📉"

    # ===== Pullback =====
    recent_high = df["h"].rolling(10).max().iloc[-1]
    pullback = pump and price < recent_high * 0.995

    # ===== AI =====
    features = {
        "rsi": 1 if r["rsi"] < 40 else -1,
        "macd": 1 if r["macd"] > r["macd_s"] else -1,
        "trend": 1 if trend_up else -1,
        "volume": 1 if vol_spike else -1,
        "momentum": 1 if change > 0 else -1
    }

    ai = ai_score(features)

    # ===== Signal =====
    signal = None

    if trend_up and pullback and r["macd"] > r["macd_s"]:
        signal = "شراء ذكي 🚀"

    if not signal or ai < 70:
        return None

    # ===== TP / SL =====
    entry = price
    tp1 = price * 1.015
    tp2 = price * 1.03
    sl  = price * 0.97

    return {
        "symbol": symbol.upper(),
        "signal": signal,
        "trend": trend,
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "ai": ai,
        "pump": pump
    }

# ================= رسالة =================
def build_msg(x):
    pump_text = "\n🚨 حركة قوية" if x["pump"] else ""

    return f"""━━━━━━━━━━━━━━━
📊 {x['symbol']}

🔥 {x['signal']}
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

# ================= WebSocket =================
async def ws_loop(app):
    while True:
        try:
            symbols = get_symbols()
            streams = "/".join([f"{s}@kline_1m" for s in symbols])

            url = f"wss://stream.binance.com:9443/stream?streams={streams}"

            async with websockets.connect(url) as ws:
                print("🔥 WS CONNECTED")

                buffer = []

                async for msg in ws:
                    data = json.loads(msg)
                    k = data.get("data", {}).get("k", {})

                    if not k:
                        continue

                    symbol = k["s"].lower()

                    t = k["t"]
                    o = float(k["o"])
                    h = float(k["h"])
                    l = float(k["l"])
                    c = float(k["c"])
                    v = float(k["v"])

                    klines[symbol].append([t,o,h,l,c,v])

                    if k.get("x"):
                        res = analyze(symbol)
                        if res:
                            buffer.append(res)

                    if len(buffer) >= 15:
                        await send_top(app, buffer)
                        buffer.clear()

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= تشغيل =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    async def start(app):
        print("🚀 BOT STARTED")
        asyncio.create_task(ws_loop(app))

    app.post_init = start

    app.run_polling()

if __name__ == "__main__":
    main()
