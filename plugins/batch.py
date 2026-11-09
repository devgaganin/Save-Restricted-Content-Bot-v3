# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

import os, re, time, asyncio, json, asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, FloodWait
from config import API_ID, API_HASH, LOG_GROUP, STRING, FORCE_SUB, FREEMIUM_LIMIT, PREMIUM_LIMIT
from utils.func import get_user_data, screenshot, thumbnail, get_video_metadata
from utils.func import get_user_data_key, process_text_with_rules, is_premium_user, E, parse_message_link
from shared_client import app as X
from plugins.settings import rename_file
from plugins import localization
from plugins.start import subscribe as sub
from utils.custom_filters import login_in_progress
from utils.encrypt import dcs
from typing import Dict, Any, Optional


Y = None if not STRING else __import__('shared_client').userbot
Z, P, UB, UC, emp = {}, {}, {}, {}, {}

ACTIVE_USERS = {}
ACTIVE_USERS_FILE = "active_users.json"

# fixed directory file_name problems
def sanitize(filename):
    return re.sub(r'[<>:"/\\|?*\']', '_', filename).strip(" .")[:255]

def load_active_users():
    try:
        if os.path.exists(ACTIVE_USERS_FILE):
            with open(ACTIVE_USERS_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception:
        return {}

async def save_active_users_to_file():
    try:
        with open(ACTIVE_USERS_FILE, 'w') as f:
            json.dump(ACTIVE_USERS, f)
    except Exception as e:
        print(f"Error saving active users: {e}")

async def add_active_batch(user_id: int, batch_info: Dict[str, Any]):
    ACTIVE_USERS[str(user_id)] = batch_info
    await save_active_users_to_file()

def is_user_active(user_id: int) -> bool:
    return str(user_id) in ACTIVE_USERS

async def update_batch_progress(user_id: int, current: int, success: int, failed: int = 0, skipped: int = 0):
    if str(user_id) in ACTIVE_USERS:
        ACTIVE_USERS[str(user_id)]["current"] = current
        ACTIVE_USERS[str(user_id)]["success"] = success
        ACTIVE_USERS[str(user_id)]["failed"] = failed
        ACTIVE_USERS[str(user_id)]["skipped"] = skipped
        await save_active_users_to_file()

async def request_batch_cancel(user_id: int):
    if str(user_id) in ACTIVE_USERS:
        task = ACTIVE_USERS[str(user_id)]
        task["cancel_requested"] = True
        await save_active_users_to_file()
        if task.get('job_id'):
            from plugins.batch_queue import jobs
            await jobs.update_one({'_id': user_id, 'job_id': task['job_id'], 'status': {'$in': ['running', 'paused']}}, {'$set': {'status': 'cancelling'}})
        return True
    return False

async def set_batch_paused(user_id: int, paused: bool):
    task = ACTIVE_USERS.get(str(user_id))
    if not task or task.get('cancel_requested') or task.get('kind') == 'single':
        return False
    task['paused'] = paused
    await save_active_users_to_file()
    if task.get('job_id'):
        from plugins.batch_queue import jobs
        await jobs.update_one({'_id': user_id, 'job_id': task['job_id'], 'status': {'$in': ['running', 'paused']}}, {'$set': {'status': 'paused' if paused else 'running'}})
    return True


def should_cancel(user_id: int) -> bool:
    user_str = str(user_id)
    return user_str in ACTIVE_USERS and ACTIVE_USERS[user_str].get("cancel_requested", False)

async def remove_active_batch(user_id: int):
    if str(user_id) in ACTIVE_USERS:
        del ACTIVE_USERS[str(user_id)]
        await save_active_users_to_file()

def get_batch_info(user_id: int) -> Optional[Dict[str, Any]]:
    return ACTIVE_USERS.get(str(user_id))

# In-memory tasks cannot resume after a restart; do not revive stale locks.
ACTIVE_USERS = {}

async def upd_dlg(c):
    try:
        async for _ in c.get_dialogs(limit=100): pass
        return True
    except Exception as e:
        print(f'Failed to update dialogs: {e}')
        return False

# fixed the old group of 2021-2022 extraction 🌝 (buy krne ka fayda nhi ab old group) ✅
async def get_msg(c, u, i, d, lt, uid=None):
    """Look up a message without implicit joins, respecting Telegram FloodWait."""
    clients = (c, u) if lt == 'public' else (u,)
    for source in clients:
        if source is None:
            continue
        for attempt in range(2):
            try:
                result = await source.get_messages(i, int(d))
                if result and not getattr(result, 'empty', False):
                    return result
                break
            except FloodWait as exc:
                if attempt:
                    break
                remaining = int(exc.value)
                while remaining > 0:
                    if uid is not None and should_cancel(uid):
                        return None
                    delay = min(remaining, 1)
                    await asyncio.sleep(delay)
                    remaining -= delay
            except Exception as exc:
                print(f'Could not fetch source message: {type(exc).__name__}')
                break
    return None


async def get_ubot(uid):
    bt = await get_user_data_key(uid, "bot_token", None)
    if not bt: return None
    if bt.startswith("enc:v1:"):
        bt = dcs(bt[len("enc:v1:"):])
    if uid in UB: return UB.get(uid)
    try:
        bot = Client(f"user_{uid}", bot_token=bt, api_id=API_ID, api_hash=API_HASH)
        await bot.start()
        UB[uid] = bot
        return bot
    except Exception as e:
        print(f"Error starting bot for user {uid}: {e}")
        return None

async def get_uclient(uid):
    ud = await get_user_data(uid)
    ubot = UB.get(uid)
    cl = UC.get(uid)
    if cl: return cl
    if not ud: return ubot if ubot else None
    xxx = ud.get('session_string')
    if xxx:
        try:
            ss = dcs(xxx)
            gg = Client(f'{uid}_client', api_id=API_ID, api_hash=API_HASH, device_model="Tgwithrohit Saver", session_string=ss)
            await gg.start()
            await upd_dlg(gg)
            UC[uid] = gg
            return gg
        except Exception as e:
            print(f'User client error: {e}')
            return ubot
    return ubot

async def prog(c, t, C, h, m, st):
    global P
    p = c / t * 100
    interval = 10 if t >= 100 * 1024 * 1024 else 20 if t >= 50 * 1024 * 1024 else 30 if t >= 10 * 1024 * 1024 else 50
    step = int(p // interval) * interval
    if m not in P or P[m] != step or p >= 100:
        P[m] = step
        c_mb = c / (1024 * 1024)
        t_mb = t / (1024 * 1024)
        bar = '🟢' * int(p / 10) + '🔴' * (10 - int(p / 10))
        speed = c / (time.time() - st) / (1024 * 1024) if time.time() > st else 0
        eta = time.strftime('%M:%S', time.gmtime((t - c) / (speed * 1024 * 1024))) if speed > 0 else '00:00'
        await C.edit_message_text(h, m, f"__**Pyro Handler...**__\n\n{bar}\n\n⚡**__Completed__**: {c_mb:.2f} MB / {t_mb:.2f} MB\n📊 **__Done__**: {p:.2f}%\n🚀 **__Speed__**: {speed:.2f} MB/s\n⏳ **__ETA__**: {eta}\n\n**__Powered by Tgwithrohit__**")
        if p >= 100: P.pop(m, None)

async def send_direct(c, m, tcid, ft=None, rtmid=None):
    try:
        if m.video:
            await c.send_video(tcid, m.video.file_id, caption=ft, duration=m.video.duration, width=m.video.width, height=m.video.height, message_thread_id=rtmid)
        elif m.video_note:
            await c.send_video_note(tcid, m.video_note.file_id, message_thread_id=rtmid)
        elif m.voice:
            await c.send_voice(tcid, m.voice.file_id, message_thread_id=rtmid)
        elif m.sticker:
            await c.send_sticker(tcid, m.sticker.file_id, message_thread_id=rtmid)
        elif m.audio:
            await c.send_audio(tcid, m.audio.file_id, caption=ft, duration=m.audio.duration, performer=m.audio.performer, title=m.audio.title, message_thread_id=rtmid)
        elif m.photo:
            photo_id = m.photo.file_id if hasattr(m.photo, 'file_id') else m.photo[-1].file_id
            await c.send_photo(tcid, photo_id, caption=ft, message_thread_id=rtmid)
        elif m.document:
            await c.send_document(tcid, m.document.file_id, caption=ft, file_name=m.document.file_name, message_thread_id=rtmid)
        else:
            return None
        return True
    except Exception as e:
        print(f'Direct send error: {e}')
        return False

async def process_msg(c, u, m, d, lt, uid, i, destination_override=None):
    try:
        selected = await get_user_data_key(uid, 'media_filter', 'all')
        kinds = {
            'photo': bool(m.photo),
            'video': bool(m.video or m.video_note),
            'audio': bool(m.audio or m.voice),
            'document': bool(m.document),
            'text': bool(m.text and not m.media),
        }
        if selected != 'all' and not kinds.get(selected, False):
            return 'Skipped: media filter.'
        cfg_chat = destination_override if destination_override is not None else await get_user_data_key(d, 'chat_id', None)

        tcid = d
        rtmid = None
        if cfg_chat:
            if '/' in cfg_chat:
                parts = cfg_chat.split('/', 1)
                tcid = int(parts[0])
                rtmid = int(parts[1]) if len(parts) > 1 else None
            else:
                tcid = int(cfg_chat)

        if m.media:
            remove_caption = await get_user_data_key(uid, 'remove_caption', False)
            orig_text = m.caption.markdown if m.caption and not remove_caption else ''
            proc_text = await process_text_with_rules(d, orig_text)
            user_cap = await get_user_data_key(d, 'caption', '')
            ft = f'{proc_text}\n\n{user_cap}' if proc_text and user_cap else user_cap if user_cap else proc_text

            if lt == 'public':
                direct = await send_direct(c, m, tcid, ft, rtmid)
                if direct is True:
                    return 'Sent directly.'
                if direct is False:
                    # A network error can follow a successful send. Never
                    # automatically fall back to another upload in that case.
                    return 'Failed: direct delivery result unknown.'

            st = time.time()
            p = await c.send_message(d, 'Downloading...')

            c_name = f"{time.time()}"
            if m.video:
                file_name = m.video.file_name
                if not file_name:
                    file_name = f"{time.time()}.mp4"
                    c_name = sanitize(file_name)
            elif m.audio:
                file_name = m.audio.file_name
                if not file_name:
                    file_name = f"{time.time()}.mp3"
                    c_name = sanitize(file_name)
            elif m.document:
                file_name = m.document.file_name
                if not file_name:
                    file_name = f"{time.time()}"
                else:
                    c_name = sanitize(file_name)
            elif m.photo:
                file_name = f"{time.time()}.jpg"
                c_name = sanitize(file_name)

            f = await u.download_media(m, file_name=c_name, progress=prog, progress_args=(c, d, p.id, st))

            if not f:
                await c.edit_message_text(d, p.id, 'Failed.')
                return 'Failed.'

            await c.edit_message_text(d, p.id, 'Renaming...')
            if (
                (m.video and m.video.file_name) or
                (m.audio and m.audio.file_name) or
                (m.document and m.document.file_name)
            ):
                f = await rename_file(f, d, p)

            fsize = os.path.getsize(f) / (1024 * 1024 * 1024)
            th = thumbnail(d)

            if fsize > 2 and Y:
                st = time.time()
                await c.edit_message_text(d, p.id, 'File is larger than 2GB. Using alternative method...')
                await upd_dlg(Y)
                mtd = await get_video_metadata(f)
                dur, h, w = mtd['duration'], mtd['width'], mtd['height']
                th = await screenshot(f, dur, d)

                send_funcs = {'video': Y.send_video, 'video_note': Y.send_video_note,
                            'voice': Y.send_voice, 'audio': Y.send_audio,
                            'photo': Y.send_photo, 'document': Y.send_document}

                for mtype, func in send_funcs.items():
                    if f.endswith('.mp4'): mtype = 'video'
                    if getattr(m, mtype, None):
                        sent = await func(LOG_GROUP, f, thumb=th if mtype == 'video' else None,
                                        duration=dur if mtype == 'video' else None,
                                        height=h if mtype == 'video' else None,
                                        width=w if mtype == 'video' else None,
                                        caption=ft if m.caption and mtype not in ['video_note', 'voice'] else None,
                                        progress=prog, progress_args=(c, d, p.id, st))
                        break
                else:
                    sent = await Y.send_document(LOG_GROUP, f, thumb=th, caption=ft or None,
                                                progress=prog, progress_args=(c, d, p.id, st))

                await c.copy_message(tcid, LOG_GROUP, sent.id, message_thread_id=rtmid)
                os.remove(f)
                await c.delete_messages(d, p.id)

                return 'Done (Large file).'

            await c.edit_message_text(d, p.id, 'Uploading...')
            st = time.time()

            try:
                video_extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ogv']
                audio_extensions = ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus', '.aiff', '.ac3']
                file_ext = os.path.splitext(f)[1].lower()
                if m.video or (m.document and file_ext in video_extensions):
                    mtd = await get_video_metadata(f)
                    dur, h, w = mtd['duration'], mtd['width'], mtd['height']
                    th = await screenshot(f, dur, d)
                    await c.send_video(tcid, video=f, caption=ft or None,
                                    thumb=th, width=w, height=h, duration=dur,
                                    progress=prog, progress_args=(c, d, p.id, st),
                                    message_thread_id=rtmid)
                elif m.video_note:
                    await c.send_video_note(tcid, video_note=f, progress=prog,
                                        progress_args=(c, d, p.id, st), message_thread_id=rtmid)
                elif m.voice:
                    await c.send_voice(tcid, f, progress=prog, progress_args=(c, d, p.id, st),
                                    message_thread_id=rtmid)
                elif m.sticker:
                    await c.send_sticker(tcid, m.sticker.file_id, message_thread_id=rtmid)
                elif m.audio or (m.document and file_ext in audio_extensions):
                    await c.send_audio(tcid, audio=f, caption=ft or None,
                                    thumb=th, progress=prog, progress_args=(c, d, p.id, st),
                                    message_thread_id=rtmid)
                elif m.photo:
                    await c.send_photo(tcid, photo=f, caption=ft or None,
                                    progress=prog, progress_args=(c, d, p.id, st),
                                    message_thread_id=rtmid)
                elif m.document:
                    await c.send_document(tcid, document=f, caption=ft or None,
                                        progress=prog, progress_args=(c, d, p.id, st),
                                        message_thread_id=rtmid)
                else:
                    await c.send_document(tcid, document=f, caption=ft or None,
                                        progress=prog, progress_args=(c, d, p.id, st),
                                        message_thread_id=rtmid)
            except Exception as e:
                await c.edit_message_text(d, p.id, f'Upload failed: {str(e)[:30]}')
                if os.path.exists(f): os.remove(f)
                return 'Failed.'

            os.remove(f)
            await c.delete_messages(d, p.id)

            return 'Done.'

        elif m.text:
            text = await process_text_with_rules(uid, m.text.markdown)
            await c.send_message(tcid, text=text, message_thread_id=rtmid)
            return 'Sent.'
    except Exception as e:
        return f'Error: {str(e)[:50]}'

@X.on_message(filters.private & filters.command(['batch', 'single']))
async def process_cmd(c, m):
    await begin_processing(c, m, m.from_user.id, m.command[0])

async def begin_processing(c, m, uid, cmd):
    language = await localization.language_for(uid)
    tr = lambda text: localization.translate(text, language)
    if FREEMIUM_LIMIT == 0 and not await is_premium_user(uid):
        await m.reply_text(tr("This bot does not provide free servies, get subscription from OWNER"))
        return

    if await sub(c, m, uid) == 1: return
    pro = await m.reply_text(tr('Doing some checks hold on...'))

    from plugins.batch_queue import has_unfinished, lock_for
    from utils.custom_filters import get_user_step
    async with lock_for(uid):
        if uid in Z or get_user_step(uid):
            await pro.edit(tr('An input is already in progress. Cancel it before starting another.'))
            return
        if is_user_active(uid) or await has_unfinished(uid):
            await pro.edit(tr('An unfinished task exists. Open Task Status to resume or stop it.'))
            return
        ubot = await get_ubot(uid)
        if not ubot:
            await pro.edit(tr('⚠️ Add your custom bot from the Account menu first.'))
            return
        Z[uid] = {'step': 'start' if cmd in ('batch', 'topic_batch') else 'start_single', 'topic_mode': cmd == 'topic_batch', 'language': language}
    cancel_buttons = InlineKeyboardMarkup([[InlineKeyboardButton(localization.button_label('cancel', '❌ 𝗖𝗔𝗡𝗖𝗘𝗟', language), callback_data='ui:cancel')]])
    prompt = ('Send a link containing BOTH topic and post IDs, such as https://t.me/c/123456/12/99. Set a destination forum topic first. You will choose how many consecutive message IDs to scan.' if cmd == 'topic_batch' else 'Send the first message link. Normal batch scans consecutive message IDs across the whole chat, including other topics. Use Topic Batch to restrict a forum topic.' if cmd == 'batch' else 'Send the message link.')
    await pro.edit(tr(prompt), reply_markup=cancel_buttons)

@X.on_message(filters.private & filters.command(['cancel', 'stop']))
async def cancel_cmd(c, m):
    uid = m.from_user.id
    if is_user_active(uid):
        if await request_batch_cancel(uid):
            await m.reply_text('Cancellation requested. The current batch will stop after the current download completes.')
        else:
            await m.reply_text('Failed to request cancellation. Please try again.')
    else:
        await m.reply_text('No active batch process found.')

@X.on_message(filters.text & filters.private & ~login_in_progress & ~filters.command([
    'start', 'batch', 'cancel', 'login', 'logout', 'stop', 'set',
    'pay', 'redeem', 'gencode', 'single', 'generate', 'keyinfo', 'encrypt', 'decrypt', 'keys', 'setbot', 'rembot']))
async def text_handler(c, m):
    uid = m.from_user.id
    if uid not in Z: return
    s = Z[uid].get('step')
    language = Z[uid].get('language', 'en')
    tr = lambda text: localization.translate(text, language)
    x = await get_ubot(uid)
    if not x:
        await m.reply(tr('⚠️ Add your custom bot from the Account menu first.'))
        return

    # Another input or cancellation may have changed state during client lookup.
    if uid not in Z or Z[uid].get('step') != s:
        return

    if s == 'start':
        L = m.text
        i, d, lt, topic = parse_message_link(L)
        if not i or not d:
            await m.reply_text(tr('⚠️ Invalid link. Send a Telegram message link or cancel.'))
            return
        topic_mode = Z[uid].get('topic_mode', False)
        if topic_mode and topic is None:
            await m.reply_text(tr('⚠️ Send a link containing both topic and post IDs, for example https://t.me/c/123456/12/99.'))
            return
        if not topic_mode and topic is not None:
            await m.reply_text(tr('⚠️ This link includes a topic. Cancel and choose Topic Batch to avoid scanning other topics.'))
            return
        Z[uid].update({'step': 'count', 'cid': i, 'sid': d, 'lt': lt, 'source_topic': topic})
        prompt = ('How many consecutive message IDs should be scanned? Other topics are skipped; this is not the number of posts copied.' if topic_mode else 'How many messages?')
        await m.reply_text(tr(prompt), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(localization.button_label('cancel', '❌ 𝗖𝗔𝗡𝗖𝗘𝗟', language), callback_data='ui:cancel')]]))

    elif s == 'start_single':
        L = m.text
        i, d, lt = E(L)
        if not i or not d:
            await m.reply_text(tr('Invalid link format.'))
            Z.pop(uid, None)
            return

        flow = Z[uid]
        flow.update({'step': 'process_single', 'cid': i, 'sid': d, 'lt': lt})
        pt = await m.reply_text(tr('📥 Preparing the post...'))
        from plugins.batch_queue import lock_for, has_unfinished
        from utils.custom_filters import get_user_step
        registered = False
        try:
            async with lock_for(uid):
                if Z.get(uid) is not flow:
                    return
                if get_user_step(uid) or is_user_active(uid) or await has_unfinished(uid):
                    await pt.edit(tr('⚠️ Finish your existing login or task first.'))
                    return
                ubot = await get_ubot(uid)
                uc = await get_uclient(uid) if ubot else None
                if Z.get(uid) is not flow:
                    return
                if not ubot or not uc:
                    await pt.edit(tr('⚠️ Connect your custom bot and account first.'))
                    return
                await add_active_batch(uid, {'kind': 'single', 'total': 1, 'current': 0,
                    'cancel_requested': False, 'paused': False, 'progress_message_id': pt.id})
                registered = True
            if should_cancel(uid):
                await pt.edit(tr('🛑 Post processing was cancelled before delivery.'))
                return
            msg = await get_msg(ubot, uc, i, d, lt, uid)
            if should_cancel(uid):
                await pt.edit(tr('🛑 Post processing was cancelled before delivery.'))
            elif msg:
                res = await process_msg(ubot, uc, msg, str(m.chat.id), lt, uid, i)
                await pt.edit(f'1/1: {tr(res)}')
            else:
                await pt.edit(tr('Message not found'))
        except Exception:
            await pt.edit(tr('⚠️ Could not process this post. Delivery may be uncertain; check your destination before retrying.'))
        finally:
            async with lock_for(uid):
                if registered:
                    await remove_active_batch(uid)
                if Z.get(uid) is flow:
                    Z.pop(uid, None)

    elif s == 'count':
        if not m.text.isascii() or not m.text.isdigit() or len(m.text) > 6:
            await m.reply_text(tr('Enter a valid batch size.'))
            return

        count = int(m.text)
        Z[uid]['step'] = 'validating'
        maxlimit = PREMIUM_LIMIT if await is_premium_user(uid) else FREEMIUM_LIMIT

        if count < 1 or count > maxlimit:
            if uid in Z and Z[uid].get('step') == 'validating':
                Z[uid]['step'] = 'count'
            await m.reply_text(tr(f'Maximum limit is {maxlimit}.'))
            return

        from plugins.batch_queue import start_new, MAX_BATCH_ITEMS
        if count > MAX_BATCH_ITEMS:
            if uid in Z and Z[uid].get('step') == 'validating':
                Z[uid]['step'] = 'count'
            await m.reply_text(tr(f'Maximum batch size is {MAX_BATCH_ITEMS}.'))
            return
        if uid not in Z or Z[uid].get('step') != 'validating':
            return
        source, first_id, link_type = Z[uid]['cid'], Z[uid]['sid'], Z[uid]['lt']
        flow = Z[uid]
        flow['step'] = 'process'
        pt = await m.reply_text(tr('📚 Preparing batch...'))
        try:
            ok, reason = await start_new(uid, source, first_id, count, link_type, pt,
                                         still_wanted=lambda: Z.get(uid) is flow, source_topic=flow.get('source_topic'), language=language)
            await pt.edit(('📚 ' if ok else '⚠️ ') + tr(reason))
        except Exception:
            await pt.edit(tr('⚠️ Could not start or update the batch. Check Task Status before trying again.'))
        finally:
            if Z.get(uid) is flow:
                Z.pop(uid, None)

