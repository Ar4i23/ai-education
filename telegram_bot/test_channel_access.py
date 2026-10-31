import asyncio
import os

from aiogram import Bot
from dotenv import load_dotenv
from config import BOT_TOKEN

load_dotenv()

CHANNEL_ID = int(os.environ["COURSE_CHANNEL_ID"])


async def main():
    bot = Bot(token=BOT_TOKEN)

    try:
        me = await bot.get_me()
        chat = await bot.get_chat(CHANNEL_ID)
        member = await bot.get_chat_member(CHANNEL_ID, me.id)

        print(f"Бот: @{me.username}")
        print(f"Канал: {chat.title}")
        print(f"CHAT_ID: {chat.id}")
        print(f"Статус бота: {member.status}")

        can_invite = getattr(member, "can_invite_users", False)
        print(f"Право приглашать: {can_invite}")

        if can_invite:
            invite = await bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                name="Проверка доступа",
                member_limit=1,
            )
            print("Тестовая ссылка успешно создана.")
            await bot.revoke_chat_invite_link(
                chat_id=CHANNEL_ID,
                invite_link=invite.invite_link,
            )
            print("Тестовая ссылка отозвана.")
        else:
            print("Боту необходимо разрешить приглашать пользователей.")

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
