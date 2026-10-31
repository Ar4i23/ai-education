"""Optional best-effort operations notification to a *separate* Telegram bot."""
import logging
import os
import socket
from config import BOT_TOKEN

import aiohttp

logger = logging.getLogger(__name__)


async def notify_admin(message: str) -> bool:
    token = BOT_TOKEN.strip()
    raw_chat_id = os.getenv('ADMIN_CHAT_ID', '').strip()
    if not token or not raw_chat_id:
        return False
    try:
        chat_id = int(raw_chat_id)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET6), timeout=aiohttp.ClientTimeout(total=20)) as client:
            async with client.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': True},
            ) as response:
                if response.status != 200:
                    logger.warning('Admin notification failed: Telegram HTTP %s', response.status)
                    return False
        return True
    except Exception:
        logger.warning('Admin notification network error (message not delivered)')
        return False
