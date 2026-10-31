"""Approve course join requests only for the owner of a paid invitation.

IMPORTANT: This router manages only invitations created by this course bot;
unknown links (including the group owner's manual links) are not touched.
"""

import asyncio
import logging
import os

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatJoinRequest

from integrations.join_claims import reserve_join, change_join_status
from integrations.admin_notifications import notify_admin

logger = logging.getLogger(__name__)
router = Router(name='course_join_requests')


def _record_status(link, user_id, before, after):
    """Best-effort journal update; report failures, never auto-retry approval."""
    try:
        saved = change_join_status(link, user_id, before, after)
    except Exception:
        logger.exception('Join state write failed: user=%s %s->%s', user_id, before, after)
        return False
    if not saved:
        logger.error('Join state transition missing: user=%s %s->%s', user_id, before, after)
    return saved


async def _decline(bot, chat_id, user_id):
    try:
        await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
    except Exception:
        logger.exception('Unable to decline join request: user=%s', user_id)


async def _revoke(bot, chat_id, link):
    try:
        await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=link)
        return True
    except Exception:
        logger.exception('Invitation revocation uncertain: chat=%s', chat_id)
        return False


@router.chat_join_request()
async def handle_course_join_request(request: ChatJoinRequest, bot: Bot) -> None:
    try:
        expected_chat_id = int(os.getenv('COURSE_CHANNEL_ID', '0'))
    except ValueError:
        logger.error('Invalid COURSE_CHANNEL_ID')
        return
    chat_id = request.chat.id
    user_id = request.from_user.id
    if expected_chat_id == 0 or chat_id != expected_chat_id:
        return

    invite = request.invite_link
    if invite is None or not getattr(invite, 'invite_link', None):
        # Do not interfere with manual admissions by the group owner.
        logger.warning('Join request without an identifiable link: user=%s', user_id)
        return
    if getattr(invite, 'is_revoked', False):
        logger.warning('Revoked invitation used: user=%s', user_id)
        return
    if getattr(invite, 'creates_join_request', False) is not True:
        logger.warning('Unexpected invitation type: user=%s', user_id)
        return

    link = invite.invite_link
    try:
        decision = reserve_join(link, user_id, chat_id, expected_chat_id)
    except Exception:
        logger.exception('Join reservation failed: user=%s', user_id)
        return

    if decision == 'ignore':
        # Unknown links are managed by the group owner, not the payment bot.
        logger.info('Unmanaged join request: user=%s', user_id)
        return
    if decision == 'review':
        logger.warning('Join request already claimed or ambiguous: user=%s', user_id)
        return
    if decision == 'deny':
        await _decline(bot, chat_id, user_id)
        return
    if decision != 'reserved':
        logger.error('Unexpected join reservation result: %s', decision)
        return

    # A durable claim now exists. Every uncertain result must remain manual.
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception:
        logger.exception('Membership check failed: user=%s', user_id)
        _record_status(link, user_id, 'approving', 'needs_review')
        return

    if member.status == ChatMemberStatus.KICKED:
        _record_status(link, user_id, 'approving', 'needs_review')
        await _decline(bot, chat_id, user_id)
        return

    already_member = member.status in (
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    ) or (
        member.status == ChatMemberStatus.RESTRICTED
        and bool(getattr(member, 'is_member', False))
    )
    if already_member:
        # Do not call approve on somebody who is already inside the group.
        logger.warning('Buyer already in group: user=%s; needs manual reconciliation', user_id)
        _record_status(link, user_id, 'approving', 'needs_review')
        return
    if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.RESTRICTED):
        logger.error('Unexpected member status: user=%s status=%s', user_id, member.status)
        _record_status(link, user_id, 'approving', 'needs_review')
        return

    try:
        approved = await bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
    except Exception:
        logger.exception('Approval result uncertain: user=%s', user_id)
        _record_status(link, user_id, 'approving', 'needs_review')
        return

    if approved is not True:
        logger.error('Telegram did not confirm approval: user=%s', user_id)
        _record_status(link, user_id, 'approving', 'needs_review')
        return

    if not _record_status(link, user_id, 'approving', 'approved'):
        # The user may ALREADY be in the group; revoking the link cannot undo
        # approval. An operator must verify group membership and DB state.
        await _revoke(bot, chat_id, link)
        _record_status(link, user_id, 'approving', 'needs_review')
        return

    if not await _revoke(bot, chat_id, link):
        _record_status(link, user_id, 'approved', 'needs_review')
        return

    if not _record_status(link, user_id, 'approved', 'completed'):
        logger.error('Approved and revoked, but status requires manual reconciliation: user=%s', user_id)
        return

    logger.info('Paid course access approved: user=%s', user_id)
    asyncio.create_task(notify_admin(
        f'✅ Ученик вступил в учебную группу\nTelegram ID: {user_id}\nСтатус: вступление одобрено'
    ))
