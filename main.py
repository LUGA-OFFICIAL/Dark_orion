import sys,os,re,asyncio,json,time,hashlib,aiohttp,feedparser
import pandas as pd,ta,websockets
from collections import defaultdict,deque
from aiohttp import web
from telegram.ext import Application,CommandHandler

print(“BOT OK”)

BOT_TOKEN=os.getenv(“BOT_TOKEN”)
CHAT_ID=int(os.getenv(“CHAT_ID”,“0”))
PORT=int(os.getenv(“PORT”,“8080”))
MIN_SCORE=55
COOLDOWN=14400
NEW_SL_PCT=0.08
MIN_VOL=500000

ALL_SYMBOLS=[“BTCUSDT”,“ETHUSDT”,“SOLUSDT”,“BNBUSDT”,“XRPUSDT”,“ADAUSDT”,“DOGEUSDT”,“AVAXUSDT”,“LINKUSDT”,“MATICUSDT”,“ARBUSDT”,“OPUSDT”,“INJUSDT”,“SUIUSDT”,“SEIUSDT”,“APTUSDT”,“NEARUSDT”,“ATOMUSDT”,“TRXUSDT”,“LTCUSDT”,“ETCUSDT”,“FILUSDT”,“ICPUSDT”,“HBARUSDT”,“ALGOUSDT”,“DOTUSDT”,“UNIUSDT”,“AAVEUSDT”,“MKRUSDT”]
LISTING_KW=[“will list”,“listing”,“new listing”,“will be listed”,“spot listing”]
EXCLUDE={“USDT”,“USD”,“BTC”,“ETH”,“BNB”,“UTC”,“GMT”,“API”,“NFT”,“BUSD”,“FDUSD”,“TUSD”,“USDC”,“DAI”}

klines=defaultdict(lambda:deque(maxlen=300))
scoreboard=defaultdict(float)
last_sig={}
open_trades={}
known_syms=set()
seen_ann=set()

async def health(req):
return web.Response(text=“OK”)

async def start_health():
a=web.Application()
a.router.add_get(”/”,health)
r=web.AppRunner(a)
await r.setup()
await web.TCPSite(r,“0.0.0.0”,PORT).start()
print(“Health OK port “+str(PORT))

def fmt(p):
if p>=1000:return “{:,.2f}”.format(p)
if p>=1:return “{:.4f}”.format(p)
if p>=0.01:return “{:.5f}”.format(p)
return “{:.8f}”.format(p)

def build_df(key):
d=list(klines[key])
if len(d)<50:return None
df=pd.DataFrame(d,columns=[“t”,“o”,“h”,“l”,“c”,“v”])
for col in [“o”,“h”,“l”,“c”,“v”]:
df[col]=pd.to_numeric(df[col],errors=“coerce”)
return df.dropna()

def analyze(symbol):
try:
df1=build_df(symbol+”_1”)
df5=build_df(symbol+”_5”)
if df1 is None or df5 is None:return None
if len(df1)<50 or len(df5)<50:return None
score=0
price=df1[“c”].iloc[-1]
ema50=ta.trend.EMAIndicator(df5[“c”],50).ema_indicator()
ema200=ta.trend.EMAIndicator(df5[“c”],200).ema_indicator() if len(df5)>=200 else ema50
if ema50.iloc[-1]>ema200.iloc[-1]:score+=25
else:score-=10
macd=ta.trend.MACD(df5[“c”])
macd_bull=macd.macd().iloc[-1]>macd.macd_signal().iloc[-1]
if macd_bull:score+=20
bb=ta.volatility.BollingerBands(df5[“c”])
bb_l=bb.bollinger_lband().iloc[-1]
bb_h=bb.bollinger_hband().iloc[-1]
bb_rng=bb_h-bb_l
bb_pos=(df5[“c”].iloc[-1]-bb_l)/bb_rng if bb_rng>0 else 0.5
if bb_pos<0.25:score+=15
elif bb_pos>0.80:score-=10
rsi=ta.momentum.RSIIndicator(df1[“c”],14).rsi().iloc[-1]
if rsi<35:score+=20
elif rsi>65:score-=15
elif rsi>52:score+=10
high15=df1[“h”].rolling(15).max().iloc[-2]
breakout=price>high15
if breakout:score+=15
vol_now=df1[“v”].iloc[-1]
vol_avg=df1[“v”].rolling(20).mean().iloc[-1]
vol_spk=vol_now>vol_avg*1.5
if vol_spk:score+=20
recent_high=df1[“h”].rolling(20).max().iloc[-2]
sweep=df1[“h”].iloc[-1]>recent_high and price<recent_high
if sweep:score+=20
cs=df1[“h”].iloc[-1]-df1[“l”].iloc[-1]
avg_cs=(df1[“h”]-df1[“l”]).rolling(20).mean().iloc[-1]
if avg_cs>0 and cs>avg_cs*2.5:return None
if score<MIN_SCORE:return None
scoreboard[symbol.upper()]+=score*0.1
atr=ta.volatility.AverageTrueRange(df1[“h”],df1[“l”],df1[“c”]).average_true_range().iloc[-1]
if score>=80:tm,t2m,sm,grade=2.0,3.5,1.2,“HIGH”
elif score>=65:tm,t2m,sm,grade=1.5,2.5,1.0,“MEDIUM”
else:tm,t2m,sm,grade=1.0,1.8,0.8,“SCALP”
if sweep:sig=“Smart Money”
elif breakout and vol_spk:sig=“Breakout+Vol”
elif rsi<35:sig=“Oversold”
else:sig=“Momentum”
tp1=price+atr*tm
tp2=price+atr*t2m
sl=price-atr*sm
t1p=round(abs((tp1-price)/price*100),2)
t2p=round(abs((tp2-price)/price*100),2)
slp=round(abs((sl-price)/price*100),2)
rr=round(t1p/slp,1) if slp>0 else 0
return dict(symbol=symbol.upper(),price=price,tp1=tp1,tp2=tp2,sl=sl,score=score,grade=grade,sig=sig,rsi=round(rsi,1),t1p=t1p,t2p=t2p,slp=slp,rr=rr)
except Exception as e:
print(“ERR: “+str(e))
return None

def signal_txt(r):
note=“Strong” if r[“grade”]==“HIGH” else “Medium” if r[“grade”]==“MEDIUM” else “Scalp”
return chr(10).join([“BUY [”+r[“grade”]+”] “+r[“symbol”],r[“sig”]+” | Score: “+str(r[“score”])+”%”,note,””,“Entry: $”+fmt(r[“price”]),“TP1:   $”+fmt(r[“tp1”])+” (+”+str(r[“t1p”])+”%)”,“TP2:   $”+fmt(r[“tp2”])+” (+”+str(r[“t2p”])+”%)”,“SL:    $”+fmt(r[“sl”])+” (-”+str(r[“slp”])+”%)”,“RR:    1:”+str(r[“rr”]),””,“RSI: “+str(r[“rsi”])+” | TF: 1m+5m | Bybit”,“Educational use only”])

def listing_txt(sym,price,vol):
sl=price*(1-NEW_SL_PCT);tp1=price*1.15;tp2=price*1.30;tp3=price*1.50
return chr(10).join([“NEW LISTING: “+sym,“Price: $”+fmt(price),“Vol: $”+”{:,.0f}”.format(vol),””,“Entry: $”+fmt(price),“TP1: $”+fmt(tp1)+” (+15%)”,“TP2: $”+fmt(tp2)+” (+30%)”,“TP3: $”+fmt(tp3)+” (+50%)”,“SL:  $”+fmt(sl)+” (-8%)”,””,“40% TP1 | 40% TP2 | 20% TP3”,“HIGH RISK!”])

def ann_txt(title,link,syms,src):
coins=” / “.join(syms) if syms else “check link”
return chr(10).join([“EARLY ALERT - Upcoming Listing!”,“Source: “+src,“Coin: “+coins,””,title,””,“Prepare before listing!”,link])

def select_top():
ranked=sorted(ALL_SYMBOLS,key=lambda s:scoreboard[s],reverse=True)[:25]
return [“kline.1.”+s for s in ranked]+[“kline.5.”+s for s in ranked]

async def ws_loop(bot,stop):
url=“wss://stream.bybit.com/v5/public/spot”
active=[]
while not stop.is_set():
try:
async with websockets.connect(url,ping_interval=20,ping_timeout=30) as ws:
print(“WS connected”)
active=select_top()
await ws.send(json.dumps({“op”:“subscribe”,“args”:active}))
last_rot=time.time()
async for raw in ws:
if stop.is_set():return
data=json.loads(raw)
if time.time()-last_rot>600:
new=select_top()
await ws.send(json.dumps({“op”:“unsubscribe”,“args”:active}))
await ws.send(json.dumps({“op”:“subscribe”,“args”:new}))
active=new;last_rot=time.time()
if “data” not in data:continue
topic=data.get(“topic”,””)
tf=“1” if “.1.” in topic else “5”
for k in data[“data”]:
sym=k.get(“symbol”,””).lower()
if not sym:continue
klines[sym+”_”+tf].append([k.get(“start”),float(k.get(“open”,0)),float(k.get(“high”,0)),float(k.get(“low”,0)),float(k.get(“close”,0)),float(k.get(“volume”,0))])
if tf==“1”:
res=analyze(sym)
if res:
now=time.time()
if now-last_sig.get(sym,0)>COOLDOWN:
last_sig[sym]=now
await bot.send_message(chat_id=CHAT_ID,text=signal_txt(res))
open_trades[res[“symbol”]]=res
trade=open_trades.get(sym.upper())
if trade:
cur=float(k.get(“close”,0))
if cur>=trade[“tp2”]:
await bot.send_message(chat_id=CHAT_ID,text=“TP2 HIT: “+sym.upper()+” $”+fmt(cur)+chr(10)+“Close position!”)
del open_trades[sym.upper()]
elif cur>=trade[“tp1”]:
await bot.send_message(chat_id=CHAT_ID,text=“TP1 HIT: “+sym.upper()+” $”+fmt(cur)+chr(10)+“Move SL to entry!”)
open_trades[sym.upper()][“sl”]=trade[“price”]
elif cur<=trade[“sl”]:
await bot.send_message(chat_id=CHAT_ID,text=“SL HIT: “+sym.upper()+” $”+fmt(cur)+chr(10)+“Trade closed.”)
del open_trades[sym.upper()]
except asyncio.CancelledError:return
except Exception as e:
print(“WS ERR: “+str(e))
await asyncio.sleep(5)

async def check_listings(bot,stop):
global known_syms
while not stop.is_set():
try:
async with aiohttp.ClientSession() as s:
async with s.get(“https://api.bybit.com/v5/market/instruments-info?category=spot”,timeout=aiohttp.ClientTimeout(total=15)) as r:
data=await r.json()
items=data.get(“result”,{}).get(“list”,[])
cur={x[“symbol”] for x in items if x.get(“quoteCoin”)==“USDT” and x.get(“status”)==“Trading”}
if not known_syms:known_syms=cur
else:
new=cur-known_syms
for sym in new:
await asyncio.sleep(30)
try:
async with aiohttp.ClientSession() as s:
async with s.get(“https://api.bybit.com/v5/market/tickers?category=spot&symbol=”+sym) as r:
t=await r.json()
it=t.get(“result”,{}).get(“list”,[])
if it:
price=float(it[0].get(“lastPrice”,0))
vol=float(it[0].get(“volume24h”,0))*price
if price>0 and vol>=MIN_VOL:
await bot.send_message(chat_id=CHAT_ID,text=listing_txt(sym,price,vol))
except Exception as e:print(“Listing err: “+str(e))
if new:known_syms=cur
except Exception as e:print(“Check err: “+str(e))
await asyncio.sleep(300)

async def check_ann(bot,stop):
global seen_ann
sources=[(“https://announcements.bybit.com/rss/en-US/”,“Bybit”),(“https://nitter.net/Bybit_Official/rss”,“Twitter”)]
while not stop.is_set():
for url,src in sources:
try:
async with aiohttp.ClientSession() as s:
async with s.get(url,headers={“User-Agent”:“Mozilla/5.0”},timeout=aiohttp.ClientTimeout(total=15)) as r:
if r.status==200:
for e in feedparser.parse(await r.text()).entries[:10]:
title=e.get(“title”,””);link=e.get(“link”,””)
aid=hashlib.md5(link.encode()).hexdigest()
if aid in seen_ann:continue
if any(kw in title.lower() for kw in LISTING_KW):
syms=[x for x in re.findall(r”(([A-Z]{2,10}))”,title) if x not in EXCLUDE]
await bot.send_message(chat_id=CHAT_ID,text=ann_txt(title,link,syms,src),disable_web_page_preview=True)
seen_ann.add(aid)
except Exception as e:print(“RSS err: “+str(e))
await asyncio.sleep(120)

async def cmd_start(u,c): await u.message.reply_text(“Bot running!”+chr(10)+”/status /trades /top /help”)
async def cmd_status(u,c): await u.message.reply_text(“Running”+chr(10)+“Symbols: “+str(len(ALL_SYMBOLS))+chr(10)+“Signals: “+str(len(last_sig))+chr(10)+“Trades: “+str(len(open_trades)))
async def cmd_trades(u,c):
if not open_trades:await u.message.reply_text(“No open trades.”);return
lines=[“Open Trades:”]
for sym,t in open_trades.items():lines.append(sym+” $”+fmt(t[“price”])+” TP1:$”+fmt(t[“tp1”])+” SL:$”+fmt(t[“sl”]))
await u.message.reply_text(chr(10).join(lines))
async def cmd_top(u,c):
ranked=sorted(ALL_SYMBOLS,key=lambda s:scoreboard[s],reverse=True)[:10]
lines=[“Top 10:”]+[str(i)+”. “+s+” “+str(round(scoreboard[s],1)) for i,s in enumerate(ranked,1)]
await u.message.reply_text(chr(10).join(lines))
async def cmd_help(u,c): await u.message.reply_text(“Beast Mode | Bybit | 1m+5m”+chr(10)+“EMA+MACD+RSI+BB+ATR+Vol”+chr(10)+“Auto TP/SL alerts”+chr(10)+“New listing alerts”+chr(10)+“Educational use only.”)

async def main():
await start_health()
app=Application.builder().token(BOT_TOKEN).build()
for cmd,fn in [(“start”,cmd_start),(“status”,cmd_status),(“trades”,cmd_trades),(“top”,cmd_top),(“help”,cmd_help)]:
app.add_handler(CommandHandler(cmd,fn))
await app.initialize()
await app.start()
bot=app.bot
await bot.send_message(chat_id=CHAT_ID,text=“Beast Mode Active!”+chr(10)+“Symbols: “+str(len(ALL_SYMBOLS))+chr(10)+“TF: 1m+5m”+chr(10)+“Min score: “+str(MIN_SCORE)+”%”+chr(10)+“Cooldown: 4h”+chr(10)+chr(10)+”/status /trades /top /help”)
stop=asyncio.Event()
await asyncio.gather(ws_loop(bot,stop),check_listings(bot,stop),check_ann(bot,stop),app.updater.start_polling(drop_pending_updates=True))

if **name** == “**main**”:
asyncio.run(main())
