"""Production launcher: callback + delivery worker + exclusive Telegram polling."""
import asyncio
import logging
import os
import socket
from aiogram.client.session.aiohttp import AiohttpSession
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiohttp import web

from config import BOT_TOKEN, PAYMENT_MODE, ROBOKASSA_MODES, validate_config
from database import init_database
from handlers import router
from integrations.join_requests import router as join_router
from integrations.join_claims import init_join_claims_table
from integrations.access_recovery import init_recovery_table
from integrations.access_delivery import init_delivery_table
from integrations.delivery_worker import process_one_delivery
from integrations.access_delivery import recover_stale_deliveries
from integrations.robokassa_checkout import checkout_ready
from integrations.admin_notifications import notify_admin
from integrations.server import create_app

logger = logging.getLogger(__name__)


async def delivery_loop(bot, channel_id):
    iteration = 0
    while True:
        try:
            # Once per ~5 minutes, safely release a claim only if invite creation
            # had not started; never resend an uncertain Telegram invitation.
            if iteration % 60 == 0:
                recovered = recover_stale_deliveries(timeout_minutes=10)
                if recovered:
                    logger.warning('Safely recovered %s stale unstarted delivery claims', recovered)
            result = await process_one_delivery(bot, channel_id)
            if result == 'delivered':
                logger.info('Course access delivered')
            elif result == 'needs_review':
                logger.error('Access delivery needs manual review; not retrying uncertain invite')
                await notify_admin('⚠️ Требуется ручная проверка выдачи доступа Antonova AI. Проверьте очередь access_deliveries.')
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Delivery worker failed; will check queue again')
        iteration += 1
        await asyncio.sleep(5)


async def main():
    validate_config()
    if PAYMENT_MODE not in ROBOKASSA_MODES or not checkout_ready():
        raise RuntimeError('Production runner requires configured Robokassa mode')
    raw = os.getenv('COURSE_CHANNEL_ID', '').strip()
    try:
        channel_id = int(raw)
    except ValueError as exc:
        raise RuntimeError('COURSE_CHANNEL_ID должен быть числовым ID') from exc
    if channel_id == 0:
        raise RuntimeError('COURSE_CHANNEL_ID не указан')
    port = int(os.getenv('ROBOKASSA_PORT', '8081'))
    if not 1024 <= port <= 65535:
        raise RuntimeError('Неверный ROBOKASSA_PORT')
    if os.getenv('RUN_TELEGRAM_POLLING', 'false').strip().lower() != 'true':
        raise RuntimeError('RUN_TELEGRAM_POLLING не true: не запускаем конкурирующего бота')

    init_database()
    init_delivery_table()
    init_recovery_table()
    init_join_claims_table()
    session = AiohttpSession(timeout=45)
    session._connector_init['family'] = socket.AF_INET6
    bot = Bot(token=BOT_TOKEN, session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.include_router(join_router)
    runner = web.AppRunner(create_app())
    worker = None
    try:
        await runner.setup()
        site = web.TCPSite(runner, host='127.0.0.1', port=port)
        await site.start()
        logger.info('Robokassa local API listening on 127.0.0.1:%s', port)
        worker = asyncio.create_task(delivery_loop(bot, channel_id))
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await runner.cleanup()
        await bot.session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
