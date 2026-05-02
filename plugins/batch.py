# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

import os, re, time, asyncio, json, asyncio 
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import UserNotParticipant
from config import API_ID, API_HASH, LOG_GROUP, STRING, FORCE_SUB, FREEMIUM_LIMIT, PREMIUM_LIMIT, STORAGE_CHANNEL_ID
from utils.func import get_user_data, screenshot, thumbnail, get_video_metadata
from utils.func import get_user_data_key, process_text_with_rules, is_premium_user, E
from utils.func import create_vault_collection, add_vault_file, cache_source_file, get_cached_source_file
from shared_client import app as X
from plugins.settings import rename_file
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

async def update_batch_progress(user_id: int, current: int, success: int):
    if str(user_id) in ACTIVE_USERS:
        ACTIVE_USERS[str(user_id)]["current"] = current
        ACTIVE_USERS[str(user_id)]["success"] = success
        await save_active_users_to_file()

async def request_batch_cancel(user_id: int):
    if str(user_id) in ACTIVE_USERS:
        ACTIVE_USERS[str(user_id)]["cancel_requested"] = True
        await save_active_users_to_file()
        return True
    return False

def should_cancel(user_id: int) -> bool:
    user_str = str(user_id)
    return user_str in ACTIVE_USERS and ACTIVE_USERS[user_str].get("cancel_requested", False)

async def remove_active_batch(user_id: int):
    if str(user_id) in ACTIVE_USERS:
        del ACTIVE_USERS[str(user_id)]
        await save_active_users_to_file()

def get_batch_info(user_id: int) -> Optional[Dict[str, Any]]:
    return ACTIVE_USERS.get(str(user_id))

ACTIVE_USERS = load_active_users()


def parse_source_input(text):
    raw = text.strip()
    i, d, lt = E(raw)
    if i and d:
        return str(i), int(d), lt, 1

    m = re.match(r'^(-?\d+)\s+(\d+)(?:\s+(\d+))?$', raw)
    if not m:
        return None

    chat_id = m.group(1)
    msg_id = int(m.group(2))
    count = int(m.group(3)) if m.group(3) else 1
    return chat_id, msg_id, 'private', count

async def upd_dlg(c):
    try:
        async for _ in c.get_dialogs(limit=100): pass
        return True
    except Exception as e:
        print(f'Failed to update dialogs: {e}')
        return False

# fixed the old group of 2021-2022 extraction 🌝 (buy krne ka fayda nhi ab old group) ✅ 
async def get_msg(c, u, i, d, lt):
    try:
        if lt == 'public':
            try:
                if str(i).lower().endswith('bot'):
                    emp[i] = False
                    xm = await u.get_messages(i, d)
                    emp[i] = getattr(xm, "empty", False)
                    if not emp[i]:
                        emp[i] = True
                        print(f"Bot chat found successfully...")
                        return xm
                    
                if emp[i]:
                    xm = await c.get_messages(i, d)
                    print(f"fetched by {c.me.username}")
                    emp[i] = getattr(xm, "empty", False)
                    if emp[i]:
                        print(f"Not fetched by {c.me.username}")
                        try: await u.join_chat(i)
                        except: pass
                        xm = await u.get_messages((await u.get_chat(f"@{i}")).id, d)
                    
                    return xm                   
            except Exception as e:
                print(f'Error fetching public message: {e}')
                return None
        else:
            if u:
                try:
                    async for _ in u.get_dialogs(limit=50): pass
                    
                    # Try with -100 prefix first
                    if str(i).startswith('-100'):
                        chat_id_100 = i
                        # For - prefix, remove -100 and add just -
                        base_id = str(i)[4:]  # Remove -100
                        chat_id_dash = f"-{base_id}"
                    elif i.isdigit():
                        chat_id_100 = f"-100{i}"
                        chat_id_dash = f"-{i}"
                    else:
                        chat_id_100 = i
                        chat_id_dash = i
                    
                    # Try -100 format first
                    try:
                        result = await u.get_messages(chat_id_100, d)
                        if result and not getattr(result, "empty", False):
                            return result
                    except Exception:
                        pass
                    
                    # Try - format second
                    try:
                        result = await u.get_messages(chat_id_dash, d)
                        if result and not getattr(result, "empty", False):
                            return result
                    except Exception:
                        pass
                    
                    # Final fallback - refresh dialogs and try original
                    try:
                        async for _ in u.get_dialogs(limit=200): pass
                        result = await u.get_messages(i, d)
                        if result and not getattr(result, "empty", False):
                            return result
                    except Exception:
                        pass
                    
                    return None
                            
                except Exception as e:
                    print(f'Private channel error: {e}')
                    return None
            return None
    except Exception as e:
        print(f'Error fetching message: {e}')
        return None


async def get_ubot(uid):
    bt = await get_user_data_key(uid, "bot_token", None)
    if not bt: return None
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
            gg = Client(f'{uid}_client', api_id=API_ID, api_hash=API_HASH, device_model="v3saver", session_string=ss)
            await gg.start()
            await upd_dlg(gg)
            UC[uid] = gg
            return gg
        except Exception as e:
            print(f'User client error: {e}')
            return ubot if ubot else Y
    return Y

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
        await C.edit_message_text(h, m, f"__**Pyro Handler...**__\n\n{bar}\n\n⚡**__Completed__**: {c_mb:.2f} MB / {t_mb:.2f} MB\n📊 **__Done__**: {p:.2f}%\n🚀 **__Speed__**: {speed:.2f} MB/s\n⏳ **__ETA__**: {eta}\n\n**__Powered by Team SPY__**")
        if p >= 100: P.pop(m, None)

async def send_direct(c, m, tcid, ft=None, rtmid=None):
    try:
        if m.video:
            await c.send_video(tcid, m.video.file_id, caption=ft, duration=m.video.duration, width=m.video.width, height=m.video.height, reply_to_message_id=rtmid)
        elif m.video_note:
            await c.send_video_note(tcid, m.video_note.file_id, reply_to_message_id=rtmid)
        elif m.voice:
            await c.send_voice(tcid, m.voice.file_id, reply_to_message_id=rtmid)
        elif m.sticker:
            await c.send_sticker(tcid, m.sticker.file_id, reply_to_message_id=rtmid)
        elif m.audio:
            await c.send_audio(tcid, m.audio.file_id, caption=ft, duration=m.audio.duration, performer=m.audio.performer, title=m.audio.title, reply_to_message_id=rtmid)
        elif m.photo:
            photo_id = m.photo.file_id if hasattr(m.photo, 'file_id') else m.photo[-1].file_id
            await c.send_photo(tcid, photo_id, caption=ft, reply_to_message_id=rtmid)
        elif m.document:
            await c.send_document(tcid, m.document.file_id, caption=ft, file_name=m.document.file_name, reply_to_message_id=rtmid)
        else:
            return False
        return True
    except Exception as e:
        print(f'Direct send error: {e}')
        return False

async def archive_and_forward(c, m, local_file, target_chat_id, reply_to_message_id, caption_text, vault_collection, source_chat_id):
    file_name = os.path.basename(local_file)
    file_ext = os.path.splitext(local_file)[1].lower()
    is_video = file_ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ogv']
    is_audio = file_ext in ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus', '.aiff', '.ac3']
    is_photo = file_ext in ['.jpg', '.jpeg', '.png', '.webp']
    file_size = os.path.getsize(local_file)
    storage_msg = None

    if is_video:
        mtd = await get_video_metadata(local_file)
        dur, h, w = mtd['duration'], mtd['height'], mtd['width']
        th = await screenshot(local_file, dur, str(target_chat_id))
        storage_msg = await c.send_video(
            STORAGE_CHANNEL_ID,
            video=local_file,
            caption=caption_text or None,
            thumb=th,
            width=w,
            height=h,
            duration=dur,
        )
    elif is_audio:
        storage_msg = await c.send_audio(
            STORAGE_CHANNEL_ID,
            audio=local_file,
            caption=caption_text or None,
        )
    elif is_photo:
        storage_msg = await c.send_photo(
            STORAGE_CHANNEL_ID,
            photo=local_file,
            caption=caption_text or None,
        )
    else:
        storage_msg = await c.send_document(
            STORAGE_CHANNEL_ID,
            document=local_file,
            caption=caption_text or None,
        )

    media = storage_msg.video or storage_msg.audio or storage_msg.photo or storage_msg.document or storage_msg.voice
    saved = await add_vault_file(
        collection_id=vault_collection["_id"] if vault_collection else None,
        source_chat_id=source_chat_id,
        source_message_id=m.id,
        storage_chat_id=STORAGE_CHANNEL_ID,
        storage_message_id=storage_msg.id,
        file_id=getattr(media, "file_id", None),
        file_unique_id=getattr(media, "file_unique_id", None),
        file_name=file_name,
        mime_type="video/mp4" if is_video else "audio/mpeg" if is_audio else "image/jpeg" if is_photo else "application/octet-stream",
        file_size=file_size,
        caption=caption_text or "",
        storage_mode="telegram_vault",
    )
    await cache_source_file(source_chat_id, m.id, saved["_id"])
    await c.copy_message(target_chat_id, STORAGE_CHANNEL_ID, storage_msg.id, reply_to_message_id=reply_to_message_id)
    return storage_msg


async def replay_cached(c, cached_file, target_chat_id, reply_to_message_id, vault_collection=None):
    if vault_collection and cached_file.get("collection_id") != vault_collection["_id"]:
        # duplicate the reference into this collection by writing a new lightweight vault file doc
        await add_vault_file(
            collection_id=vault_collection["_id"],
            source_chat_id=cached_file["source_chat_id"],
            source_message_id=cached_file["source_message_id"],
            storage_chat_id=cached_file["storage_chat_id"],
            storage_message_id=cached_file["storage_message_id"],
            file_id=cached_file["file_id"],
            file_unique_id=cached_file["file_unique_id"],
            file_name=cached_file["file_name"],
            mime_type=cached_file["mime_type"],
            file_size=cached_file["file_size"],
            caption=cached_file.get("caption", ""),
            storage_mode=cached_file.get("storage_mode", "telegram_vault"),
        )
    await c.copy_message(target_chat_id, cached_file["storage_chat_id"], cached_file["storage_message_id"], reply_to_message_id=reply_to_message_id)
    return True


async def run_batch_request(c, m, uid, ubot, uc, source_chat, start_msg_id, count, lt):
    success = 0
    collection = None
    pt = await m.reply_text('Processing batch...')

    if is_user_active(uid):
        await pt.edit('Active task exists. Use /stop first.')
        return

    await add_active_batch(uid, {
        "total": count,
        "current": 0,
        "success": 0,
        "cancel_requested": False,
        "progress_message_id": pt.id
    })

    if STORAGE_CHANNEL_ID:
        source_name = sanitize(str(source_chat))
        collection_name = f"batch_{source_name}_{int(time.time())}"
        collection = await create_vault_collection(uid, collection_name)

    try:
        for j in range(count):
            if should_cancel(uid):
                await pt.edit(f'Cancelled at {j}/{count}. Success: {success}')
                break

            await update_batch_progress(uid, j, success)
            mid = int(start_msg_id) + j

            try:
                msg = await get_msg(ubot, uc, source_chat, mid, lt)
                if msg:
                    res = await process_msg(ubot, uc, msg, str(m.chat.id), lt, uid, source_chat, vault_collection=collection)
                    if 'Done' in res or 'Copied' in res or 'Sent' in res:
                        success += 1
            except Exception as e:
                try:
                    await pt.edit(f'{j+1}/{count}: Error - {str(e)[:30]}')
                except Exception:
                    pass

            await asyncio.sleep(10)

        suffix = ""
        if collection:
            suffix = f"\n🔑 Collection key: `{collection['access_key']}`"
        await m.reply_text(f'Batch Completed ✅ Success: {success}/{count}{suffix}')
        if collection:
            try:
                from plugins.vault import _show_collection_page
                from utils.func import get_vault_collection_files
                files = await get_vault_collection_files(collection["_id"])
                if files:
                    await _show_collection_page(m, collection, files, page=1, edit=False)
            except Exception:
                pass
    finally:
        await remove_active_batch(uid)


async def run_single_request(c, m, uid, ubot, uc, source_chat, msg_id, lt):
    pt = await m.reply_text('Processing...')
    collection = None
    if STORAGE_CHANNEL_ID:
        source_name = sanitize(str(source_chat))
        collection_name = f"single_{source_name}_{int(time.time())}"
        collection = await create_vault_collection(uid, collection_name)

    try:
        msg = await get_msg(ubot, uc, source_chat, msg_id, lt)
        if msg:
            res = await process_msg(ubot, uc, msg, str(m.chat.id), lt, uid, source_chat, vault_collection=collection)
            suffix = ""
            if collection:
                suffix = f"\n🔑 Collection key: `{collection['access_key']}`"
            await pt.edit(f'1/1: {res}{suffix}')
            if collection:
                try:
                    from plugins.vault import _show_collection_page
                    from utils.func import get_vault_collection_files
                    files = await get_vault_collection_files(collection["_id"])
                    if files:
                        await _show_collection_page(m, collection, files, page=1, edit=False)
                except Exception:
                    pass
        else:
            await pt.edit('Message not found')
    except Exception as e:
        await pt.edit(f'Error: {str(e)[:50]}')

async def process_msg(c, u, m, d, lt, uid, i, vault_collection=None):
    try:
        cfg_chat = await get_user_data_key(d, 'chat_id', None)
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
            cached_file = await get_cached_source_file(i, m.id)
            if cached_file:
                await replay_cached(c, cached_file, tcid, rtmid, vault_collection=vault_collection)
                return 'Done (cached).'

            orig_text = m.caption.markdown if m.caption else ''
            proc_text = await process_text_with_rules(d, orig_text)
            user_cap = await get_user_data_key(d, 'caption', '')
            ft = f'{proc_text}\n\n{user_cap}' if proc_text and user_cap else user_cap if user_cap else proc_text
            
            if lt == 'public' and not emp.get(i, False):
                await send_direct(c, m, tcid, ft, rtmid)
                return 'Sent directly.'
            
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
                                        reply_to_message_id=rtmid, progress=prog, progress_args=(c, d, p.id, st))
                        break
                else:
                    sent = await Y.send_document(LOG_GROUP, f, thumb=th, caption=ft if m.caption else None,
                                                reply_to_message_id=rtmid, progress=prog, progress_args=(c, d, p.id, st))
                
                await c.copy_message(d, LOG_GROUP, sent.id)
                os.remove(f)
                await c.delete_messages(d, p.id)
                
                return 'Done (Large file).'
            
            await c.edit_message_text(d, p.id, 'Uploading...')
            st = time.time()

            try:
                if STORAGE_CHANNEL_ID and vault_collection:
                    await archive_and_forward(
                        c=c,
                        m=m,
                        local_file=f,
                        target_chat_id=tcid,
                        reply_to_message_id=rtmid,
                        caption_text=ft if m.caption else None,
                        vault_collection=vault_collection,
                        source_chat_id=i,
                    )
                    if os.path.exists(f):
                        os.remove(f)
                    await c.delete_messages(d, p.id)
                    return 'Done.'

                video_extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ogv']
                audio_extensions = ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus', '.aiff', '.ac3']
                file_ext = os.path.splitext(f)[1].lower()
                if m.video or (m.document and file_ext in video_extensions):
                    mtd = await get_video_metadata(f)
                    dur, h, w = mtd['duration'], mtd['width'], mtd['height']
                    th = await screenshot(f, dur, d)
                    await c.send_video(tcid, video=f, caption=ft if m.caption else None, 
                                    thumb=th, width=w, height=h, duration=dur, 
                                    progress=prog, progress_args=(c, d, p.id, st), 
                                    reply_to_message_id=rtmid)
                elif m.video_note:
                    await c.send_video_note(tcid, video_note=f, progress=prog, 
                                        progress_args=(c, d, p.id, st), reply_to_message_id=rtmid)
                elif m.voice:
                    await c.send_voice(tcid, f, progress=prog, progress_args=(c, d, p.id, st), 
                                    reply_to_message_id=rtmid)
                elif m.sticker:
                    await c.send_sticker(tcid, m.sticker.file_id, reply_to_message_id=rtmid)
                elif m.audio or (m.document and file_ext in audio_extensions):
                    await c.send_audio(tcid, audio=f, caption=ft if m.caption else None, 
                                    thumb=th, progress=prog, progress_args=(c, d, p.id, st), 
                                    reply_to_message_id=rtmid)
                elif m.photo:
                    await c.send_photo(tcid, photo=f, caption=ft if m.caption else None, 
                                    progress=prog, progress_args=(c, d, p.id, st), 
                                    reply_to_message_id=rtmid)
                elif m.document:
                    await c.send_document(tcid, document=f, caption=ft if m.caption else None, 
                                        progress=prog, progress_args=(c, d, p.id, st), 
                                        reply_to_message_id=rtmid)
                else:
                    await c.send_document(tcid, document=f, caption=ft if m.caption else None, 
                                        progress=prog, progress_args=(c, d, p.id, st), 
                                        reply_to_message_id=rtmid)
            except Exception as e:
                await c.edit_message_text(d, p.id, f'Upload failed: {str(e)[:30]}')
                if os.path.exists(f): os.remove(f)
                return 'Failed.'
            
            os.remove(f)
            await c.delete_messages(d, p.id)
            
            return 'Done.'
            
        elif m.text:
            await c.send_message(tcid, text=m.text.markdown, reply_to_message_id=rtmid)
            return 'Sent.'
    except Exception as e:
        return f'Error: {str(e)[:50]}'
        
@X.on_message(filters.command(['batch', 'single']))
async def process_cmd(c, m):
    uid = m.from_user.id
    cmd = m.command[0]
    
    if uid not in OWNER_ID and FREEMIUM_LIMIT == 0 and not await is_premium_user(uid):
        await m.reply_text("This bot does not provide free servies, get subscription from OWNER")
        return
    
    if await sub(c, m) == 1: return
    pro = await m.reply_text('Doing some checks hold on...')
    
    if is_user_active(uid):
        await pro.edit('You have an active task. Use /stop to cancel it.')
        return
    
    ubot = await get_ubot(uid)
    if not ubot:
        await pro.edit('Add your bot with /setbot first')
        return
    
    Z[uid] = {'step': 'start' if cmd == 'batch' else 'start_single'}
    await pro.edit(f'Send {"start link..." if cmd == "batch" else "link you to process"}.')

@X.on_message(filters.command(['cancel', 'stop']))
async def cancel_cmd(c, m):
    uid = m.from_user.id
    if is_user_active(uid):
        if await request_batch_cancel(uid):
            await m.reply_text('Cancellation requested. The current batch will stop after the current download completes.')
        else:
            await m.reply_text('Failed to request cancellation. Please try again.')
    else:
        await m.reply_text('No active batch process found.')


@X.on_message(filters.regex(r"^(📥 ?开始下载|📥 ?批量下载|❌ ?取消操作|🔙 ?返回主菜单)$") & filters.private)
async def legacy_button_bridge(c, m):
    uid = m.from_user.id
    if uid not in OWNER_ID:
        return

    text = m.text.strip()
    if '取消' in text:
        Z.pop(uid, None)
        await remove_active_batch(uid)
        await m.reply_text('Cancelled. Send link / IDs / collection key directly.')
        return

    if '返回' in text:
        await m.reply_text('Send link / `频道ID 消息ID [数量]` / `file_store...` directly.')
        return

    Z[uid] = {'step': 'start'}
    await m.reply_text('Send start link or `频道ID 消息ID 数量`.')

@X.on_message(filters.text & filters.private & ~login_in_progress & ~filters.command([
    'start', 'batch', 'cancel', 'login', 'logout', 'stop', 'set', 
    'pay', 'redeem', 'gencode', 'single', 'generate', 'keyinfo', 'encrypt', 'decrypt', 'keys', 'setbot', 'rembot']))
async def text_handler(c, m):
    uid = m.from_user.id
    if uid not in Z: return
    s = Z[uid].get('step')
    x = await get_ubot(uid)
    if not x:
        await m.reply("Add your bot /setbot `token`")
        return

    if s == 'start':
        L = m.text
        i, d, lt = E(L)
        if not i or not d:
            await m.reply_text('Invalid link format.')
            Z.pop(uid, None)
            return
        Z[uid].update({'step': 'count', 'cid': i, 'sid': d, 'lt': lt})
        await m.reply_text('How many messages?')

    elif s == 'start_single':
        L = m.text
        i, d, lt = E(L)
        if not i or not d:
            await m.reply_text('Invalid link format.')
            Z.pop(uid, None)
            return

        Z[uid].update({'step': 'process_single', 'cid': i, 'sid': d, 'lt': lt})
        i, s, lt = Z[uid]['cid'], Z[uid]['sid'], Z[uid]['lt']
        ubot = UB.get(uid)
        if not ubot:
            await m.reply_text('Add bot with /setbot first')
            Z.pop(uid, None)
            return
        
        uc = await get_uclient(uid)
        if not uc:
            await m.reply_text('Cannot proceed without user client.')
            Z.pop(uid, None)
            return
            
        if is_user_active(uid):
            await m.reply_text('Active task exists. Use /stop first.')
            Z.pop(uid, None)
            return

        try:
            await run_single_request(ubot, m, uid, ubot, uc, i, s, lt)
        finally:
            Z.pop(uid, None)

    elif s == 'count':
        if not m.text.isdigit():
            await m.reply_text('Enter valid number.')
            return
        
        count = int(m.text)
        maxlimit = PREMIUM_LIMIT if (uid in OWNER_ID or await is_premium_user(uid)) else FREEMIUM_LIMIT

        if count > maxlimit:
            await m.reply_text(f'Maximum limit is {maxlimit}.')
            return

        Z[uid].update({'step': 'process', 'did': str(m.chat.id), 'num': count})
        i, s, n, lt = Z[uid]['cid'], Z[uid]['sid'], Z[uid]['num'], Z[uid]['lt']
        uc = await get_uclient(uid)
        ubot = UB.get(uid)
        
        if not uc or not ubot:
            await m.reply_text('Missing client setup')
            Z.pop(uid, None)
            return
        try:
            await run_batch_request(ubot, m, uid, ubot, uc, i, s, n, lt)
        finally:
            Z.pop(uid, None)


@X.on_message(filters.text & filters.private & ~login_in_progress & ~filters.command([
    'start', 'batch', 'cancel', 'login', 'logout', 'stop', 'set',
    'pay', 'redeem', 'gencode', 'single', 'generate', 'keyinfo', 'encrypt', 'decrypt', 'keys', 'setbot', 'rembot'
]))
async def direct_input_handler(c, m):
    uid = m.from_user.id
    if uid not in OWNER_ID or uid in Z:
        return

    parsed = parse_source_input(m.text)
    if not parsed:
        return

    source_chat, start_msg_id, lt, count = parsed
    ubot = await get_ubot(uid)
    uc = await get_uclient(uid)
    if not ubot or not uc:
        await m.reply_text('Missing client setup')
        return

    if count <= 1:
        await run_single_request(ubot, m, uid, ubot, uc, source_chat, start_msg_id, lt)
        return

    await run_batch_request(ubot, m, uid, ubot, uc, source_chat, start_msg_id, count, lt)



