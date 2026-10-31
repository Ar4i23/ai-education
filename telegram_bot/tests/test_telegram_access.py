import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from integrations.telegram_access import (
    create_course_invite, create_course_invite_with_expiry, send_course_invite,
)


class TelegramAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_single_use_invite(self):
        bot = SimpleNamespace(create_chat_invite_link=AsyncMock(
            return_value=SimpleNamespace(invite_link="https://t.me/+test_invite")
        ))
        before = datetime.now(timezone.utc)
        link = await create_course_invite(bot, -1004307729335, 123)
        after = datetime.now(timezone.utc)
        self.assertEqual(link, "https://t.me/+test_invite")
        kwargs = bot.create_chat_invite_link.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -1004307729335)
        self.assertEqual(kwargs["name"], "course-order-123")
        self.assertTrue(kwargs["creates_join_request"])
        self.assertNotIn("member_limit", kwargs)
        self.assertGreaterEqual(kwargs["expire_date"].timestamp(), before.timestamp() + 86400)
        self.assertLessEqual(kwargs["expire_date"].timestamp(), after.timestamp() + 86400)

    async def test_expiry_is_identical_to_telegram_argument(self):
        bot = SimpleNamespace(create_chat_invite_link=AsyncMock(
            return_value=SimpleNamespace(invite_link="https://t.me/+test_invite")
        ))
        link, expires_at = await create_course_invite_with_expiry(bot, -1004307729335, 123)
        self.assertEqual(link, "https://t.me/+test_invite")
        self.assertEqual(expires_at, bot.create_chat_invite_link.await_args.kwargs["expire_date"])

    async def test_send_invite_message(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        await send_course_invite(bot, 123456789, "https://t.me/+test_invite")
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 123456789)
        self.assertIn("https://t.me/+test_invite", kwargs["text"])
        self.assertIn("Оплата курса подтверждена", kwargs["text"])


if __name__ == "__main__":
    unittest.main()
