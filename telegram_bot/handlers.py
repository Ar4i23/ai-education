"""Handlers for the AI Education Telegram bot."""

import os
import asyncio
import logging
from integrations.recovery_service import recover_course_access
from integrations.admin_notifications import notify_admin
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import CURRENCY, PAYMENT_MODE, PRICE, ROBOKASSA_MODES, SUPPORT_USERNAME

from integrations.robokassa_checkout import checkout_url, checkout_ready
from integrations.payment_screen_store import remember_payment_screen

from database import (
    CourseAlreadyPurchasedError,
    calculate_discounted_price,
    get_latest_pending_order,
    get_or_create_pending_order,
    get_order,
    get_paid_order,
    get_promo_code,
    get_user_promo,
    has_legal_acceptance,
    record_legal_acceptance,
    save_user,
    save_user_promo,
)

from keyboards import (
    get_promo_keyboard,
    get_promo_result_keyboard,
    get_start_keyboard,
)

from messages import (
    PROMO_INPUT_MESSAGE,
    PROMO_INVALID_MESSAGE,
    PROMO_SUCCESS_MESSAGE,
    WELCOME_MESSAGE,
)


logger = logging.getLogger(__name__)
router = Router()

OFFER_URL = "https://antonovaai.ru/offer.html"
PRIVACY_URL = "https://antonovaai.ru/privacy.html"
LEGAL_ACCEPT_PREFIX = "legal_accept:"

DEMO_PAY_PREFIX = "demo_pay:"
DEMO_CHECK_PREFIX = "demo_check:"
DEMO_QUESTION_PREFIX = "demo_question:"


async def show_screen(
    bot: Any,
    telegram_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    state: FSMContext,
    callback: CallbackQuery | None = None,
) -> None:
    """Keep a single current bot screen, falling back if editing is impossible."""
    data = await state.get_data()
    message_id = data.get("screen_message_id")
    if callback is not None and callback.message is not None:
        message_id = callback.message.message_id
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=telegram_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            await state.update_data(screen_message_id=message_id)
            return
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info("Could not edit screen %s: %s", message_id, exc)
    sent = await bot.send_message(
        chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML"
    )
    await state.update_data(screen_message_id=sent.message_id)


def checkout_stage_keyboard(order_id: int, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Открыть Robokassa", url=url)],
            [
                InlineKeyboardButton(
                    text="📄 Юридическая информация", callback_data="legal_info"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Вернуться назад", callback_data="back_to_start"
                )
            ],
        ]
    )


def legal_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📃 Публичная оферта", url=OFFER_URL)],
            [
                InlineKeyboardButton(
                    text="🔒 Политика персональных данных", url=PRIVACY_URL
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Ознакомлен(а), принимаю условия оферты",
                    callback_data=f"{LEGAL_ACCEPT_PREFIX}{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Вернуться назад", callback_data="back_to_start"
                )
            ],
        ]
    )


def legal_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📃 Публичная оферта", url=OFFER_URL)],
            [
                InlineKeyboardButton(
                    text="🔒 Политика персональных данных", url=PRIVACY_URL
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 В главное меню", callback_data="back_to_start"
                )
            ],
        ]
    )


class PromoStates(StatesGroup):
    waiting_for_code = State()
    promo_applied = State()


def format_price(amount: str) -> str:
    value = Decimal(amount).quantize(Decimal("0.01"))
    return format(value, "f")


def promo_price_message(
    promo: dict[str, Any],
    restored: bool = False,
) -> str:
    """Display the current price for a validated promo."""

    discounted_price = calculate_discounted_price(
        PRICE,
        promo["discount_amount"],
    )

    message = PROMO_SUCCESS_MESSAGE.format(
        promo_code=escape(str(promo["code"])),
        original_price=escape(format_price(PRICE)),
        discounted_price=escape(discounted_price),
        currency=escape(CURRENCY),
    )

    if restored:
        message = (
            "💾 <b>Мы сохранили твой промокод!</b>\n"
            "Повторно вводить его не нужно.\n\n" + message
        )

    return message


def paid_order_message(order: dict[str, Any]) -> str:
    """Message shown to a user who has already purchased the course."""

    return (
        "✅ <b>Курс уже оплачен!</b>\n\n"
        f"📦 Номер оплаченного заказа: <b>№{order['id']}</b>\n\n"
        "🎓 Доступ к обучению уже оформлен.\n"
        "Повторно оплачивать курс не нужно.\n\n"
        "Если ты потерял приглашение в закрытый канал "
        "или вышел из него, обратись в поддержку "
        "для восстановления доступа."
    )


def paid_order_keyboard() -> InlineKeyboardMarkup:
    """Show automatic recovery and optional support."""

    rows = [
        [
            InlineKeyboardButton(
                text="🔓 Восстановить доступ",
                callback_data="recover_course_access",
            )
        ]
    ]

    if SUPPORT_USERNAME:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💬 Связаться с поддержкой",
                    url=f"https://t.me/{SUPPORT_USERNAME}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="📄 Юридическая информация", callback_data="legal_info"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_paid_screen(
    bot: Any,
    telegram_id: int,
    state: FSMContext,
) -> bool:
    """Restore purchased-course status from SQLite."""

    order = get_paid_order(telegram_id)

    if order is None:
        return False

    await state.clear()

    await bot.send_message(
        chat_id=telegram_id,
        text=paid_order_message(order),
        reply_markup=paid_order_keyboard(),
        parse_mode="HTML",
    )

    return True


def demo_order_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Legacy fallback: no separate order confirmation step."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить через Robokassa",
                    callback_data=f"{DEMO_PAY_PREFIX}{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Вернуться назад", callback_data="back_to_start"
                )
            ],
        ]
    )


def demo_order_message(order: dict[str, Any]) -> str:
    """Show an order summary."""

    promo_line = ""

    if order["promo_code"]:
        promo_line = f"🎟 Промокод: <b>{escape(str(order['promo_code']))}</b>\n"

    return (
        f"📦 <b>Заказ №{order['id']}</b>\n\n"
        f"{promo_line}"
        f"💰 К оплате: <b>{escape(str(order['amount']))} "
        f"{escape(str(order['currency']))}</b>\n"
        "📋 Статус: <b>Ожидает оплаты</b>\n\n"
        "Нажми «Перейти к оплате», "
        "чтобы продолжить оформление."
    )


def get_user_order(
    callback_data: str,
    prefix: str,
    telegram_id: int,
) -> dict[str, Any] | None:
    """Retrieve an order only when it belongs to this Telegram user."""

    if not callback_data.startswith(prefix):
        return None

    raw_id = callback_data[len(prefix) :]

    if not raw_id.isdecimal():
        return None

    order_id = int(raw_id)

    if order_id <= 0:
        return None

    order = get_order(order_id)

    if order is None or order["telegram_id"] != telegram_id:
        return None

    return order


async def show_payment_screen(
    bot: Any,
    telegram_id: int,
    order: dict[str, Any],
    state: FSMContext,
    callback: CallbackQuery | None = None,
) -> None:
    """Go directly to Robokassa, without a redundant order screen."""
    if PAYMENT_MODE not in ROBOKASSA_MODES:
        await show_screen(
            bot,
            telegram_id,
            demo_order_message(order),
            demo_order_keyboard(order["id"]),
            state,
            callback,
        )
        return
    if not has_legal_acceptance(order["id"], telegram_id):
        text = (
            f"📋 <b>Перед оплатой заказа №{order['id']}</b>\n\n"
            "Пожалуйста, ознакомьтесь с публичной офертой и политикой "
            "обработки персональных данных по кнопкам ниже.\n\n"
            "Нажимая «Принимаю условия оферты», вы подтверждаете "
            "ознакомление с документами и принятие условий договора. "
            "Политика объясняет порядок обработки данных."
        )
        await show_screen(
            bot, telegram_id, text, legal_keyboard(order["id"]), state, callback
        )
        return
    url = checkout_url(order)
    promo_line = (
        f"🎟 Промокод: <b>{escape(str(order['promo_code']))}</b>\n"
        if order.get("promo_code")
        else ""
    )
    text = (
        "💳 <b>Оплата через Robokassa</b>\n\n"
        "🎬 Курс «ИИ-мультики с нуля»\n"
        f"{promo_line}"
        f"💰 К оплате: <b>{escape(str(order['amount']))} "
        f"{escape(str(order['currency']))}</b>\n\n"
        + ("Тестовый режим: реальные деньги не списываются."
         if PAYMENT_MODE == "robokassa_test" else
         "После подтверждения оплаты доступ выдаётся автоматически.")
    )
    keyboard = checkout_stage_keyboard(order["id"], url)
    if order.get("promo_code"):
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard.inline_keyboard[:1])
    await show_screen(bot, telegram_id, text, keyboard, state, callback)
    # The delivery worker runs outside FSM; persist the message ID per order.
    screen_id = (await state.get_data()).get("screen_message_id")
    if screen_id is not None:
        remember_payment_screen(int(order["id"]), telegram_id, int(screen_id))


async def show_saved_screen(
    bot: Any,
    telegram_id: int,
    state: FSMContext,
) -> bool:
    """Restore the current checkout stage from SQLite."""

    # Paid status always has priority over pending orders.
    if await show_paid_screen(bot, telegram_id, state):
        return True

    if PAYMENT_MODE not in {"demo", *ROBOKASSA_MODES}:
        return False

    promo = get_user_promo(telegram_id)

    order = (
        get_latest_pending_order(
            telegram_id,
            promo_code=promo["code"],
        )
        if promo is not None
        else get_latest_pending_order(telegram_id)
    )

    if order is not None:
        await state.clear()

        await show_payment_screen(bot, telegram_id, order, state)

        return True

    if promo is None:
        return False

    try:
        promo_text = promo_price_message(
            promo,
            restored=True,
        )
    except ValueError:
        logger.warning(
            "Saved promo has invalid discount: %s",
            promo["code"],
        )
        return False

    await state.update_data(promo_code=promo["code"])
    await state.set_state(PromoStates.promo_applied)

    await show_screen(bot, telegram_id, promo_text, get_promo_result_keyboard(), state)

    return True


async def show_start_screen(
    message: Message,
    state: FSMContext,
) -> None:
    """Show paid access, pending checkout or welcome screen."""

    # A /start command always opens a fresh visible message.
    await state.clear()

    if message.from_user is None:
        return

    user = message.from_user

    save_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    if await show_saved_screen(
        message.bot,
        user.id,
        state,
    ):
        return

    await show_screen(
        message.bot,
        user.id,
        WELCOME_MESSAGE.format(price=PRICE, currency=CURRENCY),
        get_start_keyboard(),
        state,
    )


async def create_demo_order(
    callback: CallbackQuery,
    state: FSMContext,
    promo_code: str | None = None,
) -> None:
    """Create an order using the server-calculated price."""

    telegram_id = callback.from_user.id

    # Never offer another purchase to an existing customer.
    if get_paid_order(telegram_id) is not None:
        await callback.answer(
            "Курс уже оплачен. Повторная покупка не нужна.",
            show_alert=True,
        )

        await show_paid_screen(
            callback.bot,
            telegram_id,
            state,
        )
        return

    if PAYMENT_MODE not in {"demo", *ROBOKASSA_MODES}:
        await callback.answer(
            "⚠️ Реальная оплата пока не подключена.",
            show_alert=True,
        )
        return

    if PAYMENT_MODE in ROBOKASSA_MODES and not checkout_ready():
        await callback.answer(
            "Robokassa не настроена.",
            show_alert=True,
        )
        return

    saved_promo = get_user_promo(telegram_id)

    if saved_promo is not None:
        promo_code = saved_promo["code"]

        existing = get_latest_pending_order(
            telegram_id,
            promo_code=promo_code,
        )
    else:
        existing = get_latest_pending_order(telegram_id)

    if existing is not None:
        target_amount = format_price(PRICE)
        if saved_promo is not None:
            target_amount = calculate_discounted_price(
                PRICE, saved_promo["discount_amount"]
            )
        if Decimal(str(existing["amount"])) == Decimal(target_amount):
            await callback.answer()
            await show_payment_screen(
                callback.bot, telegram_id, existing, state, callback
            )
            return
        # Old pending order is retained for audit, but new price needs a new order.
        existing = None

    await callback.answer("Создаю заказ…")

    try:
        user = callback.from_user

        save_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        amount = format_price(PRICE)
        source = "direct"
        applied_code = None

        if promo_code is not None:
            promo = get_promo_code(promo_code)

            if promo is None:
                await state.clear()

                await callback.bot.send_message(
                    chat_id=user.id,
                    text=(
                        "❌ Промокод больше не действует.\n"
                        "Отправьте /start, чтобы продолжить."
                    ),
                )
                return

            amount = calculate_discounted_price(
                PRICE,
                promo["discount_amount"],
            )

            applied_code = promo["code"]
            source = promo["source"]

        # The database also checks paid status inside
        # a BEGIN IMMEDIATE transaction.
        order_id, created = get_or_create_pending_order(
            telegram_id=user.id,
            amount=amount,
            currency=CURRENCY,
            promo_code=applied_code,
            source=source,
        )

        order = get_order(order_id)

        if order is None:
            raise RuntimeError(f"Order #{order_id} not found")

        logger.info(
            "Order ready: order_id=%s, created=%s, user=%s, source=%s",
            order_id,
            created,
            user.id,
            source,
        )

    except CourseAlreadyPurchasedError:
        await show_paid_screen(
            callback.bot,
            telegram_id,
            state,
        )
        return

    except Exception:
        logger.exception("Failed to create order")

        await callback.bot.send_message(
            chat_id=telegram_id,
            text=(
                "⚠️ Не удалось создать заказ.\nПопробуйте ещё раз через /start."
            ),
        )
        return

    await state.clear()
    await show_payment_screen(callback.bot, telegram_id, order, state, callback)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
) -> None:
    await show_start_screen(message, state)


@router.callback_query(F.data == "legal_info")
async def callback_legal_info(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await show_screen(
        callback.bot,
        callback.from_user.id,
        "📄 <b>Юридическая информация Antonova AI</b>\n\n"
        "Документы доступны для чтения в любое время:",
        legal_info_keyboard(),
        state,
        callback,
    )


@router.callback_query(F.data.startswith(LEGAL_ACCEPT_PREFIX))
async def callback_legal_accept(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "")[len(LEGAL_ACCEPT_PREFIX) :]
    if not raw.isdecimal():
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    order = get_user_order(
        callback.data or "", LEGAL_ACCEPT_PREFIX, callback.from_user.id
    )
    if order is None or order["status"] != "pending":
        await callback.answer("Заказ недоступен. Нажмите /start", show_alert=True)
        return
    if not record_legal_acceptance(order["id"], callback.from_user.id):
        await callback.answer("Не удалось сохранить подтверждение", show_alert=True)
        return
    await callback.answer("Условия приняты")
    await show_payment_screen(
        callback.bot, callback.from_user.id, order, state, callback
    )


@router.callback_query(F.data == "back_to_start")
async def callback_back_to_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if get_paid_order(callback.from_user.id) is not None:
        await show_paid_screen(callback.bot, callback.from_user.id, state)
        return
    await state.set_state(None)
    await show_screen(
        callback.bot,
        callback.from_user.id,
        WELCOME_MESSAGE.format(price=PRICE, currency=CURRENCY),
        get_start_keyboard(),
        state,
        callback,
    )


@router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    telegram_id = callback.from_user.id

    if get_paid_order(telegram_id) is not None:
        await callback.answer(
            "Курс уже оплачен.",
            show_alert=True,
        )
        await show_paid_screen(
            callback.bot,
            telegram_id,
            state,
        )
        return

    if PAYMENT_MODE not in {"demo", *ROBOKASSA_MODES}:
        await callback.answer(
            "⚠️ Оформление заказов пока недоступно.",
            show_alert=True,
        )
        return

    # Check the saved promo before requesting another code.
    saved_promo = get_user_promo(telegram_id)

    if saved_promo is not None:
        await state.clear()
        await create_demo_order(
            callback,
            state,
            promo_code=saved_promo["code"],
        )
        return

    # No valid saved promo: request a new one.
    await state.clear()
    await state.set_state(PromoStates.waiting_for_code)
    await callback.answer()

    await show_screen(
        callback.bot,
        telegram_id,
        PROMO_INPUT_MESSAGE,
        get_promo_keyboard(),
        state,
        callback,
    )


@router.message(PromoStates.waiting_for_code, F.text)
async def handle_promo_input(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    if await show_paid_screen(
        message.bot,
        message.from_user.id,
        state,
    ):
        return

    if PAYMENT_MODE not in {"demo", *ROBOKASSA_MODES}:
        await state.clear()
        await message.answer("⚠️ Оформление заказов пока недоступно.")
        return

    code = message.text.strip().upper()

    if not code or len(code) > 32:
        promo = None
    else:
        promo = get_promo_code(code)

    if promo is None:
        # An invalid promo ends the input stage and restores the main menu.
        # Edit the tracked bot message instead of sending another one.
        await state.set_state(None)
        await show_screen(
            message.bot,
            message.from_user.id,
            PROMO_INVALID_MESSAGE
            + "\n\n"
            + WELCOME_MESSAGE.format(price=PRICE, currency=CURRENCY),
            get_start_keyboard(),
            state,
        )
        try:
            await message.delete()
        except Exception:
            logger.debug(
                "Could not delete invalid promo message %s", message.message_id
            )
        return

    try:
        result_text = promo_price_message(promo)
    except ValueError:
        logger.warning(
            "Invalid discount for promo %s",
            promo["code"],
        )

        await message.answer("⚠️ Не удалось рассчитать скидку. Обратитесь в поддержку.")
        return

    try:
        save_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

        saved = save_user_promo(
            telegram_id=message.from_user.id,
            code=promo["code"],
        )

    except Exception:
        logger.exception("Failed to save user promo")

        await message.answer("⚠️ Не удалось сохранить промокод. Попробуйте ещё раз.")
        return

    if not saved:
        await message.answer("⚠️ Промокод не удалось применить. Попробуйте ещё раз.")
        return

    await state.update_data(promo_code=promo["code"])
    await state.set_state(PromoStates.promo_applied)

    await show_screen(
        message.bot,
        message.from_user.id,
        result_text,
        get_promo_result_keyboard(),
        state,
    )
    try:
        await message.delete()
    except Exception:
        pass


@router.message(PromoStates.waiting_for_code)
async def handle_non_text_promo(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user is not None:
        if await show_paid_screen(
            message.bot,
            message.from_user.id,
            state,
        ):
            return

    await message.answer("🎟 Отправьте промокод обычным текстовым сообщением.")


@router.callback_query(F.data == "pay")
async def callback_pay(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Respect a saved discount even on an older full-price button."""

    promo = get_user_promo(callback.from_user.id)
    await state.clear()

    await create_demo_order(
        callback,
        state,
        promo_code=promo["code"] if promo else None,
    )


@router.callback_query(F.data == "pay_with_promo")
async def callback_pay_with_promo(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if get_paid_order(callback.from_user.id) is not None:
        await callback.answer(
            "Курс уже оплачен.",
            show_alert=True,
        )

        await show_paid_screen(
            callback.bot,
            callback.from_user.id,
            state,
        )
        return

    if PAYMENT_MODE not in {"demo", *ROBOKASSA_MODES}:
        await callback.answer(
            "⚠️ Реальная оплата пока не подключена.",
            show_alert=True,
        )
        return

    promo = get_user_promo(callback.from_user.id)

    if promo is None:
        await state.clear()

        await callback.answer(
            "Промокод отсутствует или больше не действует.",
            show_alert=True,
        )
        return

    await create_demo_order(
        callback,
        state,
        promo_code=promo["code"],
    )


@router.callback_query(F.data == "recover_course_access")
async def callback_recover_course_access(
    callback: CallbackQuery,
) -> None:
    """Restore access to the course for a paid user."""

    telegram_id = callback.from_user.id

    # Verify payment before attempting recovery.
    if get_paid_order(telegram_id) is None:
        await callback.answer(
            "Оплаченный заказ не найден.",
            show_alert=True,
        )
        return

    raw_channel_id = os.getenv("COURSE_CHANNEL_ID", "").strip()

    try:
        channel_id = int(raw_channel_id)

        if channel_id == 0:
            raise ValueError

    except ValueError:
        await callback.answer(
            "Восстановление временно недоступно.",
            show_alert=True,
        )
        return

    await callback.answer("Проверяю доступ к обучению…")

    try:
        result = await recover_course_access(
            callback.bot,
            channel_id,
            telegram_id,
        )

    except Exception:
        logger.exception(
            "Course recovery failed for user %s",
            telegram_id,
        )
        result = "needs_review"

    messages = {
        "delivered": (
            "✅ Новая ссылка отправлена!\n\n"
            "Проверь сообщения от бота. "
            "Ссылка действует 24 часа."
        ),
        "already_member": (
            "✅ Ты уже состоишь в закрытом канале!\n\nДоступ к обучению активен."
        ),
        "cooldown": (
            "⏳ Повторное восстановление пока недоступно.\n\n"
            "Попробуй через 24 часа или обратись "
            "в поддержку."
        ),
        "needs_review": (
            "⚠️ Запрос требует проверки.\n\n"
            "Автоматическое восстановление остановлено. "
            "Обратись в поддержку."
        ),
        "not_paid": ("❌ Оплаченный заказ не найден."),
        "banned": ("⚠️ Автоматическое восстановление недоступно. Обратись в поддержку."),
        "membership_check_failed": (
            "⚠️ Не удалось проверить участие в канале. Попробуй позже."
        ),
        "configuration_error": ("⚠️ Восстановление временно недоступно."),
        "invalid_user": ("⚠️ Не удалось определить пользователя."),
    }

    await callback.bot.send_message(
        chat_id=telegram_id,
        text=messages.get(
            result,
            "⚠️ Не удалось восстановить доступ. Обратись в поддержку.",
        ),
    )

    if result == "delivered":
        asyncio.create_task(notify_admin(
            f"🔄 Восстановление доступа: ссылка отправлена\n"
            f"Telegram ID: {telegram_id}\n"
            "Ожидается заявка на вступление"
        ))
    elif result == "needs_review":
        asyncio.create_task(notify_admin(
            f"⚠️ Восстановление требует проверки\n"
            f"Telegram ID: {telegram_id}"
        ))


@router.callback_query(F.data.startswith("checkout_back:"))
async def callback_checkout_back(callback: CallbackQuery, state: FSMContext) -> None:
    order = get_user_order(callback.data or "", "checkout_back:", callback.from_user.id)
    if order is None or order["status"] != "pending":
        await callback.answer(
            "Заказ больше недоступен. Открой /start.", show_alert=True
        )
        return
    await callback.answer()
    await show_screen(
        callback.bot,
        callback.from_user.id,
        WELCOME_MESSAGE.format(price=PRICE, currency=CURRENCY),
        get_start_keyboard(),
        state,
        callback,
    )


@router.callback_query(F.data.startswith(DEMO_PAY_PREFIX))
async def callback_demo_pay(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Open checkout only for a pending order of an unpaid user."""

    telegram_id = callback.from_user.id

    if get_paid_order(telegram_id) is not None:
        await callback.answer(
            "Курс уже оплачен. Повторная оплата недоступна.",
            show_alert=True,
        )
        return

    order = get_user_order(
        callback.data or "",
        DEMO_PAY_PREFIX,
        telegram_id,
    )

    if order is None or order["status"] != "pending":
        await callback.answer(
            "Заказ недоступен. Откройте /start.",
            show_alert=True,
        )
        return

    if PAYMENT_MODE in ROBOKASSA_MODES:
        await callback.answer()
        await show_payment_screen(callback.bot, telegram_id, order, state, callback)
        return

    await callback.answer(
        "💳 Оплата пока не подключена. Заказ сохранён, деньги не списываются.",
        show_alert=True,
    )


@router.callback_query(
    F.data.startswith(
        (
            DEMO_CHECK_PREFIX,
            DEMO_QUESTION_PREFIX,
            "demo_success:",
            "demo_fail:",
        )
    )
)
async def callback_old_demo_buttons(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "Эта тестовая кнопка отключена. Откройте /start.",
        show_alert=True,
    )


@router.callback_query(
    F.data.startswith(
        (
            "paid:",
            "question:",
            "back_to_payment:",
        )
    )
)
async def callback_old_payment(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "⚠️ Эта кнопка оплаты больше не действует. Откройте /start.",
        show_alert=True,
    )


@router.callback_query(
    F.data.in_(
        {
            "paid",
            "question",
            "back_to_payment",
        }
    )
)
async def callback_legacy_button(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "Эта кнопка устарела. Нажмите /start.",
        show_alert=True,
    )


def _payment_amount_matches(
    payment: dict,
    expected_amount: str,
) -> bool:
    """Check that payment amount and currency match the order."""

    try:
        amount = payment["amount"]

        if amount["currency"] != "RUB":
            return False

        paid = Decimal(str(amount["value"]))
        expected = Decimal(str(expected_amount))

        return (
            paid.is_finite() and expected.is_finite() and paid > 0 and paid == expected
        )

    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidOperation,
    ):
        return False
