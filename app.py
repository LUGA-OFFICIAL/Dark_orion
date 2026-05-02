import os
import asyncio
import json
import hashlib
import re
import feedparser
import aiohttp
import pandas as pd
import ta
import websockets

from collections import defaultdict, deque
from datetime import datetime
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv(“BOT_TOKEN”, “”)
CHAT_ID   = int(os.getenv(“CHAT_ID”, “0”))
PORT      = int(os.getenv(“PORT”, “8080”))

CONFIDENCE_MIN  = 75
SL_PCT          = 0.03
TP_RATIO        = 2.5
NEW_LISTING_SL  = 0.08
MIN_VOLUME_USDT = 1000000
TF_FAST         = “1m”
TF_SLOW         = “15m”

BINANCE_RSS = “https://www.binance.com/en/feed/blog/rss”
NITTER_URL  = “https://nitter.net/binance/rss”

LISTING_KEYWORDS = [
“will list”, “lists”, “listing”, “new listing”,
“will be listed”, “spot listing”, “innovation zone”, “seed tag”,
]

EXCLUDE_SYMBOLS = {
“USDT”,“USD”,“BTC”,“ETH”,“BNB”,“UTC”,“GMT”,
“API”,“FAQ”,“NFT”,“CEO”,“KYC”,“AML”,“VIP”,
“BUSD”,“FDUSD”,“TUSD”,“USDC”,“DAI”,
}

klines             = defaultdict(lambda: deque(maxlen=200))
known_symbols      = set()
seen_announcements = set()
sent_signals       = {}

async def health(request):
return web.Response(text=“OK”)

async def start_health_server():
app_http = web.Application()
app_http.router.add_get(”/”, health)
runner = web.AppRunner(app_http)
await runner.setup()
site = web.TCPSite(runner, “0.0.0.0”, PORT)
await site.start()
print(“Health server started on port “ + str(PORT))

def build_df(key):
data = list(klines[key])
if len(data) < 60:
return None
df = pd.DataFrame(data, columns=[“t”,“o”,“h”,“l”,“c”,“v”])
for col in [“o”,“h”,“l”,“c”,“v”]:
df[col] = pd.to_numeric(df[col], errors=“coerce”)
return df.dropna()

def analyze(symbol):
try:
df_fast = build_df(symbol + “*” + TF_FAST)
df_slow = build_df(symbol + “*” + TF_SLOW)
if df_fast is None or df_slow is None:
return None

```
    score = 0

    ema50  = ta.trend.EMAIndicator(df_slow["c"], 50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df_slow["c"], 200).ema_indicator()
    macd   = ta.trend.MACD(df_slow["c"])
    bb     = ta.volatility.BollingerBands(df_slow["c"])

    trend_up  = ema50.iloc[-1] > ema200.iloc[-1]
    macd_bull = macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]
    bb_h      = bb.bollinger_hband().iloc[-1]
    bb_l      = bb.bollinger_lband().iloc[-1]
    bb_range  = bb_h - bb_l
    bb_pos    = (df_slow["c"].iloc[-1] - bb_l) / bb_range if bb_range > 0 else 0.5

    if trend_up:      score += 2
    else:             score -= 2
    if macd_bull:     score += 2
    else:             score -= 2
    if bb_pos < 0.25: score += 1
    elif bb_pos > 0.75: score -= 1

    rsi_s    = ta.momentum.RSIIndicator(df_fast["c"], 14).rsi()
    rsi      = rsi_s.iloc[-1]
    high20   = df_fast["h"].rolling(20).max().iloc[-2]
    price    = df_fast["c"].iloc[-1]
    breakout = price > high20
    vol_avg  = df_fast["v"].rolling(20).mean().iloc[-1]
    high_vol = df_fast["v"].iloc[-1] > vol_avg * 1.5

    if rsi < 35:   score += 2
    elif rsi > 65: score -= 2
    if breakout:   score += 1
    if high_vol:   score += 1

    confidence = min(95, max(40, 50 + score * 8))

    if score >= 4 and confidence >= CONFIDENCE_MIN:
        direction = "BUY"
    elif score <= -4 and confidence >= CONFIDENCE_MIN:
        direction = "SELL"
    else:
        return None

    tp_pct = SL_PCT * TP_RATIO
    if direction == "BUY":
        tp = price * (1 + tp_pct)
        sl = price * (1 - SL_PCT)
    else:
        tp = price * (1 - tp_pct)
        sl = price * (1 + SL_PCT)

    return {
        "symbol": symbol.upper(),
        "direction": direction,
        "price": price,
        "tp": tp,
        "sl": sl,
        "conf": int(confidence),
        "rsi": round(rsi, 1),
        "macd_bull": macd_bull,
        "trend_up": trend_up,
        "high_vol": high_vol,
    }

except Exception as e:
    print("ANALYZE ERROR " + symbol + ": " + str(e))
    return None
```

def fmt_price(p):
if p >= 1000:  return “{:,.2f}”.format(p)
if p >= 1:     return “{:.4f}”.format(p)
if p >= 0.01:  return “{:.5f}”.format(p)
return “{:.8f}”.format(p)

def build_signal_msg(s):
side    = “BUY” if s[“direction”] == “BUY” else “SELL”
emoji   = “green” if side == “BUY” else “red”
arrow   = “+” if side == “BUY” else “-”
tp_pct  = abs((s[“tp”] - s[“price”]) / s[“price”] * 100)
sl_pct  = abs((s[“sl”] - s[“price”]) / s[“price”] * 100)
rr      = tp_pct / sl_pct
now     = datetime.now().strftime(”%Y-%m-%d %H:%M”)
rsi_txt = “Oversold” if s[“rsi”] < 35 else “Overbought” if s[“rsi”] > 65 else “Neutral”
macd_t  = “Bullish” if s[“macd_bull”] else “Bearish”
ema_t   = “Uptrend” if s[“trend_up”]  else “Downtrend”
vol_t   = “High” if s[“high_vol”] else “Normal”

```
lines = [
    "Signal: *" + side + "* " + s["symbol"] + "/USDT",
    "Confidence: *" + str(s["conf"]) + "%*",
    "",
    "Entry:  $" + fmt_price(s["price"]),
    "TP:     $" + fmt_price(s["tp"]) + " (" + arrow + "{:.2f}".format(tp_pct) + "%)",
    "SL:     $" + fmt_price(s["sl"]) + " (-{:.2f}".format(sl_pct) + "%)",
    "RR:     1:" + "{:.1f}".format(rr),
    "",
    "RSI (" + str(s["rsi"]) + "): " + rsi_txt,
    "MACD: " + macd_t,
    "EMA 50/200: " + ema_t,
    "Volume: " + vol_t,
    "",
    "TF: " + TF_FAST + " + " + TF_SLOW,
    now,
]
return "\n".join(lines)
```

def build_new_listing_msg(symbol, price, volume):
sl  = price * (1 - NEW_LISTING_SL)
tp1 = price * 1.15
tp2 = price * 1.30
tp3 = price * 1.50
now = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
lines = [
“NEW LISTING on Binance!”,
“Symbol: *” + symbol + “*”,
“Price:  $” + fmt_price(price),
“Volume: $” + “{:,.0f}”.format(volume),
“”,
“Entry: $” + fmt_price(price),
“TP1:   $” + fmt_price(tp1) + “ (+15%)”,
“TP2:   $” + fmt_price(tp2) + “ (+30%)”,
“TP3:   $” + fmt_price(tp3) + “ (+50%)”,
“SL:    $” + fmt_price(sl)  + “ (-8%)”,
“”,
“Strategy: exit 40% at TP1, 40% at TP2, keep 20% for TP3”,
“High risk - new listing!”,
now,
]
return “\n”.join(lines)

def build_prelisting_msg(title, link, symbols, source):
coins = “ / “.join(symbols) if symbols else “check announcement”
now   = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
lines = [
“EARLY ALERT - Upcoming Listing!”,
“Source: “ + source,
“Announcement: “ + title,
“”,
“Expected coin: *” + coins + “*”,
“”,
“You have time to:”,
“- Research the project”,
“- Decide your position size”,
“- Prepare your buy order”,
“”,
link,
“Wait for official listing confirmation!”,
now,
]
return “\n”.join(lines)

async def fetch_all_usdt_symbols():
url = “https://api.binance.com/api/v3/exchangeInfo”
try:
async with aiohttp.ClientSession() as session:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
data = await r.json()
symbols = [
s[“symbol”] for s in data[“symbols”]
if s[“quoteAsset”] == “USDT”
and s[“status”] == “TRADING”
and s[“isSpotTradingAllowed”]
]
print(“Fetched “ + str(len(symbols)) + “ symbols from Binance”)
return symbols
except Exception as e:
print(“Error fetching symbols: “ + str(e))
return [
“BTCUSDT”,“ETHUSDT”,“BNBUSDT”,“SOLUSDT”,“XRPUSDT”,
“ADAUSDT”,“DOGEUSDT”,“AVAXUSDT”,“LINKUSDT”,“SUIUSDT”,
]

async def ws_stream(bot, symbols, tf, stop_event):
streams = [s.lower() + “@kline_” + tf for s in symbols]
BASE    = “wss://stream.binance.com:9443/stream?streams=”

```
while not stop_event.is_set():
    try:
        for i in range(0, len(streams), 200):
            chunk = streams[i:i+200]
            url   = BASE + "/".join(chunk)

            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=30,
                max_size=10 * 1024 * 1024,
            ) as ws:
                print("WS " + tf + " connected (" + str(len(chunk)) + " symbols)")

                async for raw in ws:
                    if stop_event.is_set():
                        return

                    msg  = json.loads(raw)
                    data = msg.get("data", {})
                    if data.get("e") != "kline":
                        continue

                    k      = data["k"]
                    symbol = k["s"].lower()
                    key    = symbol + "_" + tf

                    klines[key].append([
                        k["t"],
                        float(k["o"]),
                        float(k["h"]),
                        float(k["l"]),
                        float(k["c"]),
                        float(k["v"]),
                    ])

                    if tf == TF_FAST and k["x"]:
                        res = analyze(symbol)
                        if res:
                            sig_key = res["symbol"] + "_" + res["direction"]
                            last    = sent_signals.get(sig_key, 0)
                            now_ts  = asyncio.get_event_loop().time()
                            if now_ts - last > 14400:
                                sent_signals[sig_key] = now_ts
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=build_signal_msg(res),
                                    parse_mode="Markdown",
                                )

    except asyncio.CancelledError:
        return
    except Exception as e:
        print("WS " + tf + " ERROR: " + str(e))
        await asyncio.sleep(5)
```

async def check_new_listings(bot, stop_event):
global known_symbols
url = “https://api.binance.com/api/v3/exchangeInfo”

```
while not stop_event.is_set():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
                current = {
                    s["symbol"] for s in data["symbols"]
                    if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
                }

        if not known_symbols:
            known_symbols = current
            print("Saved " + str(len(known_symbols)) + " symbols as baseline")
        else:
            new = current - known_symbols
            for sym in new:
                print("NEW LISTING: " + sym)
                await asyncio.sleep(30)
                await handle_new_listing(bot, sym)
            if new:
                known_symbols = current

    except Exception as e:
        print("Listing check error: " + str(e))

    await asyncio.sleep(300)
```

async def handle_new_listing(bot, symbol):
try:
url = “https://api.binance.com/api/v3/ticker/24hr?symbol=” + symbol
async with aiohttp.ClientSession() as session:
async with session.get(url) as r:
t = await r.json()

```
    price  = float(t.get("lastPrice", 0))
    volume = float(t.get("quoteVolume", 0))

    if price <= 0 or volume < MIN_VOLUME_USDT:
        print(symbol + " low volume (" + str(volume) + ") - skip")
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        text=build_new_listing_msg(symbol, price, volume),
        parse_mode="Markdown",
    )

except Exception as e:
    print("New listing error " + symbol + ": " + str(e))
```

def is_listing(title):
t = title.lower()
return any(kw in t for kw in LISTING_KEYWORDS)

def extract_symbols(text):
raw = re.findall(r’(([A-Z]{2,10}))’, text)
return [s for s in raw if s not in EXCLUDE_SYMBOLS]

async def fetch_rss(url):
headers = {“User-Agent”: “Mozilla/5.0”}
try:
async with aiohttp.ClientSession() as session:
async with session.get(
url, headers=headers,
timeout=aiohttp.ClientTimeout(total=15)
) as r:
if r.status == 200:
content = await r.text()
return feedparser.parse(content).entries[:10]
except Exception as e:
print(“RSS error: “ + str(e))
return []

async def monitor_announcements(bot, stop_event):
global seen_announcements

```
while not stop_event.is_set():
    for url, source in [
        (BINANCE_RSS, "Binance Official"),
        (NITTER_URL,  "Binance Twitter"),
    ]:
        entries = await fetch_rss(url)
        for entry in entries:
            title  = entry.get("title", "")
            link   = entry.get("link", "")
            ann_id = hashlib.md5(link.encode()).hexdigest()

            if ann_id in seen_announcements:
                continue

            if is_listing(title):
                symbols = extract_symbols(title)
                msg = build_prelisting_msg(title, link, symbols, source)
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )

            seen_announcements.add(ann_id)

    await asyncio.sleep(120)
```

async def cmd_start(update, context):
await update.message.reply_text(
“Bot is running!\n\n”
“/status - Bot status\n”
“/symbols - Monitored symbols\n”
“/help - Help”
)

async def cmd_status(update, context):
now = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
await update.message.reply_text(
“Bot Status\n\n”
“WebSocket 1m: Running\n”
“WebSocket 15m: Running\n”
“New Listings: Running\n”
“Announcements: Running\n\n”
“Stored data: “ + str(len(klines)) + “ streams\n”
“Signals sent: “ + str(len(sent_signals)) + “\n”
“Time: “ + now
)

async def cmd_symbols(update, context):
await update.message.reply_text(
“Monitoring all USDT pairs on Binance\n”
“Current count: “ + str(len(known_symbols)) + “ symbols\n”
“Updates every 5 minutes automatically.”
)

async def cmd_help(update, context):
await update.message.reply_text(
“Trading Bot Help\n\n”
“The bot runs automatically and sends:\n\n”
“1. Trade signals (RSI+MACD+EMA+BB)\n”
“   Timeframes: 1m + 15m\n\n”
“2. New listing alerts\n”
“   Instant alert with 3 targets\n\n”
“3. Early listing alerts\n”
“   From Binance announcements\n\n”
“For educational purposes only.”
)

async def main():
print(“Starting bot…”)

```
await start_health_server()

all_symbols = await fetch_all_usdt_symbols()

app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start",   cmd_start))
app.add_handler(CommandHandler("status",  cmd_status))
app.add_handler(CommandHandler("symbols", cmd_symbols))
app.add_handler(CommandHandler("help",    cmd_help))

await app.initialize()
await app.start()
bot = app.bot

await bot.send_message(
    chat_id=CHAT_ID,
    text=(
        "Bot is running!\n"
        "Monitoring: " + str(len(all_symbols)) + " symbols\n"
        "Timeframes: " + TF_FAST + " + " + TF_SLOW + "\n"
        "Min confidence: " + str(CONFIDENCE_MIN) + "%\n"
        "Stop loss: " + str(int(SL_PCT*100)) + "%\n"
        "RR ratio: 1:" + str(TP_RATIO)
    )
)

stop_event = asyncio.Event()

await asyncio.gather(
    ws_stream(bot, all_symbols, TF_FAST, stop_event),
    ws_stream(bot, all_symbols, TF_SLOW, stop_event),
    check_new_listings(bot, stop_event),
    monitor_announcements(bot, stop_event),
    app.updater.start_polling(drop_pending_updates=True),
)
```

if **name** == “**main**”:
asyncio.run(main())
