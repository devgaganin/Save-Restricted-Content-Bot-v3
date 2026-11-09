"""Optional, user-opted-in premium expiry reminders.

The expiry value is claimed atomically before calling Telegram. This is
at-most-once per saved expiry, not guaranteed delivery: a crash or an
ambiguous send error after the claim can result in no reminder.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import OWNER_ID
from shared_client import app
from utils.func import premium_users_collection, users_collection
from plugins.batch_queue import lock_for

logger = logging.getLogger(__name__)
worker = None


def enabled():
    return os.getenv('EXPIRY_REMINDERS_ENABLED', 'false').lower() == 'true'


def settings():
    try:
        hours = int(os.getenv('EXPIRY_REMINDER_HOURS', '24'))
        interval = int(os.getenv('EXPIRY_REMINDER_INTERVAL_SECONDS', '900'))
    except ValueError as exc:
        raise ValueError('Expiry reminder settings must be integers') from exc
    if not 1 <= hours <= 720 or not 60 <= interval <= 86400:
        raise ValueError('Expiry reminder hours must be 1–720 and interval 60–86400 seconds')
    return hours, interval


async def scan_once(now=None):
    """Scan the current window; re-check consent and expiry before each claim."""
    hours, _ = settings()
    scan_time = now or datetime.utcnow()
    cursor = premium_users_collection.find({
        'subscription_end': {'$gt': scan_time, '$lte': scan_time + timedelta(hours=hours)}
    }, {'user_id': 1, 'subscription_end': 1})
    async for record in cursor:
        uid, expiry = record.get('user_id'), record.get('subscription_end')
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0 or uid in OWNER_ID or not isinstance(expiry, datetime):
            continue
        try:
            async with lock_for(uid):
                current_time = now or datetime.utcnow()
                if not current_time < expiry <= current_time + timedelta(hours=hours):
                    continue
                user = await users_collection.find_one({'user_id': uid, 'expiry_reminders': True}, {'_id': 1})
                if not user:
                    continue
                # A renewal/revocation between the scan and claim fails this
                # compare-and-set. The marker is set before any send, so an
                # uncertain delivery is never automatically retried.
                claimed = await premium_users_collection.update_one(
                    {'_id': record['_id'], 'user_id': uid, 'subscription_end': expiry,
                     'reminder_claimed_for': {'$ne': expiry}},
                    {'$set': {'reminder_claimed_for': expiry, 'reminder_claimed_at': current_time}})
                if not claimed.modified_count:
                    continue
                text = (f'⏰ <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗥𝗘𝗠𝗜𝗡𝗗𝗘𝗥</b>\n\n'
                        f'<blockquote>💎 <b>Expiry when checked:</b> '
                        f'{expiry:%d %b %Y %H:%M} UTC\n'
                        '📊 Check My Plan for the latest status.</blockquote>')
                buttons = InlineKeyboardMarkup([[InlineKeyboardButton('💎 𝗠𝗬 𝗣𝗟𝗔𝗡', callback_data='ui:status')],
                                                [InlineKeyboardButton('⚙️ 𝗥𝗘𝗠𝗜𝗡𝗗𝗘𝗥𝗦', callback_data='ui:reminders')]])
                try:
                    await app.send_message(uid, text, reply_markup=buttons, parse_mode=ParseMode.HTML)
                except Exception:
                    # Telegram may have delivered despite an error. Keep the
                    # claim and do not send a second copy automatically.
                    logger.warning('Expiry reminder delivery failed or is uncertain for user %s', uid, exc_info=True)
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Expiry reminder processing failed for user %s', uid)


async def reminder_loop():
    _, interval = settings()
    while True:
        try:
            await scan_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Expiry reminder scan failed; will try again later')
        await asyncio.sleep(interval)


async def run_reminders_plugin():
    global worker
    if enabled():
        settings()  # fail startup on invalid configuration
        # A duplicate premium record would otherwise allow duplicate reminders.
        await premium_users_collection.create_index('user_id', unique=True)
        worker = asyncio.create_task(reminder_loop(), name='premium-expiry-reminders')


async def shutdown_reminders():
    global worker
    if worker:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        worker = None
