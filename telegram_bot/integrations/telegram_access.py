"""Telegram course access invitations requiring approval of join requests."""

from datetime import datetime, timedelta, timezone

from aiogram import Bot


async def create_course_invite_with_expiry(
    bot: Bot,
    channel_id: int,
    order_id: int,
) -> tuple[str, datetime]:
    """Create a 24-hour join-request invitation."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    invite = await bot.create_chat_invite_link(
        chat_id=channel_id,
        name=f"course-order-{order_id}",
        expire_date=expires_at,
        creates_join_request=True,
    )

    return invite.invite_link, expires_at


async def create_course_invite(
    bot: Bot,
    channel_id: int,
    order_id: int,
) -> str:
    """Backwards-compatible helper returning only the invitation URL."""
    link, _ = await create_course_invite_with_expiry(
        bot,
        channel_id,
        order_id,
    )
    return link


async def send_course_invite(
    bot: Bot,
    telegram_id: int,
    invite_link: str,
) -> None:
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "🎉 Оплата курса подтверждена!\n\n"
            "Теперь тебе доступно обучение «ИИ-мультики с нуля».\n\n"
            "🔗 Персональная ссылка:\n"
            f"{invite_link}\n\n"
            "Ссылка действует 24 часа.\n"
            "После перехода отправь заявку на вступление.\n"
            "Бот проверит твой Telegram-аккаунт и предоставит доступ.\n\n"
            "Не передавай ссылку другим людям."
        ),
        disable_web_page_preview=True,
    )
