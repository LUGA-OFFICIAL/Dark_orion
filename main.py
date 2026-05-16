import os
import asyncio
import json
import time
import hashlib
import re
import aiohttp
import feedparser
import pandas as pd
import ta
import websockets
from collections import defaultdict, deque
from aiohttp import web
from telegram.ext import Application, CommandHandler

print(str(‘BOT STARTING’))

BOT_TOKEN = os.getenv(str(‘BOT_TOKEN’))
CHAT_ID   = int(os.getenv(str(‘CHAT_ID’), str(‘0’)))
PORT      = int(os.getenv(str(‘PORT’), str(‘8080’)))

MIN_SCORE      = 55
COOLDOWN       = 14400
NEW_SL_PCT     = 0.08
MIN_VOL        = 500000

ALL_SYMBOLS = [
str(‘BTCUSDT’), str(‘ETHUSDT’), str(‘SOLUSDT’), str(‘BNBUSDT’), str(‘XRPUSDT’),
str(‘ADAUSDT’), str(‘DOGEUSDT’), str(‘AVAXUSDT’), str(‘LINKUSDT’), str(‘MATICUSDT’),
str(‘ARBUSDT’), str(‘OPUSDT’), str(‘INJUSDT’), str(‘SUIUSDT’), str(‘SEIUSDT’),
str(‘APTUSDT’), str(‘NEARUSDT’), str(‘ATOMUSDT’), str(‘TRXUSDT’), str(‘LTCUSDT’),
str(‘ETCUSDT’), str(‘FILUSDT’), str(‘ICPUSDT’), str(‘HBARUSDT’), str(‘ALGOUSDT’),
str(‘DOTUSDT’), str(‘UNIUSDT’), str(‘AAVEUSDT’), str(‘MKRUSDT’),
]

LISTING_KW = [
str(‘will list’), str(‘listing’), str(‘new listing’),
str(‘will be listed’), str(‘spot listing’), str(‘innovation zone’),
]
EXCLUDE = {
str(‘USDT’), str(‘USD’), str(‘BTC’), str(‘ETH’), str(‘BNB’),
str(‘UTC’), str(‘GMT’), str(‘API’), str(‘FAQ’), str(‘NFT’),
str(‘BUSD’), str(‘FDUSD’), str(‘TUSD’), str(‘USDC’), str(‘DAI’),
}

klines        = defaultdict(lambda: deque(maxlen=300))
scoreboard    = defaultdict(float)
last_sig      = {}
open_trades   = {}
known_syms    = set()
seen_ann      = set()

async def health(req):
return web.Response(text=str(‘OK’))

async def start_health():
a = web.Application()
a.router.add_get(str(’/’), health)
r = web.AppRunner(a)
await r.setup()
await web.TCPSite(r, str(‘0.0.0.0’), PORT).start()
print(str(’Health OK port ’) + str(PORT))

def fmt(p):
if p >= 1000: return str(’{:,.2f}’).format(p)
if p >= 1:    return str(’{:.4f}’).format(p)
if p >= 0.01: return str(’{:.5f}’).format(p)
return str(’{:.8f}’).format(p)

def build_df(key):
d = list(klines[key])
if len(d) < 50: return None
df = pd.DataFrame(d, columns=[str(‘t’),str(‘o’),str(‘h’),str(‘l’),str(‘c’),str(‘v’)])
for col in [str(‘o’),str(‘h’),str(‘l’),str(‘c’),str(‘v’)]:
df[col] = pd.to_numeric(df[col], errors=str(‘coerce’))
return df.dropna()

def analyze(symbol):
try:
df1 = build_df(symbol + str(’_1’))
df5 = build_df(symbol + str(’_5’))
if df1 is None or df5 is None: return None
if len(df1) < 50 or len(df5) < 50: return None

```
    score = 0
    price = df1[str('c')].iloc[-1]

    ema50  = ta.trend.EMAIndicator(df5[str('c')], 50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df5[str('c')], 200).ema_indicator() if len(df5) >= 200 else ema50
    if ema50.iloc[-1] > ema200.iloc[-1]: score += 25
    else: score -= 10

    macd = ta.trend.MACD(df5[str('c')])
    if macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]:
        score += 20
        macd_bull = True
    else:
        macd_bull = False

    bb    = ta.volatility.BollingerBands(df5[str('c')])
    bb_l  = bb.bollinger_lband().iloc[-1]
    bb_h  = bb.bollinger_hband().iloc[-1]
    bb_rng = bb_h - bb_l
    bb_pos = (df5[str('c')].iloc[-1] - bb_l) / bb_rng if bb_rng > 0 else 0.5
    if bb_pos < 0.25: score += 15
    elif bb_pos > 0.80: score -= 10

    rsi = ta.momentum.RSIIndicator(df1[str('c')], 14).rsi().iloc[-1]
    if rsi < 35: score += 20
    elif rsi > 65: score -= 15
    elif rsi > 52: score += 10

    high15   = df1[str('h')].rolling(15).max().iloc[-2]
    breakout = price > high15
    if breakout: score += 15

    vol_now  = df1[str('v')].iloc[-1]
    vol_avg  = df1[str('v')].rolling(20).mean().iloc[-1]
    vol_spk  = vol_now > vol_avg * 1.5
    if vol_spk: score += 20

    recent_high = df1[str('h')].rolling(20).max().iloc[-2]
    sweep = df1[str('h')].iloc[-1] > recent_high and price < recent_high
    if sweep: score += 20

    cs  = df1[str('h')].iloc[-1] - df1[str('l')].iloc[-1]
    avg_cs = (df1[str('h')] - df1[str('l')]).rolling(20).mean().iloc[-1]
    if avg_cs > 0 and cs > avg_cs * 2.5: return None

    if score < MIN_SCORE: return None

    scoreboard[symbol.upper()] += score * 0.1

    atr = ta.volatility.AverageTrueRange(
        df1[str('h')], df1[str('l')], df1[str('c')]
    ).average_true_range().iloc[-1]

    if score >= 80:   tm, t2m, sm = 2.0, 3.5, 1.2; grade = str('HIGH')
    elif score >= 65: tm, t2m, sm = 1.5, 2.5, 1.0; grade = str('MEDIUM')
    else:             tm, t2m, sm = 1.0, 1.8, 0.8; grade = str('SCALP')

    if sweep: sig = str('Smart Money')
    elif breakout and vol_spk: sig = str('Breakout+Vol')
    elif rsi < 35: sig = str('Oversold')
    else: sig = str('Momentum')

    tp1 = price + atr * tm
    tp2 = price + atr * t2m
    sl  = price - atr * sm
    t1p = round(abs((tp1-price)/price*100), 2)
    t2p = round(abs((tp2-price)/price*100), 2)
    slp = round(abs((sl-price)/price*100), 2)
    rr  = round(t1p/slp, 1) if slp > 0 else 0

    return dict(
        symbol=symbol.upper(), price=price,
        tp1=tp1, tp2=tp2, sl=sl,
        score=score, grade=grade, sig=sig,
        rsi=round(rsi,1), t1p=t1p, t2p=t2p, slp=slp, rr=rr,
    )
except Exception as e:
    print(str('ANALYZE ERR: ') + str(e))
    return None
```

def signal_txt(r):
note = str(‘Strong - multiple indicators’) if r[str(‘grade’)] == str(‘HIGH’) else str(‘Medium momentum’) if r[str(‘grade’)] == str(‘MEDIUM’) else str(‘Scalp - higher risk’)
return chr(10).join([
str(‘BUY [’) + r[str(‘grade’)] + str(’] ‘) + r[str(‘symbol’)],
r[str(‘sig’)] + str(’ | Score: ‘) + str(r[str(‘score’)]) + str(’%’),
note,
str(’’),
str(‘Entry: $’) + fmt(r[str(‘price’)]),
str(‘TP1:   $’) + fmt(r[str(‘tp1’)]) + str(’ (+’) + str(r[str(‘t1p’)]) + str(’%)’),
str(‘TP2:   $’) + fmt(r[str(‘tp2’)]) + str(’ (+’) + str(r[str(‘t2p’)]) + str(’%)’),
str(‘SL:    $’) + fmt(r[str(‘sl’)]) + str(’ (-’) + str(r[str(‘slp’)]) + str(’%)’),
str(‘RR:    1:’) + str(r[str(‘rr’)]),
str(’’),
str(‘RSI: ‘) + str(r[str(‘rsi’)]) + str(’ | TF: 1m+5m | Bybit’),
str(‘Educational use only’),
])

def listing_txt(sym, price, vol):
sl  = price * (1 - NEW_SL_PCT)
tp1 = price * 1.15
tp2 = price * 1.30
tp3 = price * 1.50
return chr(10).join([
str(‘NEW LISTING: ‘) + sym,
str(‘Price: $’) + fmt(price),
str(‘Vol:   $’) + str(’{:,.0f}’).format(vol),
str(’’),
str(‘Entry: $’) + fmt(price),
str(‘TP1:   $’) + fmt(tp1) + str(’ (+15%)’),
str(‘TP2:   $’) + fmt(tp2) + str(’ (+30%)’),
str(‘TP3:   $’) + fmt(tp3) + str(’ (+50%)’),
str(‘SL:    $’) + fmt(sl)  + str(’ (-8%)’),
str(’’),
str(‘Exit 40% TP1 | 40% TP2 | hold 20% TP3’),
str(‘HIGH RISK - new listing!’),
])

def ann_txt(title, link, syms, src):
coins = str(’ / ’).join(syms) if syms else str(‘check link’)
return chr(10).join([
str(‘EARLY ALERT - Upcoming Listing!’),
str(‘Source: ‘) + src,
str(‘Coin: ‘) + coins,
str(’’),
title,
str(’’),
str(‘Prepare before listing!’),
link,
])

def select_top():
ranked = sorted(ALL_SYMBOLS, key=lambda s: scoreboard[s], reverse=True)[:25]
return [str(‘kline.1.’) + s for s in ranked] + [str(‘kline.5.’) + s for s in ranked]

async def ws_loop(bot, stop):
url = str(‘wss://stream.bybit.com/v5/public/spot’)
active = []
while not stop.is_set():
try:
async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
print(str(‘WS connected’))
active = select_top()
await ws.send(json.dumps({str(‘op’): str(‘subscribe’), str(‘args’): active}))
last_rot = time.time()
async for raw in ws:
if stop.is_set(): return
data = json.loads(raw)
if time.time() - last_rot > 600:
new = select_top()
await ws.send(json.dumps({str(‘op’): str(‘unsubscribe’), str(‘args’): active}))
await ws.send(json.dumps({str(‘op’): str(‘subscribe’), str(‘args’): new}))
active = new
last_rot = time.time()
if str(‘data’) not in data: continue
topic = data.get(str(‘topic’), str(’’))
tf = str(‘1’) if str(’.1.’) in topic else str(‘5’)
for k in data[str(‘data’)]:
sym = k.get(str(‘symbol’), str(’’)).lower()
if not sym: continue
klines[sym + str(’_’) + tf].append([
k.get(str(‘start’)),
float(k.get(str(‘open’), 0)),
float(k.get(str(‘high’), 0)),
float(k.get(str(‘low’), 0)),
float(k.get(str(‘close’), 0)),
float(k.get(str(‘volume’), 0)),
])
if tf == str(‘1’):
res = analyze(sym)
if res:
now = time.time()
if now - last_sig.get(sym, 0) > COOLDOWN:
last_sig[sym] = now
await bot.send_message(chat_id=CHAT_ID, text=signal_txt(res))
open_trades[res[str(‘symbol’)]] = res
trade = open_trades.get(sym.upper())
if trade:
cur = float(k.get(str(‘close’), 0))
if cur >= trade[str(‘tp2’)]:
await bot.send_message(chat_id=CHAT_ID, text=str(‘TP2 HIT: ‘) + sym.upper() + str(’ $’) + fmt(cur) + str(chr(10)) + str(‘Close position!’))
del open_trades[sym.upper()]
elif cur >= trade[str(‘tp1’)]:
await bot.send_message(chat_id=CHAT_ID, text=str(‘TP1 HIT: ‘) + sym.upper() + str(’ $’) + fmt(cur) + str(chr(10)) + str(‘Move SL to entry!’))
open_trades[sym.upper()][str(‘sl’)] = trade[str(‘price’)]
elif cur <= trade[str(‘sl’)]:
await bot.send_message(chat_id=CHAT_ID, text=str(‘SL HIT: ‘) + sym.upper() + str(’ $’) + fmt(cur) + str(chr(10)) + str(‘Trade closed.’))
del open_trades[sym.upper()]
except asyncio.CancelledError: return
except Exception as e:
print(str(’WS ERR: ’) + str(e))
await asyncio.sleep(5)

async def check_listings(bot, stop):
global known_syms
while not stop.is_set():
try:
async with aiohttp.ClientSession() as s:
async with s.get(str(‘https://api.bybit.com/v5/market/instruments-info?category=spot’), timeout=aiohttp.ClientTimeout(total=15)) as r:
data = await r.json()
items = data.get(str(‘result’), {}).get(str(‘list’), [])
cur = {x[str(‘symbol’)] for x in items if x.get(str(‘quoteCoin’)) == str(‘USDT’) and x.get(str(‘status’)) == str(‘Trading’)}
if not known_syms:
known_syms = cur
else:
new = cur - known_syms
for sym in new:
await asyncio.sleep(30)
try:
async with aiohttp.ClientSession() as s:
async with s.get(str(‘https://api.bybit.com/v5/market/tickers?category=spot&symbol=’) + sym) as r:
t = await r.json()
it = t.get(str(‘result’), {}).get(str(‘list’), [])
if it:
price = float(it[0].get(str(‘lastPrice’), 0))
vol   = float(it[0].get(str(‘volume24h’), 0)) * price
if price > 0 and vol >= MIN_VOL:
await bot.send_message(chat_id=CHAT_ID, text=listing_txt(sym, price, vol))
except Exception as e:
print(str(’Listing err: ’) + str(e))
if new: known_syms = cur
except Exception as e:
print(str(’Check listings err: ’) + str(e))
await asyncio.sleep(300)

async def check_ann(bot, stop):
global seen_ann
sources = [
(str(‘https://announcements.bybit.com/rss/en-US/’), str(‘Bybit’)),
(str(‘https://nitter.net/Bybit_Official/rss’), str(‘Twitter’)),
]
while not stop.is_set():
for url, src in sources:
try:
async with aiohttp.ClientSession() as s:
async with s.get(url, headers={str(‘User-Agent’): str(‘Mozilla/5.0’)}, timeout=aiohttp.ClientTimeout(total=15)) as r:
if r.status == 200:
entries = feedparser.parse(await r.text()).entries[:10]
for e in entries:
title = e.get(str(‘title’), str(’’))
link  = e.get(str(‘link’),  str(’’))
aid   = hashlib.md5(link.encode()).hexdigest()
if aid in seen_ann: continue
if any(kw in title.lower() for kw in LISTING_KW):
syms = [x for x in re.findall(r”(([A-Z]{2,10}))”, title) if x not in EXCLUDE]
await bot.send_message(chat_id=CHAT_ID, text=ann_txt(title, link, syms, src), disable_web_page_preview=True)
seen_ann.add(aid)
except Exception as e:
print(str(’RSS err: ’) + str(e))
await asyncio.sleep(120)

async def cmd_start(u, c):  await u.message.reply_text(str(‘Bot running!\n/status\n/trades\n/top\n/help’))
async def cmd_status(u, c): await u.message.reply_text(str(‘Running\nSymbols: ‘) + str(len(ALL_SYMBOLS)) + str(’\nSignals: ‘) + str(len(last_sig)) + str(’\nTrades: ‘) + str(len(open_trades)))
async def cmd_trades(u, c):
if not open_trades:
await u.message.reply_text(str(‘No open trades.’))
return
lines = [str(‘Open Trades:’)]
for sym, t in open_trades.items():
lines.append(sym + str(’ | $’) + fmt(t[str(‘price’)]) + str(’ | TP1:$’) + fmt(t[str(‘tp1’)]) + str(’ | SL:$’) + fmt(t[str(‘sl’)]))
await u.message.reply_text(chr(10).join(lines))
async def cmd_top(u, c):
ranked = sorted(ALL_SYMBOLS, key=lambda s: scoreboard[s], reverse=True)[:10]
lines  = [str(‘Top 10 Symbols:’)]
for i, s in enumerate(ranked, 1):
lines.append(str(i) + str(’. ‘) + s + str(’ - ’) + str(round(scoreboard[s],1)))
await u.message.reply_text(chr(10).join(lines))
async def cmd_help(u, c):   await u.message.reply_text(str(‘Beast Mode Bot\nEMA+MACD+RSI+BB+ATR+Vol\nTF: 1m+5m | Bybit\nAuto TP/SL alerts\nNew listing alerts\nEarly announcement alerts\nEducational use only.’))

async def main():
print(str(‘Initializing…’))
await start_health()
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler(str(‘start’),  cmd_start))
app.add_handler(CommandHandler(str(‘status’), cmd_status))
app.add_handler(CommandHandler(str(‘trades’), cmd_trades))
app.add_handler(CommandHandler(str(‘top’),    cmd_top))
app.add_handler(CommandHandler(str(‘help’),   cmd_help))
await app.initialize()
await app.start()
bot = app.bot
await bot.send_message(
chat_id=CHAT_ID,
text=chr(10).join([
str(‘Beast Mode Active!’),
str(‘Symbols: ‘) + str(len(ALL_SYMBOLS)),
str(‘TF: 1m + 5m’),
str(‘Min score: ‘) + str(MIN_SCORE) + str(’%’),
str(‘Cooldown: 4h per symbol’),
str(’’),
str(‘Commands: /status /trades /top /help’),
])
)
stop = asyncio.Event()
await asyncio.gather(
ws_loop(bot, stop),
check_listings(bot, stop),
check_ann(bot, stop),
app.updater.start_polling(drop_pending_updates=True),
)

if **name** == str(’**main**’):
asyncio.run(main())
