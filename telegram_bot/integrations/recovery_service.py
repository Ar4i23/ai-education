"""Safe course-access recovery via Telegram."""

import logging

from aiogram.enums import ChatMemberStatus

from integrations.access_recovery import (
    reserve_recovery,
    begin_recovery_invite,
    complete_recovery,
    mark_recovery_delivered,
    mark_recovery_review,
)
from integrations.telegram_access import create_course_invite_with_expiry

logger = logging.getLogger(__name__)


async def recover_course_access(bot, channel_id: int, telegram_id: int):
    """Return a machine-readable recovery result."""

    if (
        not isinstance(channel_id, int)
        or isinstance(channel_id, bool)
        or channel_id == 0
    ):
        return "configuration_error"

    # Do not issue a new invitation when membership cannot be verified.
    try:
        member = await bot.get_chat_member(
            chat_id=channel_id,
            user_id=telegram_id,
        )
    except Exception:
        logger.exception("Unable to verify course membership")
        return "membership_check_failed"

    if member.status in (
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    ):
        return "already_member"

    if member.status == ChatMemberStatus.RESTRICTED and getattr(
        member, "is_member", False
    ):
        return "already_member"

    if member.status not in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
        ChatMemberStatus.RESTRICTED,
    ):
        return "membership_check_failed"

    # Never automatically bypass an explicit Telegram ban.
    if member.status == ChatMemberStatus.KICKED:
        return "banned"

    status, recovery_id, token = reserve_recovery(telegram_id)

    if status != "reserved":
        return status

    if not begin_recovery_invite(recovery_id, token):
        return "needs_review"

    # From this point, an uncertain Telegram result must not be retried.
    try:
        invite_link, expires_at = await create_course_invite_with_expiry(
            bot,
            channel_id,
            recovery_id,
        )
    except Exception:
        logger.exception("Recovery invite creation failed")
        mark_recovery_review(recovery_id, token, "invite_creation_uncertain")
        return "needs_review"

    if not complete_recovery(recovery_id, token, invite_link, expires_at):
        mark_recovery_review(recovery_id, token, "invite_storage_failed")
        return "needs_review"

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                "🔓 Доступ к обучению восстановлен!\n\n"
                "Вот новая персональная ссылка:\n"
                f"{invite_link}\n\n"
                "Ссылка действует 24 часа. После перехода "
                "отправь заявку на вступление. Бот проверит "
                "твой Telegram-аккаунт и одобрит заявку.\n\n"
                "Приятного обучения!"
            ),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Recovery invitation delivery uncertain")
        mark_recovery_review(recovery_id, token, "message_delivery_uncertain")
        return "needs_review"

    if not mark_recovery_delivered(recovery_id, token):
        logger.error(
            "Recovery %s sent but delivery status was not saved",
            recovery_id,
        )
        return "needs_review"

    return "delivered"
