"""One opt-in live mirror per user, using their connected Pyrogram session."""
import asyncio
import logging
from datetime import datetime
from pyrogram import filters
from pyrogram.handlers import MessageHandler
from config import OWNER_ID
from utils.func import db, get_user_data, is_premium_user
from plugins import batch

logger = logging.getLogger(__name__)
mirrors = db['live_mirrors']
active = {}
lock = asyncio.Lock()


async def start_mirror(uid, source, restoring=False):
    async with lock:
        if uid in active:
            return False, 'A mirror is already running.'
        if uid not in OWNER_ID and not await is_premium_user(uid):
            return False, 'An active premium plan is required.'
        user = await get_user_data(uid) or {}
        if not user.get('session_string') or not user.get('bot_token'):
            return False, 'Connect your own account and custom bot first.'
        destination = user.get('chat_id')
        if not destination:
            return False, 'Set a destination in Settings first.'
        try:
            destination_id = int(destination.split('/', 1)[0])
        except (ValueError, AttributeError):
            return False, 'Destination is invalid.'
        bot = await batch.get_ubot(uid)
        user_client = await batch.get_uclient(uid)
        if not bot or not user_client or batch.UC.get(uid) is not user_client:
            return False, 'Could not connect your account and custom bot.'
        try:
            source_chat = await user_client.get_chat(source)
            source_id = source_chat.id
        except Exception:
            return False, 'Cannot access source. Check membership and the ID or username.'
        if source_id == destination_id:
            return False, 'Source and destination must be different.'
        state = {'client': user_client, 'handler': None, 'active': True, 'source_id': source_id, 'work_lock': asyncio.Lock()}

        async def on_message(client, message):
            async with state['work_lock']:
                if not state['active']:
                    return
                try:
                    # Re-check authorization and destination for each event.
                    if uid not in OWNER_ID and not await is_premium_user(uid):
                        await stop_mirror(uid)
                        return
                    settings = await get_user_data(uid) or {}
                    destination = settings.get('chat_id', '')
                    try:
                        current_dest = int(destination.split('/', 1)[0])
                    except (ValueError, AttributeError):
                        await stop_mirror(uid)
                        return
                    if current_dest == source_id:
                        await stop_mirror(uid)
                        return
                    result = await batch.process_msg(bot, user_client, message, str(uid), 'private', uid, source_id, destination_override=destination)
                    status = 'sent' if result.startswith(('Done', 'Sent', 'Copied')) else 'skipped' if result.startswith('Skipped') else 'failed'
                    await mirrors.update_one({'user_id': uid}, {'$inc': {status: 1}, '$set': {'last_message_id': message.id, 'updated_at': datetime.utcnow()}})
                except Exception:
                    logger.exception('Mirror processing failed for user %s', uid)
                    await mirrors.update_one({'user_id': uid}, {'$inc': {'failed': 1}})

        handler = MessageHandler(on_message, filters.chat(source_id) & filters.incoming)
        state['handler'] = handler
        try:
            await mirrors.create_index('user_id', unique=True)
            await mirrors.update_one({'user_id': uid}, {'$set': {'user_id': uid, 'source_id': source_id,
                'status': 'running', 'updated_at': datetime.utcnow()}, '$setOnInsert': {'sent': 0, 'failed': 0, 'skipped': 0}}, upsert=True)
            user_client.add_handler(handler, group=1)
            active[uid] = state
        except Exception:
            logger.exception('Could not start mirror for user %s', uid)
            await mirrors.update_one({'user_id': uid}, {'$set': {'status': 'error'}})
            return False, 'Could not start mirror.'
        return True, 'Mirror started. Only new messages received while online will be processed.'


async def stop_mirror(uid, drain=False):
    async with lock:
        state = active.pop(uid, None)
        if state:
            state['active'] = False
            state['client'].remove_handler(state['handler'], group=1)
        await mirrors.update_one({'user_id': uid}, {'$set': {'status': 'stopped', 'updated_at': datetime.utcnow()}})
    if state and drain:
        # Credential deletion and logout must not race an in-flight send.
        async with state['work_lock']:
            pass
    return bool(state)


async def run_mirror_plugin():
    # This restores subscriptions, not missed history. Only one bot instance
    # should run against a database to avoid duplicate mirror delivery.
    async for entry in mirrors.find({'status': 'running'}):
        try:
            ok, reason = await start_mirror(entry['user_id'], entry['source_id'], restoring=True)
            if not ok:
                logger.warning('Could not restore mirror for %s: %s', entry['user_id'], reason)
                await mirrors.update_one({'_id': entry['_id']}, {'$set': {'status': 'error'}})
        except Exception:
            logger.exception('Could not restore mirror')
