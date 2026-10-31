"""Local callback HTTP server, reachable externally only behind HTTPS proxy."""
import asyncio
import html
import logging
import os

from aiohttp import web

from config import PAYMENT_MODE, ROBOKASSA_MODES, invoice_offset
from database import get_order
from integrations.robokassa import PAYMENT_URL
from integrations.robokassa_checkout import build_fields, verify_checkout_token
from integrations.payment_callback import process_payment_notification
from integrations.admin_notifications import notify_admin

logger = logging.getLogger(__name__)


async def health(request):
    return web.json_response({'status': 'ok', 'service': 'robokassa-callback'})


async def robokassa_checkout(request):
    """Validate signed, expiring payment link and POST a signed Receipt form."""
    raw_id = request.match_info['order_id']
    if not raw_id.isdecimal() or int(raw_id) <= 0:
        return web.Response(status=404, text='Order not found')
    order = get_order(int(raw_id))
    if not order or order['status'] != 'pending':
        return web.Response(status=404, text='Order not available')
    if not verify_checkout_token(
        order, request.query.get('expires',''), request.query.get('token','')
    ):
        return web.Response(status=403, text='Invalid or expired payment link')
    try:
        fields = build_fields(order)
    except (ValueError, RuntimeError):
        logger.exception('Failed to prepare payment form')
        return web.Response(status=503, text='Payment unavailable')

    inputs = '\n'.join(
        f'<input type="hidden" name="{html.escape(key, quote=True)}" '
        f'value="{html.escape(str(value), quote=True)}">'
        for key, value in fields.items()
    )
    page = f'''<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action https://auth.robokassa.ru; base-uri 'none'">
<title>Оплата Antonova AI</title></head><body>
<p>Переходим к безопасной оплате через Robokassa…</p>
<form id="payment" method="post" action="{PAYMENT_URL}">
{inputs}<button type="submit">Перейти к оплате</button></form>
<script>document.getElementById('payment').submit();</script></body></html>'''
    return web.Response(
        text=page, content_type='text/html', charset='utf-8',
        headers={'Cache-Control': 'no-store, private', 'X-Content-Type-Options': 'nosniff',
                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'},
    )


async def robokassa_result(request):
    if PAYMENT_MODE not in ROBOKASSA_MODES:
        return web.Response(status=503, text='Payments disabled')
    if request.content_length is not None and request.content_length > 8192:
        raise web.HTTPRequestEntityTooLarge(max_size=8192, actual_size=request.content_length)
    if request.content_type != 'application/x-www-form-urlencoded':
        return web.Response(status=415, text='Unsupported Media Type')
    try:
        data = await request.post()
        for key in ('OutSum', 'InvId', 'SignatureValue'):
            if len(data.getall(key, [])) != 1:
                return web.Response(status=400, text='Invalid notification')
        # This integration never sends Shp_* fields; never ignore unsigned extras.
        if any(key.lower().startswith('shp_') for key in data):
            return web.Response(status=400, text='Unexpected custom parameters')
    except Exception:
        logger.warning('Invalid Robokassa notification body')
        return web.Response(status=400, text='Bad Request')

    out_sum = data['OutSum']
    inv_id = data['InvId']
    signature = data['SignatureValue']
    logger.info('Robokassa callback received: InvId=%s', inv_id)

    pfx = 'LIVE' if PAYMENT_MODE == 'robokassa_live' else 'TEST'
    password2 = os.getenv(f'ROBOKASSA_{pfx}_PASSWORD2', '').strip()
    if not password2:
        logger.error('Robokassa callback password #2 is not configured')
        return web.Response(status=503, text='Service Unavailable')
    try:
        result = process_payment_notification(
            out_sum=out_sum, inv_id=inv_id, signature=signature,
            password2=password2, algorithm='sha256', invoice_offset=invoice_offset(),
        )
    except Exception:
        logger.exception('Payment DB transaction failed for invoice=%s', inv_id)
        return web.Response(status=503, text='Service Unavailable')
    if not result.accepted:
        logger.warning('Payment rejected: invoice=%s reason=%s', inv_id, result.reason)
        return web.Response(status=400, text='Payment rejected')
    logger.info('Payment accepted: order=%s newly_paid=%s', result.order_id, result.newly_paid)
    if result.newly_paid:
        # Best-effort; never delay/deny the authenticated payment acknowledgement.
        asyncio.create_task(notify_admin(
            f'✅ Оплата Robokassa подтверждена\nЗаказ: {result.order_id}\nСумма: {out_sum} RUB\nTelegram ID: {result.telegram_id}'
        ))
    return web.Response(text=f'OK{inv_id}', headers={'Cache-Control':'no-store'})


def create_app():
    app = web.Application(client_max_size=8192)
    app.router.add_get('/health', health)
    app.router.add_get('/robokassa/checkout/{order_id}', robokassa_checkout)
    app.router.add_post('/robokassa/result', robokassa_result)
    return app


if __name__ == '__main__':
    web.run_app(create_app(), host='127.0.0.1', port=int(os.getenv('ROBOKASSA_PORT','8081')))
