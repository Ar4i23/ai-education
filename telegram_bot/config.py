"""Validated configuration for Robokassa-backed course payments."""

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))

BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
PRICE = os.getenv('PRICE', '3499').strip()
CURRENCY = os.getenv('CURRENCY', 'RUB').strip()
COURSE_LINK = os.getenv('COURSE_LINK', '').strip()
SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME', '').strip().lstrip('@')
PAYMENT_MODE = os.getenv('PAYMENT_MODE', 'demo').strip().lower()
ROBOKASSA_MODES = frozenset({'robokassa_test', 'robokassa_live'})


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, 'true' if default else 'false').strip().lower() == 'true'


def invoice_offset() -> int:
    try:
        value = int(os.getenv('ROBOKASSA_INVOICE_OFFSET', '0').strip())
    except ValueError as exc:
        raise RuntimeError('ROBOKASSA_INVOICE_OFFSET должен быть целым числом') from exc
    if value < 0 or value > 9_000_000_000_000_000_000:
        raise RuntimeError('Некорректный ROBOKASSA_INVOICE_OFFSET')
    return value


def public_base_url() -> str:
    return os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')


def validate_config() -> None:
    if PAYMENT_MODE not in {'demo', *ROBOKASSA_MODES}:
        raise RuntimeError('PAYMENT_MODE: demo, robokassa_test или robokassa_live')

    required = {'BOT_TOKEN': BOT_TOKEN, 'COURSE_LINK': COURSE_LINK,
                'SUPPORT_USERNAME': SUPPORT_USERNAME}
    if PAYMENT_MODE in ROBOKASSA_MODES:
        required.update({
            'ROBOKASSA_MERCHANT_LOGIN': os.getenv('ROBOKASSA_MERCHANT_LOGIN', '').strip(),
            'ROBOKASSA_HASH_ALGORITHM': os.getenv('ROBOKASSA_HASH_ALGORITHM', '').strip(),
        })
        if PAYMENT_MODE == 'robokassa_test':
            required.update({
                'ROBOKASSA_TEST_PASSWORD1': os.getenv('ROBOKASSA_TEST_PASSWORD1', '').strip(),
                'ROBOKASSA_TEST_PASSWORD2': os.getenv('ROBOKASSA_TEST_PASSWORD2', '').strip(),
            })
            if not env_bool('ROBOKASSA_TEST_MODE'):
                raise RuntimeError('Тестовый режим требует ROBOKASSA_TEST_MODE=true')
        else:
            required.update({
                'ROBOKASSA_LIVE_PASSWORD1': os.getenv('ROBOKASSA_LIVE_PASSWORD1', '').strip(),
                'ROBOKASSA_LIVE_PASSWORD2': os.getenv('ROBOKASSA_LIVE_PASSWORD2', '').strip(),
                'PUBLIC_BASE_URL': public_base_url(),
                'ROBOKASSA_CHECKOUT_SECRET': os.getenv('ROBOKASSA_CHECKOUT_SECRET', '').strip(),
                'ROBOKASSA_RECEIPT_TAX': os.getenv('ROBOKASSA_RECEIPT_TAX', '').strip(),
            })
            if env_bool('ROBOKASSA_TEST_MODE', False):
                raise RuntimeError('Боевой режим запрещён при ROBOKASSA_TEST_MODE=true')
            if not env_bool('ROBOKASSA_USE_RECEIPT'):
                raise RuntimeError('Для боевой Robokassa требуется ROBOKASSA_USE_RECEIPT=true и настроенная фискализация')
            if len(required['ROBOKASSA_CHECKOUT_SECRET']) < 32:
                raise RuntimeError('ROBOKASSA_CHECKOUT_SECRET должен быть не короче 32 символов')
            if invoice_offset() < 1_000_000:
                raise RuntimeError('Для боевой оплаты ROBOKASSA_INVOICE_OFFSET должен быть >= 1000000')

        if os.getenv('ROBOKASSA_HASH_ALGORITHM', '').lower() != 'sha256':
            raise RuntimeError('Настройте SHA256 в Robokassa и .env')

    missing = [name for name, value in required.items()
               if not value or 'placeholder' in value.lower() or 'вставь_сюда' in value.lower()]
    if missing:
        raise RuntimeError('Не заполнены обязательные настройки: ' + ', '.join(missing))

    if CURRENCY != 'RUB':
        raise RuntimeError('CURRENCY должен быть RUB')
    try:
        price = Decimal(PRICE)
        if not price.is_finite() or price <= 0 or price != price.quantize(Decimal('0.01')):
            raise RuntimeError('PRICE должен быть положительным числом с точностью до копейки')
    except InvalidOperation as exc:
        raise RuntimeError('Некорректный PRICE') from exc

    url = urlparse(COURSE_LINK)
    if url.scheme != 'https' or not url.netloc:
        raise RuntimeError('COURSE_LINK должен быть HTTPS-ссылкой')

    if PAYMENT_MODE in ROBOKASSA_MODES and os.getenv('ROBOKASSA_USE_RECEIPT', 'false').lower() == 'true':
        base = public_base_url()
        if urlparse(base).scheme != 'https' or not urlparse(base).netloc:
            raise RuntimeError('Для POST Receipt требуется PUBLIC_BASE_URL=https://...')
        if len(os.getenv('ROBOKASSA_CHECKOUT_SECRET', '').strip()) < 32:
            raise RuntimeError('Требуется ROBOKASSA_CHECKOUT_SECRET от 32 символов')
        if os.getenv('ROBOKASSA_RECEIPT_TAX', '').strip() not in {'none','vat0','vat10','vat110','vat20','vat120','vat5','vat7','vat105','vat107','vat22','vat122'}:
            raise RuntimeError('Уточните ROBOKASSA_RECEIPT_TAX в настройках фискализации')
