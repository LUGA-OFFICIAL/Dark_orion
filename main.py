import os
import asyncio
from aiohttp import web
from telegram.ext import Application

print("BOT OK")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

# ================= HEALTH =================
async def health(request):
    return web.Response(text="OK")

async def start_health():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"Health server running on {PORT}")

# ================= MAIN =================
async def main():
    await start_health()

    app = Application.builder().token(BOT_TOKEN).build()

    await app.initialize()
    await app.start()

    print("BOT RUNNING")

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text="🔥 Beast Mode Active!"
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
