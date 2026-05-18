# ================= MAIN =================
async def main():

    await start_health()

    app = Application.builder().token(BOT_TOKEN).build()

    await app.initialize()

    await app.start()

    print("✅ BOT RUNNING")

    # ================= GROUP TEST =================
    try:

        me = await app.bot.get_me()

        print(
            "BOT USERNAME:",
            me.username
        )

        chat = await app.bot.get_chat(
            GROUP_CHAT_ID
        )

        print(
            "GROUP FOUND:",
            chat.title
        )

        await app.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="✅ GROUP TEST"
        )

        print("GROUP OK")

    except Exception as e:

        print(
            "GROUP ERROR:",
            e
        )

    # ================= START WS =================
    asyncio.create_task(
        ws_loop(app.bot)
    )

    await asyncio.Event().wait()

if __name__ == "__main__":

    asyncio.run(main())
