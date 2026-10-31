"""Tests for Telegram join-request handling without real API calls."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from aiogram.enums import ChatMemberStatus

from integrations.join_requests import handle_course_join_request


CHAT_ID = -1004307729335
BUYER_ID = 111111
STRANGER_ID = 222222
LINK = "https://t.me/+test_paid_link"


class JoinRequestTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"COURSE_CHANNEL_ID": str(CHAT_ID)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.reserve_patch = patch(
            "integrations.join_requests.reserve_join",
            return_value="reserved",
        )
        self.reserve = self.reserve_patch.start()
        self.addCleanup(self.reserve_patch.stop)

        self.status_patch = patch(
            "integrations.join_requests.change_join_status",
            return_value=True,
        )
        self.change_status = self.status_patch.start()
        self.addCleanup(self.status_patch.stop)

        self.bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(
                    status=ChatMemberStatus.LEFT,
                )
            ),
            approve_chat_join_request=AsyncMock(return_value=True),
            decline_chat_join_request=AsyncMock(return_value=True),
            revoke_chat_invite_link=AsyncMock(return_value=True),
        )

    def request(self, user_id=BUYER_ID, chat_id=CHAT_ID, link=LINK):
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            from_user=SimpleNamespace(id=user_id),
            invite_link=(
                SimpleNamespace(
                    invite_link=link,
                    creates_join_request=True,
                )
                if link else None
            ),
        )

    async def test_buyer_approved_and_link_revoked(self):
        await handle_course_join_request(self.request(), self.bot)

        self.bot.approve_chat_join_request.assert_awaited_once_with(
            chat_id=CHAT_ID,
            user_id=BUYER_ID,
        )
        self.bot.revoke_chat_invite_link.assert_awaited_once_with(
            chat_id=CHAT_ID,
            invite_link=LINK,
        )
        self.change_status.assert_has_calls([
            call(LINK, BUYER_ID, "approving", "approved"),
            call(LINK, BUYER_ID, "approved", "completed"),
        ])

    async def test_stranger_declined(self):
        self.reserve.return_value = "deny"

        await handle_course_join_request(
            self.request(user_id=STRANGER_ID),
            self.bot,
        )

        self.bot.decline_chat_join_request.assert_awaited_once_with(
            chat_id=CHAT_ID,
            user_id=STRANGER_ID,
        )
        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_unknown_link_not_approved(self):
        self.reserve.return_value = "ignore"

        await handle_course_join_request(self.request(), self.bot)

        self.bot.approve_chat_join_request.assert_not_awaited()
        self.bot.revoke_chat_invite_link.assert_not_awaited()

    async def test_duplicate_request_not_approved(self):
        self.reserve.return_value = "review"

        await handle_course_join_request(self.request(), self.bot)

        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_wrong_group_ignored(self):
        await handle_course_join_request(
            self.request(chat_id=-100123456789),
            self.bot,
        )

        self.reserve.assert_not_called()
        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_request_without_link_ignored(self):
        await handle_course_join_request(
            self.request(link=None),
            self.bot,
        )

        self.reserve.assert_not_called()
        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_banned_user_not_approved(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(
            status=ChatMemberStatus.KICKED,
        )

        await handle_course_join_request(self.request(), self.bot)

        self.bot.approve_chat_join_request.assert_not_awaited()
        self.bot.decline_chat_join_request.assert_awaited_once()
        self.change_status.assert_called_with(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_approval_error_requires_review(self):
        self.bot.approve_chat_join_request.side_effect = RuntimeError(
            "Simulated Telegram error"
        )

        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(
                self.request(),
                self.bot,
            )

        self.change_status.assert_called_with(
            LINK, BUYER_ID, "approving", "needs_review"
        )
        self.bot.revoke_chat_invite_link.assert_not_awaited()

    async def test_revoke_error_requires_review(self):
        self.bot.revoke_chat_invite_link.side_effect = RuntimeError(
            "Simulated Telegram error"
        )

        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(
                self.request(),
                self.bot,
            )

        self.change_status.assert_any_call(
            LINK, BUYER_ID, "approved", "needs_review"
        )

    async def test_database_error_prevents_approval(self):
        self.reserve.side_effect = RuntimeError(
            "Simulated database error"
        )

        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(
                self.request(),
                self.bot,
            )

        self.bot.approve_chat_join_request.assert_not_awaited()


    async def test_database_failure_after_approval(self):
        self.change_status.side_effect = [
            RuntimeError("Simulated SQLite failure"),
            True,
        ]

        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(
                self.request(),
                self.bot,
            )

        self.bot.approve_chat_join_request.assert_awaited_once()
        self.bot.revoke_chat_invite_link.assert_awaited_once_with(
            chat_id=CHAT_ID,
            invite_link=LINK,
        )

        self.change_status.assert_any_call(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_emergency_revocation_failure(self):
        self.change_status.side_effect = [
            RuntimeError("Simulated SQLite failure"),
            True,
        ]

        self.bot.revoke_chat_invite_link.side_effect = RuntimeError(
            "Simulated Telegram failure"
        )

        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(
                self.request(),
                self.bot,
            )

        self.bot.approve_chat_join_request.assert_awaited_once()
        self.bot.revoke_chat_invite_link.assert_awaited_once()

        self.change_status.assert_any_call(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_existing_member_not_approved_twice(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(
            status=ChatMemberStatus.MEMBER,
        )
        await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_not_awaited()
        self.change_status.assert_called_with(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_restricted_existing_member_not_approved(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(
            status=ChatMemberStatus.RESTRICTED, is_member=True,
        )
        await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_restricted_nonmember_may_be_approved(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(
            status=ChatMemberStatus.RESTRICTED, is_member=False,
        )
        await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_awaited_once()

    async def test_revoked_invitation_not_approved(self):
        request = self.request()
        request.invite_link.is_revoked = True
        await handle_course_join_request(request, self.bot)
        self.reserve.assert_not_called()
        self.bot.approve_chat_join_request.assert_not_awaited()

    async def test_membership_check_exception_cannot_approve(self):
        self.bot.get_chat_member.side_effect = RuntimeError("mock offline")
        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_not_awaited()
        self.change_status.assert_called_with(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_approval_persist_failure_attempts_revocation(self):
        self.change_status.side_effect = [False, True]
        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_awaited_once()
        self.bot.revoke_chat_invite_link.assert_awaited_once()
        self.change_status.assert_any_call(
            LINK, BUYER_ID, "approving", "needs_review"
        )

    async def test_completed_persist_failure_has_no_second_approval(self):
        self.change_status.side_effect = [True, False]
        with patch("integrations.join_requests.logger"):
            await handle_course_join_request(self.request(), self.bot)
        self.bot.approve_chat_join_request.assert_awaited_once()
        self.bot.revoke_chat_invite_link.assert_awaited_once()
        self.assertEqual(self.change_status.call_count, 2)

    async def test_unknown_link_is_left_for_owner_to_manage(self):
        self.reserve.return_value = "ignore"
        await handle_course_join_request(self.request(), self.bot)
        self.bot.decline_chat_join_request.assert_not_awaited()
        self.bot.approve_chat_join_request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
