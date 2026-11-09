"""Persistent, manually resumable per-user batch jobs.

A Telegram send and a MongoDB checkpoint cannot be committed atomically. If a
process stops between them, the item stays in-flight until the user chooses
whether to retry it or skip it. Never silently resend an uncertain item.
"""
import asyncio
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime
from collections import defaultdict

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatType
import re
from config import FREEMIUM_LIMIT, PREMIUM_LIMIT
from utils.func import db, get_user_data_key, is_premium_user
from shared_client import app
from plugins import batch, localization
from utils.custom_filters import get_user_step

log = logging.getLogger(__name__)
jobs = db['batch_jobs']
history = db['batch_history']
workers = {}
locks = defaultdict(asyncio.Lock)


def lock_for(uid):
    return locks[uid]
# Bound Mongo document size regardless of operator-supplied plan limits.
MAX_BATCH_ITEMS = 5000
MAX_MESSAGE_ID = 2_147_483_647


def progress_keyboard(language='en'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(localization.button_label('pause', '⏸️ 𝗣𝗔𝗨𝗦𝗘', language), callback_data='ui:pause'),
         InlineKeyboardButton(localization.button_label('resume', '▶️ 𝗥𝗘𝗦𝗨𝗠𝗘', language), callback_data='ui:resume')],
        [InlineKeyboardButton(localization.button_label('stop', '🛑 𝗦𝗧𝗢𝗣', language), callback_data='ui:stop')],
    ])


def summary(job, language='en'):
    if language == 'hi':
        return (f"📊 जाँची गई ID: {job.get('current', 0)}/{job.get('total', 0)}\n"
                f"✅ भेजी गई: {job.get('success', 0)} | ❌ असफल: {job.get('failed', 0)}\n"
                f"⏭️ छोड़ी गई: {job.get('skipped', 0)} | ❔ अनिश्चित, छोड़ने का चयन: {job.get('uncertain', 0)}")
    return (f"📊 Processed: {job.get('current', 0)}/{job.get('total', 0)}\n"
            f"✅ Sent: {job.get('success', 0)} | ❌ Failed: {job.get('failed', 0)}\n"
            f"⏭️ Skipped: {job.get('skipped', 0)} | ❔ Uncertain, skipped by choice: {job.get('uncertain', 0)}")


def failed_fingerprint(ids):
    return hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest()[:16]


async def get_job(uid):
    return await jobs.find_one({'_id': uid})


async def has_unfinished(uid):
    job = await get_job(uid)
    return bool(job and job.get('status') in ('running', 'paused', 'interrupted', 'cancelling'))


async def archive(job):
    if job:
        record = dict(job)
        record['user_id'] = record['_id']
        record['_id'] = record['job_id']
        record['archived_at'] = datetime.utcnow()
        await history.replace_one({'_id': record['_id']}, record, upsert=True)


def topic_membership(msg, topic_id):
    """Return True/False, or None when Telegram metadata cannot establish scope."""
    if not isinstance(topic_id, int) or isinstance(topic_id, bool) or not 1 <= topic_id <= MAX_MESSAGE_ID:
        return None
    chat = getattr(msg, 'chat', None)
    if getattr(chat, 'type', None) != ChatType.FORUM:
        return None
    # Topic creation service messages are not transferable user content.
    if getattr(msg, 'service', None):
        return False
    if getattr(msg, 'is_topic_message', None) is not True:
        return None
    thread = getattr(msg, 'message_thread_id', None)
    if not isinstance(thread, int) or isinstance(thread, bool) or thread < 1:
        return None
    return thread == topic_id


async def validate_topic_route(source, source_topic, destination, bot, user_client):
    """Require distinct existing forums; never create topics or send during checks."""
    if (not isinstance(source_topic, int) or isinstance(source_topic, bool)
            or not 1 <= source_topic <= MAX_MESSAGE_ID):
        return None, 'Invalid source topic ID.'
    if not isinstance(destination, str) or not re.fullmatch(r'-100[0-9]{1,15}/[0-9]{1,10}', destination):
        return None, 'Set a destination forum chat ID and topic ID in Settings first.'
    chat_text, topic_text = destination.split('/')
    dest_id, dest_topic = int(chat_text), int(topic_text)
    if not 1 <= dest_topic <= MAX_MESSAGE_ID:
        return None, 'Invalid destination topic ID.'
    try:
        lookup_source = int(source) if isinstance(source, str) and re.fullmatch(r'-100[0-9]+', source) else source
        source_chat = await user_client.get_chat(lookup_source)
        destination_chat = await user_client.get_chat(dest_id)
        bot_chat = await bot.get_chat(dest_id)
        if (source_chat.type != ChatType.FORUM or destination_chat.type != ChatType.FORUM
                or bot_chat.type != ChatType.FORUM or destination_chat.id != dest_id or bot_chat.id != dest_id):
            return None, 'Source and destination must be accessible forum groups.'
        if source_chat.id == dest_id:
            return None, 'Source and destination groups must be different.'
        origin = await user_client.get_forum_topics_by_id(source_chat.id, source_topic)
        target = await user_client.get_forum_topics_by_id(dest_id, dest_topic)
        if not origin or getattr(origin, 'id', None) != source_topic or not target or getattr(target, 'id', None) != dest_topic:
            return None, 'Could not verify the selected topics.'
        if getattr(target, 'closed', False):
            return None, 'Destination topic is closed.'
        return source_chat.id, None
    except Exception:
        return None, 'Cannot verify forum access or topics. Check that your account and custom bot can access them.'


async def start_new(uid, source, start, count, link_type, message, still_wanted=None, source_topic=None, language='en'):
    async with lock_for(uid):
        if get_user_step(uid):
            return False, 'Finish or cancel login first.'
        if batch.is_user_active(uid) or await has_unfinished(uid):
            return False, 'An unfinished batch exists. Open Task Status to resume or stop it.'
        limit = PREMIUM_LIMIT if await is_premium_user(uid) else FREEMIUM_LIMIT
        if count < 1 or count > min(MAX_BATCH_ITEMS, limit):
            return False, f'Your current plan allows at most {min(MAX_BATCH_ITEMS, limit)} items.'
        if not isinstance(start, int) or not 1 <= start <= MAX_MESSAGE_ID or start + count - 1 > MAX_MESSAGE_ID:
            return False, 'Invalid message range.'
        bot = await batch.get_ubot(uid)
        user_client = await batch.get_uclient(uid)
        if not bot or not user_client:
            return False, 'Connect your custom bot and account first.'
        if still_wanted is not None and not still_wanted():
            return False, 'Batch input was cancelled.'
        dest = await get_user_data_key(uid, 'chat_id', None) or str(uid)
        if source_topic is not None:
            source_id, error = await validate_topic_route(source, source_topic, dest, bot, user_client)
            if error:
                return False, error
            source = source_id
            if still_wanted is not None and not still_wanted():
                return False, 'Batch input was cancelled.'
            first = await batch.get_msg(bot, user_client, source, start, link_type, uid)
            if still_wanted is not None and not still_wanted():
                return False, 'Batch input was cancelled.'
            if not first or topic_membership(first, source_topic) is not True:
                return False, 'The first post could not be verified in that source topic. Check the link and permissions.'
        old = await get_job(uid)
        await archive(old)
        job = {'_id': uid, 'job_id': secrets.token_hex(12), 'source': source,
               'destination': str(dest), 'link_type': link_type,
               'pending': list(range(int(start), int(start) + count)), 'failed_ids': [],
               'total': count, 'current': 0, 'success': 0, 'failed': 0, 'skipped': 0,
               'uncertain': 0, 'status': 'running', 'created_at': datetime.utcnow(),
               'status_message_id': message.id, 'language': language if language in localization.SUPPORTED else 'en'}
        if source_topic is not None:
            job['source_topic'] = source_topic
        if still_wanted is not None and not still_wanted():
            return False, 'Batch input was cancelled.'
        # No send is attempted until the job itself has been saved.
        await jobs.replace_one({'_id': uid}, job, upsert=True)
        await launch(uid, job, bot, user_client)
        return True, 'Batch started.'


async def launch(uid, job, bot, user_client):
    await batch.add_active_batch(uid, {'job_id': job['job_id'], 'total': job['total'],
        'current': job['current'], 'success': job['success'], 'failed': job['failed'],
        'skipped': job['skipped'], 'cancel_requested': False, 'paused': False,
        'progress_message_id': job.get('status_message_id')})
    task = asyncio.create_task(run_job(uid, job['job_id'], bot, user_client))
    workers[uid] = task


async def run_job(uid, job_id, bot, user_client):
    last_update = 0.0
    job = None
    try:
        while True:
            job = await jobs.find_one({'_id': uid, 'job_id': job_id})
            if not job:
                return
            if batch.should_cancel(uid):
                await jobs.update_one({'_id': uid, 'job_id': job_id},
                    {'$set': {'status': 'cancelled', 'updated_at': datetime.utcnow()}})
                await notify(uid, job, '🛑 Batch stopped.')
                return
            if (batch.get_batch_info(uid) or {}).get('paused'):
                await asyncio.sleep(1)
                continue
            if not job.get('pending'):
                await jobs.update_one({'_id': uid, 'job_id': job_id},
                    {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}})
                await notify(uid, job, '✅ Batch completed.')
                return
            mid = job['pending'][0]
            # This checkpoint precedes every fetch/send. On restart it is an
            # uncertain item, even if the failure happened before the send.
            nonce = secrets.token_hex(8)
            claim = await jobs.update_one({'_id': uid, 'job_id': job_id},
                                  {'$set': {'inflight': mid, 'inflight_nonce': nonce, 'updated_at': datetime.utcnow()}})
            if not claim.matched_count:
                return
            msg = await batch.get_msg(bot, user_client, job['source'], mid, job['link_type'], uid)
            if batch.should_cancel(uid) and not msg:
                await jobs.update_one({'_id': uid, 'job_id': job_id, 'inflight': mid, 'inflight_nonce': nonce}, {'$unset': {'inflight': '', 'inflight_nonce': ''}})
                continue
            # Unknown membership fails closed; never leak another topic's content.
            member = topic_membership(msg, job['source_topic']) if msg and job.get('source_topic') is not None else True
            if not msg:
                result = 'failed'
            elif member is not True:
                result = 'skipped' if member is False else 'failed'
            else:
                try:
                    outcome = await batch.process_msg(bot, user_client, msg, str(uid), job['link_type'], uid,
                        job['source'], destination_override=job['destination'])
                    result = ('success' if outcome.startswith(('Done', 'Copied', 'Sent')) else
                              'skipped' if outcome.startswith('Skipped') else 'failed')
                except Exception:
                    log.exception('Batch item failed for user %s', uid)
                    result = 'failed'
            update = {'$pull': {'pending': mid}, '$inc': {'current': 1, result: 1},
                      '$unset': {'inflight': '', 'inflight_nonce': ''}, '$set': {'updated_at': datetime.utcnow()}}
            if result == 'failed':
                update['$push'] = {'failed_ids': mid}
            # If this write fails, keep inflight set: the delivery result is unknown.
            checkpoint = await jobs.update_one({'_id': uid, 'job_id': job_id, 'inflight': mid, 'inflight_nonce': nonce}, update)
            if not checkpoint.matched_count:
                raise RuntimeError('Batch checkpoint not recorded')
            job = await jobs.find_one({'_id': uid, 'job_id': job_id})
            await batch.update_batch_progress(uid, job['current'], job['success'], job['failed'], job['skipped'])
            if time.monotonic() - last_update >= 15:
                try:
                    await app.edit_message_text(uid, job['status_message_id'], ('📚 बैच\n\n' if job.get('language') == 'hi' else '📚 𝗕𝗔𝗧𝗖𝗛\n\n') + summary(job, job.get('language', 'en')), reply_markup=progress_keyboard(job.get('language', 'en')))
                    last_update = time.monotonic()
                except Exception:
                    pass
            if job.get('pending'):
                for _ in range(10):
                    if batch.should_cancel(uid) or (batch.get_batch_info(uid) or {}).get('paused'):
                        break
                    await asyncio.sleep(1)
    except asyncio.CancelledError:
        # Keep inflight and pending intact so restart asks for explicit choice.
        try:
            await jobs.update_one({'_id': uid, 'job_id': job_id, 'status': {'$in': ['running', 'paused', 'cancelling']}}, {'$set': {'status': 'interrupted', 'updated_at': datetime.utcnow()}})
        except Exception:
            log.exception('Could not mark interrupted batch')
        raise
    except Exception:
        log.exception('Batch interrupted for user %s', uid)
        try:
            await jobs.update_one({'_id': uid, 'job_id': job_id, 'status': {'$in': ['running', 'paused', 'cancelling']}}, {'$set': {'status': 'interrupted', 'updated_at': datetime.utcnow()}})
            await app.send_message(uid, localization.translate('⚠️ Batch interrupted. Open Task Status to review and resume.', (job or {}).get('language', 'en')))
        except Exception:
            log.exception('Could not record interruption')
    finally:
        await batch.remove_active_batch(uid)
        if workers.get(uid) is asyncio.current_task():
            workers.pop(uid, None)


async def notify(uid, job, title):
    language = job.get('language', 'en')
    buttons = []
    if job.get('failed_ids') and not job.get('pending') and job.get('inflight') is None:
        buttons.append([InlineKeyboardButton(localization.button_label('retry_failed', '🔁 𝗥𝗘𝗧𝗥𝗬 𝗙𝗔𝗜𝗟𝗘𝗗', language), callback_data='ui:retry_failed')])
    if job.get('pending'):
        buttons.append([InlineKeyboardButton(localization.button_label('resume', '▶️ 𝗥𝗘𝗦𝗨𝗠𝗘', language), callback_data='ui:resume')])
    buttons.append([InlineKeyboardButton(localization.button_label('home', '🏠 𝗛𝗢𝗠𝗘', language), callback_data='ui:home')])
    try:
        await app.send_message(uid, localization.translate(title, language) + '\n\n' + summary(job, language), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        log.exception('Could not send batch summary')


async def resume(uid, message, inflight_choice=None, expected_job_id=None, expected_inflight=None, expected_nonce=None):
    async with lock_for(uid):
        if get_user_step(uid):
            return False, 'Finish or cancel login first.'
        if batch.is_user_active(uid):
            return False, 'A batch is already running.'
        job = await get_job(uid)
        if not job or job.get('status') not in ('interrupted', 'paused', 'cancelled', 'running', 'cancelling') or not job.get('pending'):
            return False, 'No saved batch to resume.'
        limit = PREMIUM_LIMIT if await is_premium_user(uid) else FREEMIUM_LIMIT
        if job['total'] > min(limit, MAX_BATCH_ITEMS):
            return False, 'Your current plan does not allow this batch size.'
        if inflight_choice is not None and (inflight_choice not in ('skip', 'retry')
                or expected_job_id != job.get('job_id') or expected_inflight != job.get('inflight')
                or job.get('inflight') is None or expected_nonce != (job.get('inflight_nonce') or 'legacy')):
            return False, 'This choice is outdated. Open Task Status and review the current item.'
        if job.get('inflight') is not None and inflight_choice is None:
            return False, 'uncertain'
        bot = await batch.get_ubot(uid)
        user_client = await batch.get_uclient(uid)
        if not bot or not user_client:
            return False, 'Connect your custom bot and account first.'
        update = {'$set': {'status': 'running', 'status_message_id': message.id, 'updated_at': datetime.utcnow()},
                  '$unset': {'inflight': '', 'inflight_nonce': ''}}
        if job.get('inflight') is not None and inflight_choice == 'skip':
            update['$pull'] = {'pending': job['inflight']}
            update['$inc'] = {'current': 1, 'uncertain': 1}
        # Retry leaves the uncertain ID in pending; only with explicit consent.
        changed = await jobs.update_one({'_id': uid, 'job_id': job['job_id'], 'status': job['status']}, update)
        if not changed.matched_count:
            return False, 'Batch changed; open Task Status and try again.'
        job = await get_job(uid)
        await launch(uid, job, bot, user_client)
        return True, 'Saved batch resumed.'


async def retry_failed(uid, message, expected_job_id=None, expected_fingerprint=None):
    async with lock_for(uid):
        if get_user_step(uid):
            return False, 'Finish or cancel login first.'
        if batch.is_user_active(uid) or await has_unfinished(uid):
            return False, 'Stop or finish the active batch first.'
        job = await get_job(uid)
        failed = list(job.get('failed_ids', [])) if job else []
        if not failed:
            return False, 'No failed items to retry.'
        if job.get('pending') or job.get('inflight') is not None:
            return False, 'Resume the remaining items before retrying failures.'
        if expected_job_id != job.get('job_id') or expected_fingerprint != failed_fingerprint(failed):
            return False, 'This confirmation is outdated. Review the current failed items first.'
        limit = PREMIUM_LIMIT if await is_premium_user(uid) else FREEMIUM_LIMIT
        if len(failed) > min(limit, MAX_BATCH_ITEMS):
            return False, 'Your current plan does not allow this retry size.'
        bot = await batch.get_ubot(uid)
        user_client = await batch.get_uclient(uid)
        if not bot or not user_client:
            return False, 'Connect your custom bot and account first.'
        await archive(job)
        new = {'_id': uid, 'job_id': secrets.token_hex(12), 'source': job['source'],
               'destination': job['destination'], 'link_type': job['link_type'],
               'pending': failed, 'failed_ids': [], 'total': len(failed), 'current': 0,
               'success': 0, 'failed': 0, 'skipped': 0, 'uncertain': 0,
               'status': 'running', 'created_at': datetime.utcnow(), 'status_message_id': message.id,
               'retried_from': job['job_id'], 'language': job.get('language', 'en')}
        if job.get('source_topic') is not None:
            new['source_topic'] = job['source_topic']
        await jobs.replace_one({'_id': uid}, new, upsert=True)
        await launch(uid, new, bot, user_client)
        return True, 'Retry started.'


async def cancel_saved(uid):
    async with lock_for(uid):
        if batch.is_user_active(uid):
            return False
        result = await jobs.update_one({'_id': uid, 'status': {'$in': ['running', 'cancelling', 'interrupted', 'paused']}},
                                       {'$set': {'status': 'cancelled', 'updated_at': datetime.utcnow()}})
        return bool(result.modified_count)


async def shutdown_workers():
    tasks = list(workers.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_batch_queue_plugin():
    # Single-instance recovery: do not automatically resend any in-flight item.
    await jobs.update_many({'status': {'$in': ['running', 'paused', 'cancelling']}},
                           {'$set': {'status': 'interrupted', 'updated_at': datetime.utcnow()}})
