“””
╔══════════════════════════════════════════════════════════════╗
║         بوت تيليغرام للتداول — Binance — الكود الكامل      ║
║                                                              ║
║  pip install python-telegram-bot websockets pandas ta        ║
║              aiohttp feedparser                              ║
╚══════════════════════════════════════════════════════════════╝
“””

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

print(“🚨 BOT LOADED”)

# ══════════════════════════════════════════════════════════════

# 1. الإعدادات

# ══════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv(“BOT_TOKEN”, “ضع_التوكن_هنا”)
CHAT_ID   = int(os.getenv(“CHAT_ID”, “0”))
PORT      = int(os.getenv(“PORT”, “8080”))

# ── إعدادات التحليل ──────────────────────────────────────────

CONFIDENCE_MIN  = 75      # الحد الأدنى للثقة لإرسال السيجنال
SL_PCT          = 0.03    # وقف الخسارة 3%
TP_RATIO        = 2.5     # نسبة المكافأة / المخاطرة
NEW_LISTING_SL  = 0.08    # وقف خسارة العملات الجديدة 8%
MIN_VOLUME_USDT = 1_000_000  # حجم تداول أدنى مليون دولار

# ── الإطارات الزمنية ─────────────────────────────────────────

# 1m للتداول السريع، 15m للتأكيد

TF_FAST = “1m”
TF_SLOW = “15m”

# ── إعلانات بينانس ───────────────────────────────────────────

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

# ══════════════════════════════════════════════════════════════

# 2. الذاكرة — تخزين الشموع والحالة

# ══════════════════════════════════════════════════════════════

# كل عملة × كل إطار زمني → آخر 200 شمعة

klines: dict = defaultdict(lambda: deque(maxlen=200))

# العملات المعروفة (لاكتشاف الجديدة)

known_symbols: set = set()

# الإعلانات المشوفة

seen_announcements: set = set()

# السيجنالات المرسلة (لتجنب التكرار)

sent_signals: dict = {}

# ══════════════════════════════════════════════════════════════

# 3. Health Check Server لـ Railway

# ══════════════════════════════════════════════════════════════

async def health(request):
return web.Response(text=“OK ✅”)

async def start_health_server():
app_http = web.Application()
app_http.router.add_get(”/”, health)
runner = web.AppRunner(app_http)
await runner.setup()
site = web.TCPSite(runner, “0.0.0.0”, PORT)
await site.start()
print(f”✅ Health server على port {PORT}”)

# ══════════════════════════════════════════════════════════════

# 4. التحليل الفني

# ══════════════════════════════════════════════════════════════

def build_df(key: str) -> pd.DataFrame | None:
“”“يبني DataFrame من الشموع المخزنة”””
data = list(klines[key])
if len(data) < 60:
return None
df = pd.DataFrame(data, columns=[“t”,“o”,“h”,“l”,“c”,“v”])
for col in [“o”,“h”,“l”,“c”,“v”]:
df[col] = pd.to_numeric(df[col], errors=“coerce”)
return df.dropna()

def analyze(symbol: str) -> dict | None:
“””
يحلل العملة على إطارين زمنيين ويُرجع السيجنال أو None
“””
try:
df_fast = build_df(f”{symbol}*{TF_FAST}”)  # 1m
df_slow = build_df(f”{symbol}*{TF_SLOW}”)  # 15m

```
    if df_fast is None or df_slow is None:
        return None

    score = 0

    # ── تحليل الإطار البطيء (15m) — الاتجاه العام ────────
    ema50  = ta.trend.EMAIndicator(df_slow["c"], 50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df_slow["c"], 200).ema_indicator()
    macd   = ta.trend.MACD(df_slow["c"])
    bb     = ta.volatility.BollingerBands(df_slow["c"])

    trend_up   = ema50.iloc[-1] > ema200.iloc[-1]
    macd_bull  = macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]
    bb_range   = bb.bollinger_hband().iloc[-1] - bb.bollinger_lband().iloc[-1]
    bb_pos     = (df_slow["c"].iloc[-1] - bb.bollinger_lband().iloc[-1]) / bb_range if bb_range > 0 else 0.5

    if trend_up:   score += 2
    else:          score -= 2
    if macd_bull:  score += 2
    else:          score -= 2
    if bb_pos < 0.25: score += 1
    elif bb_pos > 0.75: score -= 1

    # ── تحليل الإطار السريع (1m) — نقطة الدخول ───────────
    rsi_fast = ta.momentum.RSIIndicator(df_fast["c"], 14).rsi()
    rsi      = rsi_fast.iloc[-1]

    # Breakout — السعر كسر أعلى نقطة خلال 20 شمعة
    high20   = df_fast["h"].rolling(20).max().iloc[-2]
    price    = df_fast["c"].iloc[-1]
    breakout = price > high20

    if rsi < 35:   score += 2
    elif rsi > 65: score -= 2
    if breakout:   score += 1

    # ── حجم التداول ────────────────────────────────────────
    vol_avg = df_fast["v"].rolling(20).mean().iloc[-1]
    vol_now = df_fast["v"].iloc[-1]
    high_vol = vol_now > vol_avg * 1.5
    if high_vol: score += 1

    # ── الثقة والقرار ───────────────────────────────────────
    confidence = min(95, max(40, 50 + score * 8))

    if score >= 4 and confidence >= CONFIDENCE_MIN:
        direction = "BUY"
    elif score <= -4 and confidence >= CONFIDENCE_MIN:
        direction = "SELL"
    else:
        return None

    # ── الأسعار ─────────────────────────────────────────────
    tp_pct = SL_PCT * TP_RATIO
    if direction == "BUY":
        tp = price * (1 + tp_pct)
        sl = price * (1 - SL_PCT)
    else:
        tp = price * (1 - tp_pct)
        sl = price * (1 + SL_PCT)

    return {
        "symbol":    symbol.upper(),
        "direction": direction,
        "price":     price,
        "tp":        tp,
        "sl":        sl,
        "conf":      int(confidence),
        "rsi":       round(rsi, 1),
        "macd_bull": macd_bull,
        "trend_up":  trend_up,
        "bb_pos":    round(bb_pos, 2),
        "high_vol":  high_vol,
    }

except Exception as e:
    print(f"⚠️ ANALYZE ERROR {symbol}: {e}")
    return None
```

# ══════════════════════════════════════════════════════════════

# 5. بناء رسائل التيليغرام

# ══════════════════════════════════════════════════════════════

def fmt_price(p: float) -> str:
“”“تنسيق السعر حسب حجمه”””
if p >= 1000:  return f”{p:,.2f}”
if p >= 1:     return f”{p:.4f}”
if p >= 0.01:  return f”{p:.5f}”
return f”{p:.8f}”

def build_signal_msg(s: dict) -> str:
side     = “شراء 🟢” if s[“direction”] == “BUY” else “بيع 🔴”
arrow    = “+” if s[“direction”] == “BUY” else “-”
tp_pct   = abs((s[“tp”] - s[“price”]) / s[“price”] * 100)
sl_pct   = abs((s[“sl”] - s[“price”]) / s[“price”] * 100)
rr       = tp_pct / sl_pct
now      = datetime.now().strftime(”%Y-%m-%d %H:%M”)

```
rsi_txt  = "تشبع بيعي ✅" if s["rsi"] < 35 else "تشبع شرائي ⚠️" if s["rsi"] > 65 else "محايد"
macd_txt = "صاعد ✅" if s["macd_bull"] else "هابط ⚠️"
ema_txt  = "صاعد ✅" if s["trend_up"]  else "هابط ⚠️"
vol_txt  = "عالٍ ✅" if s["high_vol"]  else "عادي"

return (
    f"🤖 *سيجنال {side}*\n"
    f"━━━━━━━━━━━━━━━━━━━━\n\n"
    f"💎 العملة: *{s['symbol']}/USDT*\n"
    f"⏱ الإطارين: {TF_FAST} + {TF_SLOW}\n"
    f"📊 الثقة: *{s['conf']}%*\n\n"
    f"━━━━━━━━━━━━━━━━━━━━\n"
    f"🎯 الدخول:   *${fmt_price(s['price'])}*\n"
    f"✅ الهدف:    *${fmt_price(s['tp'])}*  ({arrow}{tp_pct:.2f}%)\n"
    f"🛑 الستوب:  *${fmt_price(s['sl'])}*  (-{sl_pct:.2f}%)\n"
    f"⚖️ RR:       *1 : {rr:.1f}*\n\n"
    f"━━━━━━━━━━━━━━━━━━━━\n"
    f"📈 المؤشرات:\n"
    f"• RSI ({s['rsi']}): {rsi_txt}\n"
    f"• MACD: {macd_txt}\n"
    f"• EMA 50/200: {ema_txt}\n"
    f"• الحجم: {vol_txt}\n\n"
    f"⚠️ للتعليم فقط — ليس نصيحة مالية\n"
    f"🕐 {now}"
)
```

def build_new_listing_msg(symbol: str, price: float, volume: float) -> str:
sl   = price * (1 - NEW_LISTING_SL)
tp1  = price * 1.15
tp2  = price * 1.30
tp3  = price * 1.50
now  = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
return (
f”🚨 *إدراج جديد على Binance!* 🚨\n”
f”━━━━━━━━━━━━━━━━━━━━\n\n”
f”💎 العملة: *{symbol}*\n”
f”💰 سعر الإدراج: *${fmt_price(price)}*\n”
f”📊 حجم التداول: *${volume:,.0f}*\n\n”
f”━━━━━━━━━━━━━━━━━━━━\n”
f”🎯 الدخول:     *${fmt_price(price)}*\n”
f”✅ هدف 1 (TP1): *${fmt_price(tp1)}*  (+15%)\n”
f”✅ هدف 2 (TP2): *${fmt_price(tp2)}*  (+30%)\n”
f”✅ هدف 3 (TP3): *${fmt_price(tp3)}*  (+50%)\n”
f”🛑 الستوب:     *${fmt_price(sl)}*   (-8%)\n\n”
f”━━━━━━━━━━━━━━━━━━━━\n”
f”⚡ الاستراتيجية:\n”
f”• اخرج 40% عند TP1\n”
f”• اخرج 40% عند TP2\n”
f”• اترك 20% لـ TP3\n\n”
f”⚠️ عملات الإدراج عالية المخاطر!\n”
f”🕐 {now}”
)

def build_prelisting_msg(title: str, link: str, symbols: list, source: str) -> str:
coins = “ · “.join([f”*{s}*” for s in symbols]) if symbols else “راجع الإعلان”
now   = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
icon  = “🐦” if “تويتر” in source else “📢”
return (
f”⚡ *تنبيه مبكر — إدراج قادم!*\n”
f”━━━━━━━━━━━━━━━━━━━━\n\n”
f”{icon} المصدر: {source}\n”
f”📋 *{title}*\n\n”
f”💎 العملة المتوقعة: {coins}\n\n”
f”━━━━━━━━━━━━━━━━━━━━\n”
f”⏰ عندك وقت الآن:\n”
f”• ابحث عن المشروع\n”
f”• حدد كمية الاستثمار\n”
f”• جهّز أمر الشراء\n\n”
f”🔗 [الإعلان الرسمي]({link})\n”
f”⚠️ انتظر تأكيد الإدراج!\n”
f”🕐 {now}”
)

# ══════════════════════════════════════════════════════════════

# 6. WebSocket — استقبال البيانات المباشرة من Binance

# ══════════════════════════════════════════════════════════════

async def fetch_all_usdt_symbols() -> list[str]:
“”“جلب كل عملات USDT النشطة من Binance”””
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
print(f”✅ تم جلب {len(symbols)} عملة من Binance”)
return symbols
except Exception as e:
print(f”⚠️ خطأ في جلب العملات: {e}”)
# قائمة احتياطية
return [
“BTCUSDT”,“ETHUSDT”,“BNBUSDT”,“SOLUSDT”,“XRPUSDT”,
“ADAUSDT”,“DOGEUSDT”,“AVAXUSDT”,“LINKUSDT”,“SUIUSDT”,
“ALGOUSDT”,“HBARUSDT”,“DOTUSDT”,“MATICUSDT”,“UNIUSDT”,
]

async def ws_stream(bot, symbols: list[str], tf: str, stop_event: asyncio.Event):
“””
WebSocket stream لمجموعة عملات وإطار زمني واحد.
Binance يسمح بـ 200 stream في اتصال واحد.
“””
# بناء قائمة الـ streams
streams = [f”{s.lower()}@kline_{tf}” for s in symbols]

```
# Binance combined stream
BASE = "wss://stream.binance.com:9443/stream?streams="

while not stop_event.is_set():
    try:
        # نقسم العملات لمجموعات 200
        for i in range(0, len(streams), 200):
            chunk = streams[i:i+200]
            url   = BASE + "/".join(chunk)

            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=30,
                max_size=10 * 1024 * 1024,
            ) as ws:
                print(f"🔥 WS {tf} متصل ({len(chunk)} عملة)")

                async for raw in ws:
                    if stop_event.is_set():
                        return

                    msg  = json.loads(raw)
                    data = msg.get("data", {})
                    if data.get("e") != "kline":
                        continue

                    k      = data["k"]
                    symbol = k["s"].lower()
                    key    = f"{symbol}_{tf}"

                    klines[key].append([
                        k["t"],
                        float(k["o"]),
                        float(k["h"]),
                        float(k["l"]),
                        float(k["c"]),
                        float(k["v"]),
                    ])

                    # نحلل فقط على الإطار السريع (1m) لما الشمعة تكتمل
                    if tf == TF_FAST and k["x"]:  # x = شمعة مكتملة
                        sym_clean = k["s"].replace("USDT","").lower() + "usdt"
                        res = analyze(sym_clean)
                        if res:
                            sig_key = f"{res['symbol']}_{res['direction']}"
                            last    = sent_signals.get(sig_key, 0)
                            now_ts  = asyncio.get_event_loop().time()

                            # لا نرسل نفس السيجنال أكثر من مرة كل 4 ساعات
                            if now_ts - last > 14400:
                                sent_signals[sig_key] = now_ts
                                msg_text = build_signal_msg(res)
                                await bot.send_message(
                                    chat_id=CHAT_ID,
                                    text=msg_text,
                                    parse_mode="Markdown",
                                )

    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"⚠️ WS {tf} ERROR: {e} — إعادة في 5 ثواني")
        await asyncio.sleep(5)
```

# ══════════════════════════════════════════════════════════════

# 7. نظام اكتشاف العملات الجديدة

# ══════════════════════════════════════════════════════════════

async def check_new_listings(bot, stop_event: asyncio.Event):
“”“يفحص كل 5 دقائق إذا في عملة جديدة أُدرجت”””
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
            print(f"✅ تم حفظ {len(known_symbols)} عملة كقاعدة")
        else:
            new = current - known_symbols
            for sym in new:
                print(f"🆕 عملة جديدة: {sym}")
                await asyncio.sleep(30)  # انتظر 30 ثانية لبدء التداول
                await handle_new_listing(bot, sym)
            if new:
                known_symbols = current

    except Exception as e:
        print(f"⚠️ خطأ في فحص الإدراجات: {e}")

    await asyncio.sleep(300)  # كل 5 دقائق
```

async def handle_new_listing(bot, symbol: str):
“”“يجلب بيانات العملة الجديدة ويرسل السيجنال”””
try:
url = f”https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}”
async with aiohttp.ClientSession() as session:
async with session.get(url) as r:
t = await r.json()

```
    price  = float(t.get("lastPrice", 0))
    volume = float(t.get("quoteVolume", 0))

    if price <= 0 or volume < MIN_VOLUME_USDT:
        print(f"⚠️ {symbol} حجم منخفض ({volume:,.0f}$) — تجاهل")
        return

    msg = build_new_listing_msg(symbol, price, volume)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=msg,
        parse_mode="Markdown",
    )

except Exception as e:
    print(f"⚠️ خطأ في معالجة {symbol}: {e}")
```

# ══════════════════════════════════════════════════════════════

# 8. نظام مراقبة إعلانات Binance

# ══════════════════════════════════════════════════════════════

def is_listing(title: str) -> bool:
t = title.lower()
return any(kw in t for kw in LISTING_KEYWORDS)

def extract_symbols(text: str) -> list:
raw = re.findall(r’(([A-Z]{2,10}))’, text)
return [s for s in raw if s not in EXCLUDE_SYMBOLS]

async def fetch_rss(url: str) -> list:
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
print(f”⚠️ RSS ERROR ({url}): {e}”)
return []

async def monitor_announcements(bot, stop_event: asyncio.Event):
“”“يراقب إعلانات Binance الرسمية كل دقيقتين”””
global seen_announcements

```
while not stop_event.is_set():
    for url, source in [
        (BINANCE_RSS, "Binance الرسمي"),
        (NITTER_URL,  "تويتر Binance"),
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

    await asyncio.sleep(120)  # كل دقيقتين
```

# ══════════════════════════════════════════════════════════════

# 9. أوامر التيليغرام

# ══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“🤖 *مرحباً! أنا بوت التداول الذكي*\n\n”
“الأوامر:\n”
“/status — حالة البوت\n”
“/symbols — عدد العملات المراقبة\n”
“/help — المساعدة”,
parse_mode=“Markdown”,
)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
now = datetime.now().strftime(”%Y-%m-%d %H:%M:%S”)
total_klines = len(klines)
await update.message.reply_text(
f”✅ *حالة البوت*\n\n”
f”🟢 WebSocket 1m: يعمل\n”
f”🟢 WebSocket 15m: يعمل\n”
f”🟢 مراقبة الإدراجات: يعمل\n”
f”🟢 مراقبة الإعلانات: يعمل\n\n”
f”📊 البيانات المخزنة: {total_klines} إطار\n”
f”📨 سيجنالات مرسلة: {len(sent_signals)}\n”
f”🕐 {now}”,
parse_mode=“Markdown”,
)

async def cmd_symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
f”📋 *العملات المراقبة*\n\n”
f”البوت يراقب *كل عملات USDT* المتاحة على Binance\n”
f”العدد الحالي: *{len(known_symbols)}* عملة\n\n”
f”يتحدث تلقائياً كل 5 دقائق لاكتشاف الجديد.”,
parse_mode=“Markdown”,
)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“📖 *دليل الاستخدام*\n\n”
“البوت يعمل تلقائياً ويرسل:\n\n”
“1️⃣ *سيجنالات عادية*\n”
“   RSI + MACD + EMA + Bollinger\n”
“   على إطارين: 1m + 15m\n\n”
“2️⃣ *إدراجات جديدة*\n”
“   لحظة الإدراج + 3 أهداف\n\n”
“3️⃣ *تنبيهات مبكرة*\n”
“   من إعلانات Binance وتويتر\n\n”
“⚠️ للتعليم فقط، ليس نصيحة مالية.”,
parse_mode=“Markdown”,
)

# ══════════════════════════════════════════════════════════════

# 10. التشغيل الرئيسي

# ══════════════════════════════════════════════════════════════

async def main():
print(“🚀 جاري التشغيل…”)

```
# 1. Health server
await start_health_server()

# 2. جلب كل العملات
all_symbols = await fetch_all_usdt_symbols()

# 3. تهيئة البوت
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start",   cmd_start))
app.add_handler(CommandHandler("status",  cmd_status))
app.add_handler(CommandHandler("symbols", cmd_symbols))
app.add_handler(CommandHandler("help",    cmd_help))

await app.initialize()
await app.start()
bot = app.bot

# 4. رسالة تشغيل
await bot.send_message(
    chat_id=CHAT_ID,
    text=(
        f"✅ *البوت يعمل!*\n\n"
        f"📊 يراقب *{len(all_symbols)}* عملة\n"
        f"⏱ الإطارين: {TF_FAST} + {TF_SLOW}\n"
        f"🔍 الحد الأدنى للثقة: {CONFIDENCE_MIN}%\n"
        f"🛑 وقف الخسارة: {int(SL_PCT*100)}%\n"
        f"🎯 نسبة RR: 1:{TP_RATIO}"
    ),
    parse_mode="Markdown",
)

stop_event = asyncio.Event()

# 5. تشغيل كل الأنظمة معاً
await asyncio.gather(
    # WebSocket الإطار السريع (1m)
    ws_stream(bot, all_symbols, TF_FAST, stop_event),
    # WebSocket الإطار البطيء (15m)
    ws_stream(bot, all_symbols, TF_SLOW, stop_event),
    # مراقبة الإدراجات الجديدة
    check_new_listings(bot, stop_event),
    # مراقبة الإعلانات
    monitor_announcements(bot, stop_event),
    # Polling للأوامر
    app.updater.start_polling(drop_pending_updates=True),
)
```

if **name** == “**main**”:
asyncio.run(main())
