print(‘BEAST MODE STARTING…’)
import os
import asyncio
import json
import time
from collections import defaultdict, deque
from aiohttp import web
import pandas as pd
import ta
import websockets
from telegram.ext import Application, CommandHandler

BOT_TOKEN = os.getenv(‘BOT_TOKEN’)
CHAT_ID = int(os.getenv(‘CHAT_ID’, ‘0’))
PORT = int(os.getenv(‘PORT’, ‘8080’))

klines = defaultdict(lambda: deque(maxlen=300))
scoreboard = defaultdict(float)
last_signal_time = {}
open_trades = {}

ALL_SYMBOLS = [
‘BTCUSDT’,‘ETHUSDT’,‘SOLUSDT’,‘BNBUSDT’,‘XRPUSDT’,‘ADAUSDT’,
‘DOGEUSDT’,‘AVAXUSDT’,‘LINKUSDT’,‘MATICUSDT’,‘ARBUSDT’,‘OPUSDT’,
‘INJUSDT’,‘SUIUSDT’,‘SEIUSDT’,‘APTUSDT’,‘NEARUSDT’,
‘ATOMUSDT’,‘TRXUSDT’,‘LTCUSDT’,‘ETCUSDT’,‘FILUSDT’,‘ICPUSDT’,
‘HBARUSDT’,‘ALGOUSDT’,‘DOTUSDT’,‘UNIUSDT’,‘AAVEUSDT’,‘MKRUSDT’,
]

active_symbols = []

async def health(req):
return web.Response(text=‘OK’)

async def start_health():
a = web.Application()
a.router.add_get(’/’, health)
r = web.AppRunner(a)
await r.setup()
await web.TCPSite(r, ‘0.0.0.0’, PORT).start()
print(’Health OK port ’ + str(PORT))

def fmt(p):
if p >= 1000: return ‘{:,.2f}’.format(p)
if p >= 1: return ‘{:.4f}’.format(p)
if p >= 0.01: return ‘{:.5f}’.format(p)
return ‘{:.8f}’.format(p)

def select_top(limit=25):
ranked = sorted(ALL_SYMBOLS, key=lambda s: scoreboard[s], reverse=True)
selected = ranked[:limit]
args1 = [‘kline.1.’ + s for s in selected]
args5 = [‘kline.5.’ + s for s in selected]
return args1 + args5

def analyze(symbol):
try:
k1 = list(klines[symbol + ‘_1’])
k5 = list(klines[symbol + ‘_5’])

```
    # FIX 1: lowered from 100 to 50
    if len(k1) < 50 or len(k5) < 50:
        return None

    df1 = pd.DataFrame(k1, columns=['t','o','h','l','c','v'])
    df5 = pd.DataFrame(k5, columns=['t','o','h','l','c','v'])
    for col in ['o','h','l','c','v']:
        df1[col] = pd.to_numeric(df1[col], errors='coerce')
        df5[col] = pd.to_numeric(df5[col], errors='coerce')
    df1 = df1.dropna()
    df5 = df5.dropna()
    if len(df1) < 50 or len(df5) < 50:
        return None

    price = df1['c'].iloc[-1]

    # EMA trend on 5m
    ema50 = ta.trend.EMAIndicator(df5['c'], 50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df5['c'], 200).ema_indicator() if len(df5) >= 200 else ema50
    trend_up = ema50.iloc[-1] > ema200.iloc[-1]

    # MACD on 5m
    macd = ta.trend.MACD(df5['c'])
    macd_bull = macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]

    # RSI on 1m
    rsi = ta.momentum.RSIIndicator(df1['c'], 14).rsi().iloc[-1]

    # Bollinger on 5m
    bb = ta.volatility.BollingerBands(df5['c'])
    bb_l = bb.bollinger_lband().iloc[-1]
    bb_h = bb.bollinger_hband().iloc[-1]
    bb_range = bb_h - bb_l
    bb_pos = (df5['c'].iloc[-1] - bb_l) / bb_range if bb_range > 0 else 0.5

    # Volume spike on 1m
    vol_now = df1['v'].iloc[-1]
    vol_avg = df1['v'].rolling(20).mean().iloc[-1]
    vol_spike = vol_now > vol_avg * 1.5

    # Breakout on 1m
    high15 = df1['h'].rolling(15).max().iloc[-2]
    breakout = price > high15

    # Smart money sweep
    recent_high = df1['h'].rolling(20).max().iloc[-2]
    sweep = df1['h'].iloc[-1] > recent_high and price < recent_high

    # Candle filter - ignore wicks too large
    candle_size = df1['h'].iloc[-1] - df1['l'].iloc[-1]
    avg_candle = (df1['h'] - df1['l']).rolling(20).mean().iloc[-1]
    if avg_candle > 0 and candle_size > avg_candle * 2.5:
        return None

    # ATR for SL/TP
    atr = ta.volatility.AverageTrueRange(df1['h'], df1['l'], df1['c']).average_true_range().iloc[-1]

    # SCORE
    score = 0
    if trend_up:   score += 25
    if macd_bull:  score += 20
    if vol_spike:  score += 20
    if breakout:   score += 15
    if rsi < 35:   score += 20
    elif rsi > 65: score -= 15
    elif rsi > 52: score += 10
    if bb_pos < 0.25: score += 15
    elif bb_pos > 0.80: score -= 10
    if sweep:      score += 20

    # FIX 2: lowered min score from 70 to 55
    if score < 55:
        return None

    scoreboard[symbol.upper()] += score * 0.1

    if score >= 80:
        grade = 'HIGH QUALITY'
        tp1_m = 2.0
        tp2_m = 3.5
        sl_m  = 1.2
    elif score >= 65:
        grade = 'MEDIUM'
        tp1_m = 1.5
        tp2_m = 2.5
        sl_m  = 1.0
    else:
        grade = 'SCALP'
        tp1_m = 1.0
        tp2_m = 1.8
        sl_m  = 0.8

    if sweep:
        sig_type = 'Smart Money'
    elif breakout and vol_spike:
        sig_type = 'Breakout+Volume'
    elif rsi < 35:
        sig_type = 'Oversold Bounce'
    else:
        sig_type = 'Momentum'

    tp1 = price + atr * tp1_m
    tp2 = price + atr * tp2_m
    sl  = price - atr * sl_m

    tp1_pct = abs((tp1 - price) / price * 100)
    tp2_pct = abs((tp2 - price) / price * 100)
    sl_pct  = abs((sl  - price) / price * 100)
    rr = tp1_pct / sl_pct if sl_pct > 0 else 0

    return {
        'symbol':   symbol.upper(),
        'price':    price,
        'tp1':      tp1,
        'tp2':      tp2,
        'sl':       sl,
        'score':    score,
        'grade':    grade,
        'type':     sig_type,
        'rsi':      round(rsi, 1),
        'tp1_pct':  round(tp1_pct, 2),
        'tp2_pct':  round(tp2_pct, 2),
        'sl_pct':   round(sl_pct, 2),
        'rr':       round(rr, 1),
    }

except Exception as e:
    print('ANALYZE ERR ' + symbol + ': ' + str(e))
    return None
```

def signal_text(r):
grade_emoji = ‘HIGH QUALITY’ if ‘HIGH’ in r[‘grade’] else ‘MEDIUM’ if ‘MED’ in r[‘grade’] else ‘SCALP’
if ‘HIGH’ in r[‘grade’]:
note = ‘Strong signal - multiple indicators aligned’
elif ‘MED’ in r[‘grade’]:
note = ‘Medium signal - good momentum’
else:
note = ‘Scalp only - higher risk’

```
return '\n'.join([
    'BUY SIGNAL [' + grade_emoji + ']',
    r['symbol'] + ' | ' + r['type'],
    note,
    '',
    'Entry: $' + fmt(r['price']),
    'TP1:   $' + fmt(r['tp1']) + ' (+' + str(r['tp1_pct']) + '%)',
    'TP2:   $' + fmt(r['tp2']) + ' (+' + str(r['tp2_pct']) + '%)',
    'SL:    $' + fmt(r['sl'])  + ' (-' + str(r['sl_pct'])  + '%)',
    'RR:    1:' + str(r['rr']),
    '',
    'Score: ' + str(r['score']) + '% | RSI: ' + str(r['rsi']),
    'TF: 1m + 5m | Bybit',
    'Educational use only',
])
```

def tp_hit_text(sym, cur, tp_num):
return ‘\n’.join([
‘TP’ + str(tp_num) + ’ HIT: ’ + sym,
‘Price: $’ + fmt(cur),
‘Move SL to entry!’ if tp_num == 1 else ‘Consider closing position!’,
])

def sl_hit_text(sym, cur):
return ‘\n’.join([
’SL HIT: ’ + sym,
‘Price: $’ + fmt(cur),
‘Trade closed at stop loss.’,
])

async def ws_loop(bot, stop):
global active_symbols
url = ‘wss://stream.bybit.com/v5/public/spot’

```
while not stop.is_set():
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
            print('WS connected')
            active_symbols = select_top()
            await ws.send(json.dumps({'op': 'subscribe', 'args': active_symbols}))
            last_rotate = time.time()

            async for raw in ws:
                if stop.is_set(): return
                data = json.loads(raw)

                # rotate symbols every 10 min
                if time.time() - last_rotate > 600:
                    new = select_top()
                    await ws.send(json.dumps({'op': 'unsubscribe', 'args': active_symbols}))
                    await ws.send(json.dumps({'op': 'subscribe', 'args': new}))
                    active_symbols = new
                    last_rotate = time.time()
                    print('Symbols rotated')

                if 'data' not in data:
                    continue

                topic = data.get('topic', '')
                tf = '1' if '.1.' in topic else '5'

                for k in data['data']:
                    sym = k.get('symbol', '').lower()
                    if not sym: continue

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
                            # FIX 3: cooldown 4h per symbol
                            if now - last_signal_time.get(sym, 0) > 14400:
                                last_signal_time[sym] = now
                                await bot.send_message(chat_id=CHAT_ID, text=signal_text(res))
                                open_trades[res['symbol']] = res

                        # check open trades
                        trade = open_trades.get(sym.upper())
                        if trade:
                            cur = float(k.get('close', 0))
                            if cur >= trade['tp1']:
                                await bot.send_message(chat_id=CHAT_ID, text=tp_hit_text(sym.upper(), cur, 1))
                                trade['sl'] = trade['price']
                                trade['tp1'] = trade['tp2']
                            elif cur >= trade['tp2']:
                                await bot.send_message(chat_id=CHAT_ID, text=tp_hit_text(sym.upper(), cur, 2))
                                del open_trades[sym.upper()]
                            elif cur <= trade['sl']:
                                await bot.send_message(chat_id=CHAT_ID, text=sl_hit_text(sym.upper(), cur))
                                del open_trades[sym.upper()]

    except asyncio.CancelledError: return
    except Exception as e:
        print('WS ERR: ' + str(e))
        await asyncio.sleep(5)
```

async def cmd_start(update, ctx):
await update.message.reply_text(
‘Beast Mode Bot - Bybit\n\n’
‘/status - Bot status\n’
‘/trades - Open trades\n’
‘/top - Top performing symbols\n’
‘/help - Help’
)

async def cmd_status(update, ctx):
await update.message.reply_text(
‘Status: Running\n’
’Symbols: ’ + str(len(ALL_SYMBOLS)) + ‘\n’
’Streams: ’ + str(len(klines)) + ‘\n’
’Signals sent: ’ + str(len(last_signal_time)) + ‘\n’
’Open trades: ’ + str(len(open_trades))
)

async def cmd_trades(update, ctx):
if not open_trades:
await update.message.reply_text(‘No open trades right now.’)
return
lines = [‘Open Trades:’]
for sym, t in open_trades.items():
lines.append(sym + ’ | Entry: $’ + fmt(t[‘price’]) + ’ | TP1: $’ + fmt(t[‘tp1’]) + ’ | SL: $’ + fmt(t[‘sl’]))
await update.message.reply_text(’\n’.join(lines))

async def cmd_top(update, ctx):
ranked = sorted(ALL_SYMBOLS, key=lambda s: scoreboard[s], reverse=True)[:10]
lines = [‘Top 10 symbols by score:’]
for i, sym in enumerate(ranked, 1):
lines.append(str(i) + ‘. ’ + sym + ’ - ’ + ‘{:.1f}’.format(scoreboard[sym]))
await update.message.reply_text(’\n’.join(lines))

async def cmd_help(update, ctx):
await update.message.reply_text(
‘Beast Mode Bot\n\n’
‘Indicators: EMA + MACD + RSI + BB + ATR + Volume\n’
‘Timeframes: 1m + 5m\n’
‘Auto TP1/TP2/SL alerts\n’
‘Smart symbol rotation every 10min\n\n’
‘Educational use only.’
)

async def main():
print(‘Initializing…’)
await start_health()

```
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler('start', cmd_start))
app.add_handler(CommandHandler('status', cmd_status))
app.add_handler(CommandHandler('trades', cmd_trades))
app.add_handler(CommandHandler('top', cmd_top))
app.add_handler(CommandHandler('help', cmd_help))

await app.initialize()
await app.start()
bot = app.bot

await bot.send_message(
    chat_id=CHAT_ID,
    text=(
        'Beast Mode Active!\n'
        'Symbols: ' + str(len(ALL_SYMBOLS)) + '\n'
        'TF: 1m + 5m\n'
        'Min score: 55%\n'
        'Cooldown: 4h per symbol\n\n'
        'Commands: /status /trades /top /help'
    )
)

stop = asyncio.Event()
await asyncio.gather(
    ws_loop(bot, stop),
    app.updater.start_polling(drop_pending_updates=True),
)
```

if **name** == ‘**main**’:
asyncio.run(main())
