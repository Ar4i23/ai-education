"""Conservative one-task worker; importing does not start it."""

import logging

from aiogram.exceptions import TelegramBadRequest

from integrations.access_delivery import (
    claim_next_delivery,
    begin_invite_creation,
    save_delivery_invite,
    mark_delivery_success,
)

from integrations.telegram_access import (
    create_course_invite_with_expiry,
    send_course_invite,
)

from integrations.payment_screen_store import (
    get_payment_screen,
    forget_payment_screen,
)

logger = logging.getLogger(__name__)


async def process_one_delivery(bot, channel_id: int) -> str:
    """Handle one task; uncertain Telegram outcomes require manual review."""

    if (
        not isinstance(channel_id, int)
        or isinstance(channel_id, bool)
        or channel_id == 0
    ):
        raise ValueError("A valid Telegram channel ID is required")

    task = claim_next_delivery()

    if task is None:
        return "empty"

    order_id = task["order_id"]
    claimed_at = task["claimed_at"]
    token = task["claim_token"]

    if task.get("invite_link") or task.get("invite_started_at"):
        return "needs_review"

    # Prevent automatic duplicate invitation creation.
    if not begin_invite_creation(order_id, claimed_at, token):
        return "needs_review"

    try:
        invite_link, expires_at = await create_course_invite_with_expiry(
            bot,
            channel_id,
            order_id,
        )
    except Exception as exc:
        logger.error("Delivery uncertain: order=%s stage=invite_creation error=%s", order_id, type(exc).__name__)
        return "needs_review"

    try:
        saved = save_delivery_invite(
            order_id,
            claimed_at,
            token,
            invite_link,
            expires_at.isoformat(),
        )
    except Exception as exc:
        logger.error("Delivery uncertain: order=%s stage=invite_storage error=%s", order_id, type(exc).__name__)
        return "needs_review"

    if not saved:
        return "needs_review"

    try:
        await send_course_invite(
            bot,
            task["telegram_id"],
            invite_link,
        )
    except Exception as exc:
        logger.error("Delivery uncertain: order=%s stage=message_delivery error=%s", order_id, type(exc).__name__)
        return "needs_review"

    if not mark_delivery_success(
        order_id,
        claimed_at,
        token,
        invite_link,
    ):
        return "needs_review"

    # Delete the previous checkout screen only after delivery.
    try:
        screen = get_payment_screen(order_id)

        if screen is not None:
            chat_id, message_id = screen

            if chat_id == task["telegram_id"]:
                try:
                    await bot.delete_message(
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                except TelegramBadRequest as exc:
                    logger.warning(
                        "Checkout message %s not deleted: %s",
                        message_id,
                        exc,
                    )
                else:
                    forget_payment_screen(order_id)

    except Exception:
        logger.exception(
            "Checkout cleanup failed for order %s",
            order_id,
        )

    return "delivered"
