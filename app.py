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
CHAT_ID = int(os.getenv(“CHAT_ID”, “0”))
PORT = int(os.getenv(“PORT”, “8080”))
CONFIDENCE_MIN = 75
SL_PCT = 0.03
TP_RATIO = 2.5
NEW_LISTING_SL = 0.08
MIN_VOLUME_USDT = 1000000
TF_FAST = “1m”
TF_SLOW = “15m”
BINANCE_RSS = “https://www.binance.com/en/feed/blog/rss”
NITTER_URL = “https://nitter.net/binance/rss”
LISTING_KEYWORDS = [“will list”,“lists”,“listing”,“new listing”,“will be listed”,“spot listing”,“innovation zone”,“seed tag”]
EXCLUDE_SYMBOLS = {“USDT”,“USD”,“BTC”,“ETH”,“BNB”,“UTC”,“GMT”,“API”,“FAQ”,“NFT”,“CEO”,“KYC”,“AML”,“VIP”,“BUSD”,“FDUSD”,“TUSD”,“USDC”,“DAI”}
klines = defaultdict(lambda: deque(maxlen=200))
known_symbols = set()
seen_announcements = set()
sent_signals = {}

async def health(request):
return web.Response(text=“OK”)

async def start_health_server():
app_http = web.Application()
app_http.router.add_get(”/”, health)
runner = web.AppRunner(app_http)
await runner.setup()
site = web.TCPSite(runner, “0.0.0.0”, PORT)
await site.start()
print(“Health server port “ + str(PORT))

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
score = 0
ema50 = ta.trend.EMAIndicator(df_slow[“c”], 50).ema_indicator()
ema200 = ta.trend.EMAIndicator(df_slow[“c”], 200).ema_indicator()
macd = ta.trend.MACD(df_slow[“c”])
bb = ta.volatility.BollingerBands(df_slow[“c”])
trend_up = ema50.iloc[-1] > ema200.iloc[-1]
macd_bull = macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]
bb_h = bb.bollinger_hband().iloc[-1]
bb_l = bb.bollinger_lband().iloc[-1]
bb_range = bb_h - bb_l
bb_pos = (df_slow[“c”].iloc[-1] - bb_l) / bb_range if bb_range > 0 else 0.5
if trend_up: score += 2
else: score -= 2
if macd_bull: score += 2
else: score -= 2
if bb_pos < 0.25: score += 1
elif bb_pos > 0.75: score -= 1
rsi_s = ta.momentum.RSIIndicator(df_fast[“c”], 14).rsi()
rsi = rsi_s.iloc[-1]
high20 = df_fast[“h”].rolling(20).max().iloc[-2]
price = df_fast[“c”].iloc[-1]
breakout = price > high20
vol_avg = df_fast[“v”].rolling(20).mean().iloc[-1]
high_vol = df_fast[“v”].iloc[-1] > vol_avg * 1.5
if rsi < 35: score += 2
elif rsi > 65: score -= 2
if breakout: score += 1
if high_vol: score += 1
confidence = min(95, max(40, 50 + score * 8))
if score >= 4 and confidence >= CONFIDENCE_MIN:
direction = “BUY”
elif score <= -4 and confidence >= CONFIDENCE_MIN:
direction = “SELL”
else:
return None
tp_pct = SL_PCT * TP_RATIO
if direction == “BUY”:
tp = price * (1 + tp_pct)
sl = price * (1 - SL_PCT)
else:
tp = price * (1 - tp_pct)
sl = price * (1 + SL_PCT)
return {“symbol”: symbol.upper(), “direction”: direction, “price”: price, “tp”: tp, “sl”: sl, “conf”: int(confidence), “rsi”: round(rsi, 1), “macd_bull”: macd_bull, “trend_up”: trend_up, “high_vol”: high_vol}
except Exception as e:
print(“ANALYZE ERROR: “ + str(e))
return None

def fmt(p):
if p >= 1000: return “{:,.2f}”.format(p)
if p >= 1: return “{:.4f}”.format(p)
if p >= 0.01: return “{:.5f}”.format(p)
return “{:.8f}”.format(p)

def signal_msg(s):
arrow = “+” if s[“direction”] == “BUY” else “-”
tp_pct = abs((s[“tp”] - s[“price”]) / s[“price”] * 100)
sl_pct = abs((s[“sl”] - s[“price”]) / s[“price”] * 100)
rr = tp_pct / sl_pct
now = datetime.now().strftime(”%Y-%m-%d %H:%M”)
rsi_t = “Oversold” if s[“rsi”] < 35 else “Overbought” if s[“rsi”] > 65 else “Neutral”
return “\n”.join([
“Signal: *” + s[“direction”] + “* “ + s[“symbol”],
“Confidence: *” + str(s[“conf”]) + “%*”,
“”,
“Entry: $” + fmt(s[“price”]),
“TP:    $” + fmt(s[“tp”]) + “ (” + arrow + “{:.2f}”.format(tp_pct) + “%)”,
“SL:    $” + fmt(s[“sl”]) + “ (-{:.2f}”.format(sl_pct) + “%)”,
“RR:    1:{:.1f}”.format(rr),
“”,
“RSI(” + str(s[“rsi”]) + “): “ + rsi_t,
“MACD: “ + (“Bull” if s[“macd_bull”] else “Bear”),
“EMA: “ + (“Up” if s[“trend_up”] else “Down”),
“Vol: “ + (“High” if s[“high_vol”] else “Normal”),
now,
])

def listing_msg(symbol, price, volume):
sl = price * (1 - NEW_LISTING_SL)
tp1 = price * 1.15
tp2 = price * 1.30
tp3 = price * 1.50
now = datetime.now().strftime(”%Y-%m-%d %H:%M”)
return “\n”.join([
“NEW LISTING: *” + symbol + “*”,
“Price:  $” + fmt(price),
“Volume: ${:,.0f}”.format(volume),
“”,
“Entry: $” + fmt(price),
“TP1:   $” + fmt(tp1) + “ (+15%)”,
“TP2:   $” + fmt(tp2) + “ (+30%)”,
“TP3:   $” + fmt(tp3) + “ (+50%)”,
“SL:    $” + fmt(sl) + “ (-8%)”,
“”,
“Exit 40% TP1, 40% TP2, 20% TP3”,
“HIGH RISK - new listing!”,
now,
])

def prelisting_msg(title, link, symbols, source):
coins = “ / “.join(symbols) if symbols else “check link”
now = datetime.now().strftime(”%Y-%m-%d %H:%M”)
return “\n”.join([
“EARLY ALERT - Upcoming Listing!”,
“Source: “ + source,
“Coin: *” + coins + “*”,
title,
“”,
“Prepare before listing!”,
link,
now,
])

async def fetch_all_symbols():
url = “https://api.binance.com/api/v3/exchangeInfo”
try:
async with aiohttp.ClientSession() as s:
async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
data = await r.json()
syms = [x[“symbol”] for x in data[“symbols”] if x[“quoteAsset”] == “USDT” and x[“status”] == “TRADING” and x[“isSpotTradingAllowed”]]
print(“Got “ + str(len(syms)) + “ symbols”)
return syms
except Exception as e:
print(“Symbol fetch error: “ + str(e))
return [“BTCUSDT”,“ETHUSDT”,“BNBUSDT”,“SOLUSDT”,“XRPUSDT”,“ADAUSDT”,“DOGEUSDT”,“AVAXUSDT”,“LINKUSDT”,“SUIUSDT”]

async def ws_stream(bot, symbols, tf, stop_event):
streams = [s.lower() + “@kline_” + tf for s in symbols]
BASE = “wss://stream.binance.com:9443/stream?streams=”
while not stop_event.is_set():
try:
for i in range(0, len(streams), 180):
chunk = streams[i:i+180]
url = BASE + “/”.join(chunk)
async with websockets.connect(url, ping_interval=20, ping_timeout=30, max_size=10*1024*1024) as ws:
print(“WS “ + tf + “ connected “ + str(len(chunk)) + “ symbols”)
async for raw in ws:
if stop_event.is_set():
return
msg = json.loads(raw)
data = msg.get(“data”, {})
if data.get(“e”) != “kline”:
continue
k = data[“k”]
sym = k[“s”].lower()
klines[sym + “*” + tf].append([k[“t”], float(k[“o”]), float(k[“h”]), float(k[“l”]), float(k[“c”]), float(k[“v”])])
if tf == TF_FAST and k[“x”]:
res = analyze(sym)
if res:
key = res[“symbol”] + “*” + res[“direction”]
last = sent_signals.get(key, 0)
now_ts = asyncio.get_event_loop().time()
if now_ts - last > 14400:
sent_signals[key] = now_ts
await bot.send_message(chat_id=CHAT_ID, text=signal_msg(res), parse_mode=“Markdown”)
except asyncio.CancelledError:
return
except Exception as e:
print(“WS “ + tf + “ error: “ + str(e))
await asyncio.sleep(5)

async def check_new_listings(bot, stop_event):
global known_symbols
url = “https://api.binance.com/api/v3/exchangeInfo”
while not stop_event.is_set():
try:
async with aiohttp.ClientSession() as s:
async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
data = await r.json()
current = {x[“symbol”] for x in data[“symbols”] if x[“quoteAsset”] == “USDT” and x[“status”] == “TRADING”}
if not known_symbols:
known_symbols = current
print(“Baseline: “ + str(len(known_symbols)))
else:
new = current - known_symbols
for sym in new:
print(“NEW: “ + sym)
await asyncio.sleep(30)
try:
async with aiohttp.ClientSession() as s:
async with s.get(“https://api.binance.com/api/v3/ticker/24hr?symbol=” + sym) as r:
t = await r.json()
price = float(t.get(“lastPrice”, 0))
volume = float(t.get(“quoteVolume”, 0))
if price > 0 and volume >= MIN_VOLUME_USDT:
await bot.send_message(chat_id=CHAT_ID, text=listing_msg(sym, price, volume), parse_mode=“Markdown”)
except Exception as e:
print(“Listing handle error: “ + str(e))
if new:
known_symbols = current
except Exception as e:
print(“Listing check error: “ + str(e))
await asyncio.sleep(300)

async def monitor_announcements(bot, stop_event):
global seen_announcements
while not stop_event.is_set():
for url, source in [(BINANCE_RSS, “Binance”), (NITTER_URL, “Twitter”)]:
try:
async with aiohttp.ClientSession() as s:
async with s.get(url, headers={“User-Agent”: “Mozilla/5.0”}, timeout=aiohttp.ClientTimeout(total=15)) as r:
if r.status == 200:
entries = feedparser.parse(await r.text()).entries[:10]
for entry in entries:
title = entry.get(“title”, “”)
link = entry.get(“link”, “”)
aid = hashlib.md5(link.encode()).hexdigest()
if aid in seen_announcements:
continue
if any(kw in title.lower() for kw in LISTING_KEYWORDS):
syms = [x for x in re.findall(r”(([A-Z]{2,10}))”, title) if x not in EXCLUDE_SYMBOLS]
await bot.send_message(chat_id=CHAT_ID, text=prelisting_msg(title, link, syms, source), parse_mode=“Markdown”, disable_web_page_preview=True)
seen_announcements.add(aid)
except Exception as e:
print(“RSS error: “ + str(e))
await asyncio.sleep(120)

async def cmd_start(update, context):
await update.message.reply_text(“Bot running!\n/status\n/symbols\n/help”)

async def cmd_status(update, context):
await update.message.reply_text(“Status: Running\nStreams: “ + str(len(klines)) + “\nSignals sent: “ + str(len(sent_signals)))

async def cmd_symbols(update, context):
await update.message.reply_text(“Monitoring “ + str(len(known_symbols)) + “ USDT pairs on Binance”)

async def cmd_help(update, context):
await update.message.reply_text(“Auto signals: RSI+MACD+EMA+BB on 1m+15m\nNew listings: instant alert\nAnnouncements: early alert\nFor educational use only.”)

async def main():
print(“Starting…”)
await start_health_server()
all_symbols = await fetch_all_symbols()
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler(“start”, cmd_start))
app.add_handler(CommandHandler(“status”, cmd_status))
app.add_handler(CommandHandler(“symbols”, cmd_symbols))
app.add_handler(CommandHandler(“help”, cmd_help))
await app.initialize()
await app.start()
bot = app.bot
await bot.send_message(chat_id=CHAT_ID, text=“Bot running!\nSymbols: “ + str(len(all_symbols)) + “\nTF: “ + TF_FAST + “+” + TF_SLOW + “\nMin confidence: “ + str(CONFIDENCE_MIN) + “%”)
stop_event = asyncio.Event()
await asyncio.gather(
ws_stream(bot, all_symbols, TF_FAST, stop_event),
ws_stream(bot, all_symbols, TF_SLOW, stop_event),
check_new_listings(bot, stop_event),
monitor_announcements(bot, stop_event),
app.updater.start_polling(drop_pending_updates=True),
)

if **name** == “**main**”:
asyncio.run(main())
