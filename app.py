print(‘BOT STARTING…’)
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

BOT_TOKEN = os.getenv(‘BOT_TOKEN’)
CHAT_ID = int(os.getenv(‘CHAT_ID’, ‘0’))
PORT = int(os.getenv(‘PORT’, ‘8080’))

MIN_SCORE = 70
NEW_SL_PCT = 0.08
MIN_VOL = 500000
COOLDOWN = 14400

SYMBOLS = [
‘BTCUSDT’,‘ETHUSDT’,‘BNBUSDT’,‘SOLUSDT’,‘XRPUSDT’,
‘ADAUSDT’,‘DOGEUSDT’,‘AVAXUSDT’,‘LINKUSDT’,‘SUIUSDT’,
‘DOTUSDT’,‘MATICUSDT’,‘UNIUSDT’,‘ATOMUSDT’,‘NEARUSDT’,
‘ALGOUSDT’,‘HBARUSDT’,‘INJUSDT’,‘APTUSDT’,‘OPUSDT’,
‘ARBUSDT’,‘FILUSDT’,‘LDOUSDT’,‘MKRUSDT’,‘AAVEUSDT’,
‘XTZUSDT’,‘EGLDUSDT’,‘FLOWUSDT’,‘MANAUSDT’,‘SANDUSDT’,
]

LISTING_KW = [‘will list’,‘listing’,‘new listing’,‘will be listed’,‘spot listing’,‘innovation zone’]
EXCLUDE = {‘USDT’,‘USD’,‘BTC’,‘ETH’,‘BNB’,‘UTC’,‘GMT’,‘API’,‘FAQ’,‘NFT’,‘BUSD’,‘FDUSD’,‘TUSD’,‘USDC’,‘DAI’}

klines = defaultdict(lambda: deque(maxlen=250))
known_syms = set()
seen_ann = set()
last_sig = {}
open_trades = {}

async def health(req):
return web.Response(text=‘OK’)

async def start_health():
a = web.Application()
a.router.add_get(’/’, health)
r = web.AppRunner(a)
await r.setup()
await web.TCPSite(r, ‘0.0.0.0’, PORT).start()
print(’Health OK port ’ + str(PORT))

def build_df(key):
d = list(klines[key])
if len(d) < 80:
return None
df = pd.DataFrame(d, columns=[‘t’,‘o’,‘h’,‘l’,‘c’,‘v’])
for col in [‘o’,‘h’,‘l’,‘c’,‘v’]:
df[col] = pd.to_numeric(df[col], errors=‘coerce’)
return df.dropna()

def analyze(symbol):
try:
df1 = build_df(symbol + ‘_1’)
df5 = build_df(symbol + ‘_5’)
if df1 is None or df5 is None:
return None

```
    score = 0

    ema50 = ta.trend.EMAIndicator(df5['c'], 50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df5['c'], 200).ema_indicator()
    trend = ema50.iloc[-1] > ema200.iloc[-1]
    if trend:
        score += 30
    else:
        score -= 10

    macd = ta.trend.MACD(df5['c'])
    macd_bull = macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]
    if macd_bull:
        score += 20

    bb = ta.volatility.BollingerBands(df5['c'])
    bb_l = bb.bollinger_lband().iloc[-1]
    bb_h = bb.bollinger_hband().iloc[-1]
    bb_range = bb_h - bb_l
    bb_pos = (df5['c'].iloc[-1] - bb_l) / bb_range if bb_range > 0 else 0.5
    if bb_pos < 0.25:
        score += 15
    elif bb_pos > 0.80:
        score -= 10

    rsi = ta.momentum.RSIIndicator(df1['c'], 14).rsi().iloc[-1]
    if rsi < 35:
        score += 20
    elif rsi > 65:
        score -= 15
    elif rsi > 52:
        score += 10

    price = df1['c'].iloc[-1]
    high15 = df1['h'].rolling(15).max().iloc[-2]
    breakout = price > high15
    if breakout:
        score += 20

    vol_now = df1['v'].iloc[-1]
    vol_avg = df1['v'].rolling(20).mean().iloc[-1]
    vol_spike = vol_now > vol_avg * 1.7
    if vol_spike:
        score += 15

    atr = ta.volatility.AverageTrueRange(df1['h'], df1['l'], df1['c']).average_true_range().iloc[-1]

    if score < MIN_SCORE:
        return None

    tp1 = price + atr * 1.5
    tp2 = price + atr * 3.0
    sl = price - atr * 1.2

    return {
        'symbol': symbol.upper(),
        'price': price,
        'tp1': tp1,
        'tp2': tp2,
        'sl': sl,
        'score': score,
        'rsi': round(rsi, 1),
        'trend': trend,
        'macd': macd_bull,
        'breakout': breakout,
        'vol_spike': vol_spike,
    }
except Exception as e:
    print('ANALYZE ERR ' + symbol + ': ' + str(e))
    return None
```

def fmt(p):
if p >= 1000:
return ‘{:,.2f}’.format(p)
if p >= 1:
return ‘{:.4f}’.format(p)
if p >= 0.01:
return ‘{:.5f}’.format(p)
return ‘{:.8f}’.format(p)

def pct(a, b):
return ‘{:.2f}’.format(abs((b - a) / a * 100))

def signal_text(r):
rsi_t = ‘Oversold’ if r[‘rsi’] < 35 else ‘Overbought’ if r[‘rsi’] > 65 else ‘Neutral’
trend_t = ‘UP’ if r[‘trend’] else ‘DOWN’
macd_t = ‘Bull’ if r[‘macd’] else ‘Bear’
vol_t = ‘SPIKE’ if r[‘vol_spike’] else ‘Normal’
tp1_p = pct(r[‘price’], r[‘tp1’])
tp2_p = pct(r[‘price’], r[‘tp2’])
sl_p = pct(r[‘price’], r[‘sl’])
rr1 = abs((r[‘tp1’] - r[‘price’]) / (r[‘price’] - r[‘sl’]))
lines = [
‘BUY SIGNAL - ’ + r[‘symbol’],
‘Score: ’ + str(r[‘score’]) + ‘%’,
‘’,
‘Entry: $’ + fmt(r[‘price’]),
‘TP1:   $’ + fmt(r[‘tp1’]) + ’ (+’ + tp1_p + ‘%)’,
‘TP2:   $’ + fmt(r[‘tp2’]) + ’ (+’ + tp2_p + ‘%)’,
‘SL:    $’ + fmt(r[‘sl’]) + ’ (-’ + sl_p + ‘%)’,
‘RR:    1:’ + ‘{:.1f}’.format(rr1),
‘’,
’RSI: ’ + str(r[‘rsi’]) + ’ - ’ + rsi_t,
’EMA Trend: ’ + trend_t,
’MACD: ’ + macd_t,
’Volume: ’ + vol_t,
’Breakout: ’ + (‘YES’ if r[‘breakout’] else ‘NO’),
‘’,
‘TF: 1m + 5m | Bybit’,
‘For educational use only’,
]
return ‘\n’.join(lines)

def listing_text(sym, price, vol):
sl = price * (1 - NEW_SL_PCT)
tp1 = price * 1.15
tp2 = price * 1.30
tp3 = price * 1.50
lines = [
‘NEW LISTING: ’ + sym,
‘Price:  $’ + fmt(price),
‘Volume: $’ + ‘{:,.0f}’.format(vol),
‘’,
‘Entry: $’ + fmt(price),
‘TP1:   $’ + fmt(tp1) + ’ (+15%)’,
‘TP2:   $’ + fmt(tp2) + ’ (+30%)’,
‘TP3:   $’ + fmt(tp3) + ’ (+50%)’,
‘SL:    $’ + fmt(sl) + ’ (-8%)’,
‘’,
‘Exit 40% at TP1, 40% at TP2, hold 20% for TP3’,
‘HIGH RISK - new listing!’,
]
return ‘\n’.join(lines)

def prelisting_text(title, link, syms, src):
coins = ’ / ’.join(syms) if syms else ‘check announcement’
lines = [
‘EARLY ALERT - Upcoming Listing!’,
’Source: ’ + src,
’Coin: ’ + coins,
‘’,
title,
‘’,
‘Prepare your order before listing!’,
link,
]
return ‘\n’.join(lines)

async def ws_loop(bot, stop):
args = []
for sym in SYMBOLS:
s = sym.lower()
args.append(‘kline.1.’ + sym)
args.append(‘kline.5.’ + sym)

```
url = 'wss://stream.bybit.com/v5/public/spot'
sub = json.dumps({'op': 'subscribe', 'args': args})

while not stop.is_set():
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
            print('WS connected - ' + str(len(SYMBOLS)) + ' symbols')
            await ws.send(sub)

            async for raw in ws:
                if stop.is_set():
                    return
                data = json.loads(raw)
                if 'data' not in data:
                    continue

                topic = data.get('topic', '')
                tf = '1' if '.1.' in topic else '5'

                for k in data['data']:
                    sym = k.get('symbol', '').lower()
                    if not sym:
                        continue

                    klines[sym + '_' + tf].append([
                        k.get('start'),
                        float(k.get('open', 0)),
                        float(k.get('high', 0)),
                        float(k.get('low', 0)),
                        float(k.get('close', 0)),
                        float(k.get('volume', 0)),
                    ])

                    if tf == '1':
                        res = analyze(sym)
                        if res:
                            now = time.time()
                            if now - last_sig.get(sym, 0) > COOLDOWN:
                                last_sig[sym] = now
                                await bot.send_message(chat_id=CHAT_ID, text=signal_text(res))
                                open_trades[res['symbol']] = res

                        trade = open_trades.get(sym.upper())
                        if trade:
                            cur = float(k.get('close', 0))
                            if cur >= trade['tp1']:
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text='TP1 HIT: ' + sym.upper() + ' at $' + fmt(cur) + '\nMove SL to entry point!'
                                )
                                del open_trades[sym.upper()]
                            elif cur <= trade['sl']:
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text='SL HIT: ' + sym.upper() + ' at $' + fmt(cur) + '\nTrade closed.'
                                )
                                del open_trades[sym.upper()]

    except asyncio.CancelledError:
        return
    except Exception as e:
        print('WS ERR: ' + str(e))
        await asyncio.sleep(5)
```

async def check_listings(bot, stop):
global known_syms
while not stop.is_set():
try:
async with aiohttp.ClientSession() as s:
async with s.get(‘https://api.bybit.com/v5/market/instruments-info?category=spot’, timeout=aiohttp.ClientTimeout(total=15)) as r:
data = await r.json()
items = data.get(‘result’, {}).get(‘list’, [])
cur = {x[‘symbol’] for x in items if x.get(‘quoteCoin’) == ‘USDT’ and x.get(‘status’) == ‘Trading’}

```
        if not known_syms:
            known_syms = cur
            print('Baseline: ' + str(len(known_syms)) + ' symbols')
        else:
            new = cur - known_syms
            for sym in new:
                print('NEW LISTING: ' + sym)
                await asyncio.sleep(30)
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get('https://api.bybit.com/v5/market/tickers?category=spot&symbol=' + sym) as r:
                            t = await r.json()
                    items2 = t.get('result', {}).get('list', [])
                    if items2:
                        price = float(items2[0].get('lastPrice', 0))
                        vol = float(items2[0].get('volume24h', 0)) * price
                        if price > 0 and vol >= MIN_VOL:
                            await bot.send_message(chat_id=CHAT_ID, text=listing_text(sym, price, vol))
                except Exception as e:
                    print('Listing handle err: ' + str(e))
            if new:
                known_syms = cur
    except Exception as e:
        print('Listing check err: ' + str(e))
    await asyncio.sleep(300)
```

async def check_ann(bot, stop):
global seen_ann
sources = [
(‘https://announcements.bybit.com/rss/en-US/’, ‘Bybit Official’),
(‘https://nitter.net/Bybit_Official/rss’, ‘Bybit Twitter’),
]
while not stop.is_set():
for url, src in sources:
try:
async with aiohttp.ClientSession() as s:
async with s.get(url, headers={‘User-Agent’: ‘Mozilla/5.0’}, timeout=aiohttp.ClientTimeout(total=15)) as r:
if r.status == 200:
entries = feedparser.parse(await r.text()).entries[:10]
for e in entries:
title = e.get(‘title’, ‘’)
link = e.get(‘link’, ‘’)
aid = hashlib.md5(link.encode()).hexdigest()
if aid in seen_ann:
continue
if any(kw in title.lower() for kw in LISTING_KW):
syms = [x for x in re.findall(r’(([A-Z]{2,10}))’, title) if x not in EXCLUDE]
await bot.send_message(
chat_id=CHAT_ID,
text=prelisting_text(title, link, syms, src),
disable_web_page_preview=True
)
seen_ann.add(aid)
except Exception as e:
print(’RSS err: ’ + str(e))
await asyncio.sleep(120)

async def cmd_start(update, ctx):
await update.message.reply_text(
‘Bot is running!\n\n’
‘/status - Bot status\n’
‘/symbols - Monitored symbols\n’
‘/trades - Open trades\n’
‘/help - Help’
)

async def cmd_status(update, ctx):
await update.message.reply_text(
‘Status: Running\n’
’Symbols: ’ + str(len(SYMBOLS)) + ‘\n’
’Streams: ’ + str(len(klines)) + ‘\n’
’Signals sent: ’ + str(len(last_sig)) + ‘\n’
’Open trades: ’ + str(len(open_trades)) + ‘\n’
’Known listings: ’ + str(len(known_syms))
)

async def cmd_symbols(update, ctx):
txt = ‘Monitoring ’ + str(len(SYMBOLS)) + ’ symbols on Bybit:\n\n’
txt += ’, ’.join(SYMBOLS)
await update.message.reply_text(txt)

async def cmd_trades(update, ctx):
if not open_trades:
await update.message.reply_text(‘No open trades.’)
return
lines = [‘Open Trades:’]
for sym, t in open_trades.items():
lines.append(sym + ’ - Entry: $’ + fmt(t[‘price’]) + ’ TP1: $’ + fmt(t[‘tp1’]) + ’ SL: $’ + fmt(t[‘sl’]))
await update.message.reply_text(’\n’.join(lines))

async def cmd_help(update, ctx):
await update.message.reply_text(
‘Trading Bot - Bybit\n\n’
‘Auto signals: EMA+MACD+RSI+BB+ATR\n’
‘Timeframes: 1m + 5m\n’
‘Auto TP1/SL alerts\n’
‘New listing alerts\n’
‘Early announcement alerts\n\n’
‘For educational use only.’
)

async def main():
print(‘Initializing…’)
await start_health()

```
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler('start', cmd_start))
app.add_handler(CommandHandler('status', cmd_status))
app.add_handler(CommandHandler('symbols', cmd_symbols))
app.add_handler(CommandHandler('trades', cmd_trades))
app.add_handler(CommandHandler('help', cmd_help))

await app.initialize()
await app.start()
bot = app.bot

await bot.send_message(
    chat_id=CHAT_ID,
    text=(
        'Bot is running!\n'
        'Platform: Bybit\n'
        'Symbols: ' + str(len(SYMBOLS)) + '\n'
        'Timeframes: 1m + 5m\n'
        'Min score: ' + str(MIN_SCORE) + '%\n'
        'Cooldown: ' + str(int(COOLDOWN/3600)) + 'h per symbol\n\n'
        'Commands: /status /symbols /trades /help'
    )
)

stop = asyncio.Event()
await asyncio.gather(
    ws_loop(bot, stop),
    check_listings(bot, stop),
    check_ann(bot, stop),
    app.updater.start_polling(drop_pending_updates=True),
)
```

if **name** == ‘**main**’:
asyncio.run(main())
