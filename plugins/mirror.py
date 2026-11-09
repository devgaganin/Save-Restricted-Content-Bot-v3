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
# Retain stopped state until an in-flight callback has drained, even on errors.
stopping = {}
lock = asyncio.Lock()


async def start_mirror(uid, source, restoring=False):
    async with lock:
        if uid in active:
            return False, 'A mirror is already running.'
        if uid in stopping:
            return False, 'Previous mirror needs a confirmed stop. Tap Stop Mirror and try again.'
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
        handler_attempted = False
        try:
            await mirrors.create_index('user_id', unique=True)
            result = await mirrors.update_one({'user_id': uid}, {'$set': {'user_id': uid, 'source_id': source_id,
                'status': 'running', 'updated_at': datetime.utcnow()}, '$setOnInsert': {'sent': 0, 'failed': 0, 'skipped': 0}}, upsert=True)
            if not result.acknowledged:
                raise RuntimeError('Mirror start was not acknowledged')
            handler_attempted = True
            user_client.add_handler(handler, group=1)
            active[uid] = state
        except Exception:
            # A failed registration may still have queued a handler. Disable it
            # before surfacing failure, and retain it for explicit stop/drain.
            state['active'] = False
            if handler_attempted:
                stopping[uid] = state
            logger.warning('Could not start mirror for user %s', uid)
            try:
                result = await mirrors.update_one({'user_id': uid}, {'$set': {'status': 'error'}})
                if not result.acknowledged:
                    raise RuntimeError('Mirror error state was not acknowledged')
            except Exception:
                raise RuntimeError('Mirror start state is uncertain') from None
            if handler_attempted:
                return False, 'Mirror start failed. Tap Stop Mirror to confirm cleanup.'
            return False, 'Could not start mirror.'
        return True, 'Mirror started. Only new messages received while online will be processed.'


async def stop_mirror(uid, drain=False):
    """Stop new work and persist the stop; optionally wait for in-flight work.

    Retain the state on failure and on non-draining stops so a later credential
    operation cannot lose track of a callback that is still using old clients.
    Do not wait on work_lock while holding the global lock: an in-flight
    callback can itself call stop_mirror when access or settings change.
    """
    async with lock:
        state = active.pop(uid, None) or stopping.get(uid)
        if state:
            state['active'] = False
            stopping[uid] = state
            if not state.get('detached'):
                state['client'].remove_handler(state['handler'], group=1)
                state['detached'] = True
        result = await mirrors.update_one({'user_id': uid}, {'$set': {'status': 'stopped', 'updated_at': datetime.utcnow()}})
        if not result.acknowledged:
            raise RuntimeError('Mirror stop was not acknowledged')
    if state and drain:
        async with state['work_lock']:
            pass
        async with lock:
            if stopping.get(uid) is state:
                stopping.pop(uid, None)
    return bool(state)


async def status_snapshot(uid):
    """Report local worker and persisted state separately, with no credentials."""
    async with lock:
        local = 'running' if uid in active else 'stopping' if uid in stopping else 'inactive'
    saved = await mirrors.find_one({'user_id': uid},
        {'_id': 0, 'status': 1})
    return local, saved


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
