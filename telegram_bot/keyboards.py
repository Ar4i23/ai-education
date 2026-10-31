"""Inline keyboards for the AI Education Telegram bot."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить курс", callback_data="pay")],
            [
                InlineKeyboardButton(
                    text="🎟 У меня есть промокод", callback_data="enter_promo"
                )
            ],
            [InlineKeyboardButton(text="📄 Юридическая информация", callback_data="legal_info")],
        ]
    )


def get_promo_keyboard() -> InlineKeyboardMarkup:
    """Allow going back only while entering a promo code."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Вернуться назад", callback_data="back_to_start"
                )
            ],
        ]
    )


def get_promo_result_keyboard() -> InlineKeyboardMarkup:
    """Once a promo is saved, the next step is checkout."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить через Robokassa", callback_data="pay_with_promo"
                )
            ],
        ]
    )


def get_question_keyboard(
    support_username: str, payment_id: str
) -> InlineKeyboardMarkup:
    """Legacy payment-support navigation, kept for compatibility."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать в поддержку",
                    url=f"https://t.me/{support_username}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад к оплате",
                    callback_data=f"back_to_payment:{payment_id}",
                )
            ],
        ]
    )
