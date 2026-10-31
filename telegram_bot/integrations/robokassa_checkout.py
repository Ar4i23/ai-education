"""Single source of truth for test and live Robokassa checkout.

Production uses an authenticated local bridge to POST the fiscal receipt.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from config import PAYMENT_MODE, ROBOKASSA_MODES, env_bool, invoice_offset, public_base_url
from integrations.robokassa import build_payment_url, build_payment_fields

DESCRIPTION = 'Обучающий онлайн-курс Antonova AI'
PAYMENT_FORM_TTL = 24 * 3600


def checkout_ready() -> bool:
    if PAYMENT_MODE not in ROBOKASSA_MODES:
        return False
    is_live = PAYMENT_MODE == 'robokassa_live'
    pfx = 'ROBOKASSA_LIVE' if is_live else 'ROBOKASSA_TEST'
    return (bool(os.getenv('ROBOKASSA_MERCHANT_LOGIN', '').strip())
            and bool(os.getenv(f'{pfx}_PASSWORD1', '').strip())
            and bool(os.getenv(f'{pfx}_PASSWORD2', '').strip())
            and os.getenv('ROBOKASSA_HASH_ALGORITHM', '').lower() == 'sha256'
            and (is_live or env_bool('ROBOKASSA_TEST_MODE'))
            and (not is_live or not env_bool('ROBOKASSA_TEST_MODE'))
            and (not is_live or env_bool('ROBOKASSA_USE_RECEIPT')))


def needs_post_receipt() -> bool:
    return env_bool('ROBOKASSA_USE_RECEIPT')


def payment_invoice_id(order_id: int) -> int:
    value = int(order_id) + invoice_offset()
    if not 1 <= value <= 9223372036854775807:
        raise ValueError('Invoice ID вне допустимого диапазона')
    return value


def _password1() -> str:
    mode = 'LIVE' if PAYMENT_MODE == 'robokassa_live' else 'TEST'
    return os.environ[f'ROBOKASSA_{mode}_PASSWORD1'].strip()


def build_fields(order):
    if not checkout_ready():
        raise ValueError('Robokassa checkout не настроен')
    if order['status'] != 'pending' or order['currency'] != 'RUB':
        raise ValueError('Нельзя оплатить заказ в текущем состоянии')
    return build_payment_fields(
        merchant_login=os.environ['ROBOKASSA_MERCHANT_LOGIN'].strip(),
        password1=_password1(),
        order_id=payment_invoice_id(int(order['id'])),
        amount=order['amount'],
        description=DESCRIPTION,
        is_test=PAYMENT_MODE == 'robokassa_test',
        algorithm='sha256',
        include_receipt=needs_post_receipt(),
        receipt_tax=os.getenv('ROBOKASSA_RECEIPT_TAX', 'none').strip(),
    )


def _token(order, expires: int) -> str:
    secret = os.getenv('ROBOKASSA_CHECKOUT_SECRET', '').encode('utf-8')
    if len(secret) < 32:
        raise RuntimeError('ROBOKASSA_CHECKOUT_SECRET не настроен')
    msg = f"{int(order['id'])}:{expires}:{order['idempotence_key']}".encode('utf-8')
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify_checkout_token(order, expires: str, supplied_token: str) -> bool:
    try:
        timestamp = int(expires)
        if timestamp < int(time.time()) or timestamp > int(time.time()) + PAYMENT_FORM_TTL:
            return False
        if not supplied_token or len(supplied_token) != 64:
            return False
        expected = _token(order, timestamp)
        return hmac.compare_digest(expected, supplied_token)
    except (ValueError, TypeError, KeyError):
        return False


def checkout_url(order):
    if not checkout_ready():
        raise ValueError('Robokassa checkout не настроен')
    if order['status'] != 'pending' or order['currency'] != 'RUB':
        raise ValueError('Нельзя оплатить заказ в текущем состоянии')
    if not needs_post_receipt():
        # Allowed only in explicit TEST mode; a live receipt is mandatory.
        if PAYMENT_MODE != 'robokassa_test':
            raise RuntimeError('В рабочем режиме нельзя отключать чек')
        return build_payment_url(
            merchant_login=os.environ['ROBOKASSA_MERCHANT_LOGIN'].strip(),
            password1=_password1(), order_id=payment_invoice_id(int(order['id'])),
            amount=order['amount'], description=DESCRIPTION,
            is_test=True, algorithm='sha256', include_receipt=False,
        )
    expires = int(time.time()) + PAYMENT_FORM_TTL
    query = urlencode({'expires': expires, 'token': _token(order, expires)})
    return f"{public_base_url()}/robokassa/checkout/{order['id']}?{query}"
