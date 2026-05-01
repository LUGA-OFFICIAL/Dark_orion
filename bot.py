import asyncio
import json
import websockets
import ccxt
import pandas as pd
import ta
from datetime import datetime
from collections import defaultdict

from telegram.ext import Application, CommandHandler

# ========= CONFIG =========
BOT_TOKEN = "8578183213:AAGiXcQ_IyjuPPv97dYSsor8uzm84vJgcD0"
CHAT_ID   = 7776277503

SYMBOLS = ["btcusdt","ethusdt","solusdt"]

# multi-timeframe
TF_FAST = "1m"
TF_MID  = "5m"
TF_SLOW = "1h"

CONFIDENCE_MIN = 78
MAX_SIGNALS_PER_HOUR = 10

exchange = ccxt.binance({"enableRateLimit": True})

state = {
    "last_signal": {},
    "hour_count": 0,
    "hour_ts": datetime.utcnow().hour
}

# ========= HELPERS =========
def reset_hour_counter():
    h = datetime.utcnow().hour
    if h != state["hour_ts"]:
        state["hour_ts"] = h
        state["hour_count"] = 0

def fetch_df(symbol, tf, limit=200):
    data = exchange.fetch_ohlcv(symbol.upper(), tf, limit=limit)
    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])
    return df

def add_indicators(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["c"], 14).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"] = macd.macd()
    df["macd_s"] = macd.macd_signal()
    df["ema20"] = ta.trend.EMAIndicator(df["c"],20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["c"],50).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(
        high=df["h"], low=df["l"], close=df["c"], window=14
    ).average_true_range()
    df["vol_avg"] = df["v"].rolling(20).mean()
    return df

def order_book_bias(symbol):
    ob = exchange.fetch_order_book(symbol.upper(), limit=20)
    bids = sum(b[1] for b in ob["bids"])
    asks = sum(a[1] for a in ob["asks"])
    return 1 if bids > asks else -1

# ========= STRATEGY =========
def compute_signal(symbol):
    # --- multi-timeframe ---
    df1 = add_indicators(fetch_df(symbol, TF_FAST))
    df5 = add_indicators(fetch_df(symbol, TF_MID))
    df1h = add_indicators(fetch_df(symbol, TF_SLOW))

    r1  = df1.iloc[-1]
    r5  = df5.iloc[-1]
    r1h = df1h.iloc[-1]

    score = 0

    # trend (1h)
    if r1h["ema20"] > r1h["ema50"]:
        score += 2
    else:
        score -= 2

    # momentum (5m)
    if r5["macd"] > r5["macd_s"]:
        score += 2
    else:
        score -= 2

    # entry (1m)
    if r1["rsi"] < 35:
        score += 2
    elif r1["rsi"] > 65:
        score -= 2

    # volume
    if r1["v"] > r1["vol_avg"] * 1.5:
        score += 1

    # order book
    score += order_book_bias(symbol)

    confidence = max(40, min(95, 50 + score * 7))

    if score >= 3:
        side = "BUY"
    elif score <= -3:
        side = "SELL"
    else:
        return None, confidence, None, None

    # ATR-based SL/TP (ديناميكي)
    price = r1["c"]
    atr = r1["atr"]

    if side == "BUY":
        sl = price - 1.2 * atr
        tp = price + 2.5 * atr
    else:
        sl = price + 1.2 * atr
        tp = price - 2.5 * atr

    return side, confidence, price, (tp, sl)

# ========= TELEGRAM =========
async def send_signal(app, symbol, side, price, tp, sl, conf):
    reset_hour_counter()
    if state["hour_count"] >= MAX_SIGNALS_PER_HOUR:
        return

    if state["last_signal"].get(symbol) == side:
        return

    state["last_signal"][symbol] = side
    state["hour_count"] += 1

    rr = abs((tp - price) / (price - sl)) if (price - sl) != 0 else 0

    msg = f"""📊 {symbol.upper()}
{side}
Price: {price:.4f}

TP: {tp:.4f}
SL: {sl:.4f}
RR: 1:{rr:.2f}

Confidence: {conf}%"""

    await app.bot.send_message(chat_id=CHAT_ID, text=msg)

# ========= LOOP =========
async def loop(app):
    while True:
        for s in SYMBOLS:
            try:
                side, conf, price, levels = compute_signal(s)
                if side and conf >= CONFIDENCE_MIN:
                    tp, sl = levels
                    await send_signal(app, s, side, price, tp, sl, conf)
            except Exception as e:
                print("ERR:", e)

        await asyncio.sleep(30)  # كل 30 ثانية

# ========= COMMAND =========
async def start(update, context):
    await update.message.reply_text("🚀 PRO TRADING BOT V3 RUNNING")

# ========= MAIN =========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    app.job_queue.run_once(lambda c: asyncio.create_task(loop(app)), 2)

    print("🔥 V3 STARTED")
    app.run_polling()

if __name__ == "__main__":
    main()
