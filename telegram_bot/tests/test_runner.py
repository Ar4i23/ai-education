"""Test-only launcher: Telegram polling, local Robokassa callback and access worker.

Run only inside the isolated test bot project. Does not expose HTTP publicly.
"""
import asyncio
import logging
import os
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher

from config import BOT_TOKEN, PAYMENT_MODE, validate_config
from database import init_database
from handlers import router
from integrations.robokassa_checkout import checkout_ready
from integrations.server import create_app
from integrations.delivery_worker import process_one_delivery

logger = logging.getLogger(__name__)


def test_configuration():
    validate_config()
    if PAYMENT_MODE != "robokassa_test" or not checkout_ready():
        raise RuntimeError("Only configured Robokassa TEST mode is allowed")
    raw = os.getenv("COURSE_CHANNEL_ID", "").strip()
    try:
        channel_id = int(raw)
    except ValueError as exc:
        raise RuntimeError("COURSE_CHANNEL_ID must be an integer") from exc
    if channel_id == 0:
        raise RuntimeError("COURSE_CHANNEL_ID must be nonzero")
    return channel_id


async def delivery_loop(bot: Bot, channel_id: int):
    while True:
        try:
            result = await process_one_delivery(bot, channel_id)
            if result == "delivered":
                logger.info("Course access delivered")
            elif result == "needs_review":
                logger.warning("Delivery needs manual review; no automatic retry")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Delivery worker error; will check queue later")
        await asyncio.sleep(5)


async def main():
    channel_id = test_configuration()
    init_database()
    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    runner = web.AppRunner(create_app())
    worker_task = None
    try:
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=8080)
        await site.start()
        logger.info("Local Robokassa callback listening on http://127.0.0.1:8080/robokassa/result")
        worker_task = asyncio.create_task(delivery_loop(bot, channel_id))
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
