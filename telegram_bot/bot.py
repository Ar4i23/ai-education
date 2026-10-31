"""
Главный файл запуска бота
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import BOT_TOKEN, PAYMENT_MODE, ROBOKASSA_MODES, validate_config
from database import init_database
from handlers import router

# Настройка логирования
logging.basicConfig(level=logging.INFO)


async def main():
    """
    Главная функция бота
    """
    if PAYMENT_MODE in ROBOKASSA_MODES:
        raise RuntimeError("Для Robokassa запускайте production_runner.py, а не bot.py")
    validate_config()

    # Инициализируем базу данных
    init_database()
    logging.info("База данных SQLite готова к работе")

    # Создаём бота и диспетчер
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем обработчики
    dp.include_router(router)

    logging.info("🤖 Бот запущен! Ожидаю команды...")

    # Запускаем polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
