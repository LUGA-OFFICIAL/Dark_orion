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

MAX_SYMBOLS = 35          # عدد الأزواج المراقَبة
TOP_K = 3                 # عدد الرسائل في كل دفعة
COOLDOWN_SEC = 1800       # منع تكرار نفس الزوج (30 دقيقة)

# ================= مصادر =================
ex = ccxt.binance({"enableRateLimit": True})

# ================= تخزين =================
klines = defaultdict(lambda: deque(maxlen=120))  # لكل زوج آخر 120 شمعة 1m
last_sent = {}  # symbol -> last timestamp

# ================= AI (Lightweight) =================
def ai_score(features: dict) -> int:
    # weights بسيطة (تقدر تعدلها)
    w = {
        "rsi": 0.2,
        "macd": 0.2,
        "trend": 0.2,
        "volume": 0.2,
        "momentum": 0.2,
    }
    s = (
        features["rsi"] * w["rsi"] +
        features["macd"] * w["macd"] +
        features["trend"] * w["trend"] +
        features["volume"] * w["volume"] +
        features["momentum"] * w["momentum"]
    )
    # sigmoid -> نسبة %
    prob = 1 / (1 + math.exp(-s))
    return int(prob * 100)

# ================= جلب أفضل الأزواج =================
def get_top_usdt_symbols(n=MAX_SYMBOLS):
    tickers = ex.fetch_tickers()
    pairs = []
    for s, d in tickers.items():
        if "/USDT" in s and d.get("quoteVolume"):
            pairs.append((s.replace("/", "").lower(), d["quoteVolume"]))  # btcusdt
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:n]]

# ================= التحليل الذكي =================
def analyze(symbol: str):
    data = list(klines[symbol])
    if len(data) < 60:
        return None

    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])

    # مؤشرات
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
    price_change = (r["c"] - prev["c"]) / prev["c"] * 100
    volume_spike = r["v"] > (r["vma"] if pd.notna(r["vma"]) else 0) * 2
    pump = price_change > 1.5 and volume_spike

    # ===== Smart Pullback =====
    recent_high = df["h"].rolling(10).max().iloc[-1]
    recent_low  = df["l"].rolling(10).min().iloc[-1]
    pullback_long  = pump and price < recent_high * 0.995
    pullback_short = pump and price > recent_low  * 1.005

    # ===== Trend =====
    trend_up = r["ema50"] > r["ema200"]
    trend = "صاعد 📈" if trend_up else "هابط 📉"

    # ===== Features للـ AI =====
    features = {
        "rsi": 1 if r["rsi"] < 40 else -1,
        "macd": 1 if r["macd"] > r["macd_s"] else -1,
        "trend": 1 if trend_up else -1,
        "volume": 1 if volume_spike else -1,
        "momentum": 1 if price_change > 0 else -1,
    }
    ai_prob = ai_score(features)

    # ===== Score إضافي (TA) =====
    score = 0
    reasons = []

    if r["rsi"] < 40:
        score += 15; reasons.append("RSI مناسب")
    if r["macd"] > r["macd_s"]:
        score += 15; reasons.append("MACD إيجابي")
    if trend_up:
        score += 15; reasons.append("ترند صاعد")
    if pump:
        score += 25; reasons.append("🚨 Pump (سعر+فوليوم)")
    if pullback_long or pullback_short:
        score += 30; reasons.append("🎯 دخول بعد تصحيح")

    # ===== الإشارة =====
    signal = None
    if trend_up and pullback_long and r["macd"] > r["macd_s"]:
        signal = "شراء ذكي 🧠🚀"
    elif (not trend_up) and pullback_short and r["macd"] < r["macd_s"]:
        signal = "بيع ذكي 🧠🔻"

    # فلتر AI
    if not signal or ai_prob < 70:
        return None

    # ===== TP/SL =====
    entry = price
    if "شراء" in signal:
        tp1 = price * 1.015
        tp2 = price * 1.03
        sl  = price * 0.97
    else:
        tp1 = price * 0.985
        tp2 = price * 0.97
        sl  = price * 1.03

    return {
        "symbol": symbol.upper(),
        "signal": signal,
        "trend": trend,
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "score": min(100, score),
        "ai": ai_prob,
        "reasons": reasons,
        "pump": pump
    }

# ================= الرسالة =================
def build_msg(x: dict) -> str:
    reasons = "\n".join([f"✔ {r}" for r in x["reasons"]])
    pump_text = "\n🚨 تنبيه: حركة انفجارية" if x["pump"] else ""
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
📊 قوة الذكاء: {x['ai']}%  |  Score: {x['score']}%{pump_text}

🧠 التحليل:
{reasons}

━━━━━━━━━━━━━━━
⚠️ إدارة رأس المال مهمة
━━━━━━━━━━━━━━━
"""

# ================= إرسال Top K =================
async def send_top(app, candidates):
    candidates.sort(key=lambda x: (x["ai"], x["score"]), reverse=True)
    sent = 0
    now = time.time()

    for x in candidates:
        sym = x["symbol"]
        if sym in last_sent and now - last_sent[sym] < COOLDOWN_SEC:
            continue

        await app.bot.send_message(chat_id=CHAT_ID, text=build_msg(x))
        last_sent[sym] = now
        sent += 1
        if sent >= TOP_K:
            break

# ================= WebSocket Loop =================
async def ws_loop(app):
    while True:
        try:
            symbols = get_top_usdt_symbols()
            streams = "/".join([f"{s}@kline_1m" for s in symbols])
            url = f"wss://stream.binance.com:9443/stream?streams={streams}"

            async with websockets.connect(url, ping_interval=20) as ws:
                print("🔥 WS CONNECTED")
                buffer_candidates = []

                async for msg in ws:
                    data = json.loads(msg)
                    k = data.get("data", {}).get("k", {})
                    if not k:
                        continue

                    symbol = k["s"].lower()
                    t = k["t"]; o = float(k["o"]); h = float(k["h"])
                    l = float(k["l"]); c = float(k["c"]); v = float(k["v"])

                    # خزّن الشمعة (حتى قبل الإغلاق)
                    klines[symbol].append([t,o,h,l,c,v])

                    # لما الشمعة تقفل
                    if k.get("x"):
                        res = analyze(symbol)
                        if res:
                            buffer_candidates.append(res)

                    # كل دفعة نرسل أفضل فرص
                    if len(buffer_candidates) >= 20:
                        await send_top(app, buffer_candidates)
                        buffer_candidates.clear()

        except Exception as e:
            print("WS ERROR:", e)
            await asyncio.sleep(5)

# ================= تشغيل =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.create_task(ws_loop(app))
    print("🚀 PRO SMART BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
