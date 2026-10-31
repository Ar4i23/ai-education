import asyncio

from aiogram import Bot
from config import BOT_TOKEN


async def main():
    bot = Bot(token=BOT_TOKEN)

    try:
        updates = await bot.get_updates(
            limit=100,
            timeout=0,
            allowed_updates=["channel_post"],
        )

        print(f"Получено обновлений: {len(updates)}")

        for update in updates:
            print(f"Тип обновления: {update.event_type}")

            if update.channel_post:
                chat = update.channel_post.chat
                print(f"Название канала: {chat.title}")
                print(f"CHAT_ID: {chat.id}")

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
