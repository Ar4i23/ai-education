"""Application configuration loaded from the bot's adjacent .env file."""

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRICE = os.getenv("PRICE", "3499").strip()
CURRENCY = os.getenv("CURRENCY", "руб").strip()
COURSE_LINK = os.getenv("COURSE_LINK", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()


def validate_config() -> None:
    """Fail before polling if a required production setting is missing."""
    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "COURSE_LINK": COURSE_LINK,
        "SUPPORT_USERNAME": SUPPORT_USERNAME,
        "YOOKASSA_SHOP_ID": YOOKASSA_SHOP_ID,
        "YOOKASSA_SECRET_KEY": YOOKASSA_SECRET_KEY,
    }
    missing = [
        name
        for name, value in required.items()
        if not value or "placeholder" in value.lower() or "вставь_сюда" in value.lower()
    ]
    if missing:
        raise RuntimeError("Заполните обязательные настройки в telegram_bot/.env: " + ", ".join(missing))

    try:
        amount = Decimal(PRICE)
    except InvalidOperation as exc:
        raise RuntimeError("PRICE должен быть положительным числом.") from exc
    if not amount.is_finite() or amount <= 0:
        raise RuntimeError("PRICE должен быть положительным числом.")
    try:
        if amount != amount.quantize(Decimal("0.01")):
            raise RuntimeError("PRICE не должен содержать больше двух знаков после запятой.")
    except InvalidOperation as exc:
        raise RuntimeError("PRICE должен быть положительным числом не более чем с двумя знаками после запятой.") from exc

    parsed_link = urlparse(COURSE_LINK)
    if parsed_link.scheme != "https" or not parsed_link.netloc:
        raise RuntimeError("COURSE_LINK должен быть действующей HTTPS-ссылкой.")
