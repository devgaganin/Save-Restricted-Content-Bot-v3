# ---------------------------------------------------
# File Name: ytdl.py (pure code)
# Description: A Pyrogram bot for downloading yt and other sites videos from Telegram channels or groups
#              and uploading them back to Telegram.
# Author: Gagan
# GitHub: https://github.com/devgaganin/
# Telegram: https://t.me/tgwithrohit
# YouTube: https://youtube.com/@dev_gagan
# Created: 2025-01-11
# Last Modified: 2025-01-11
# Version: 2.0.5
# License: MIT License
# ---------------------------------------------------

import yt_dlp
import os
import tempfile
import time
import asyncio
import logging
import math
from shared_client import client, app
from telethon import events
from telethon.tl.types import DocumentAttributeVideo
from pyrogram.enums import ParseMode
from urllib.parse import urlsplit
from utils.func import get_video_metadata, thumbnail
from devgagantools import fast_upload
import aiofiles
from config import YT_COOKIES, INSTA_COOKIES, ENABLE_EXTERNAL_DOWNLOADS
from mutagen.id3 import ID3, TIT2, TPE1, COMM
from mutagen.mp3 import MP3

logger = logging.getLogger(__name__)


ongoing_downloads = {}

QUALITY_FORMATS = {
    'auto': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
    **{str(h): f'bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]/best[ext=mp4][height<={h}]'
       for h in (360, 480, 720, 1080)},
}


def valid_external_url(url):
    """Basic syntax only; network isolation is still mandatory for SSRF."""
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme.lower() in ('http', 'https') and parsed.hostname
                    and not parsed.username and not parsed.password
                    and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        return False


def cookies_for_url(url):
    """Only use configured provider cookies for the actual matching hostname."""
    if not valid_external_url(url):
        return None
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:
        return None
    def belongs(domain):
        return host == domain or host.endswith('.' + domain)
    cookies = INSTA_COOKIES if belongs('instagram.com') else YT_COOKIES if belongs('youtube.com') or belongs('youtu.be') else None
    # Default template comments are not a usable Netscape cookie file.
    if not cookies or not any(line.strip() and (not line.lstrip().startswith('#') or line.startswith('#HttpOnly_')) for line in cookies.splitlines()):
        return None
    return cookies


class SafeYdlLogger:
    # Extractor diagnostics may contain private URLs or cookie paths.
    def debug(self, message):
        pass

    def warning(self, message):
        logger.warning('yt-dlp reported a warning')

    def error(self, message):
        logger.error('yt-dlp reported an error')


async def await_thread_completion(func, *args):
    """Do not remove temporary files while a cancelled worker still uses them."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            logger.warning('Downloader worker failed while shutting down')
        raise


async def extract_audio_async(ydl_opts, url):
    def sync_extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)
    return await await_thread_completion(sync_extract)


async def process_audio(client, event, url, cookies_env_var=None):
    if not ENABLE_EXTERNAL_DOWNLOADS:
        await event.reply('⚠️ External downloads are disabled.')
        return
    if not valid_external_url(url):
        await event.reply('⚠️ Please provide a valid HTTP(S) URL.')
        return
    # Unique per-request directory keeps partial files and credentials isolated.
    with tempfile.TemporaryDirectory(prefix='tg_audio_') as temp_dir:
        cookie_path = None
        if cookies_env_var:
            cookie_path = os.path.join(temp_dir, 'cookies.txt')
            with open(cookie_path, 'w', encoding='utf-8') as cookie_file:
                cookie_file.write(cookies_env_var)
        download_path = os.path.join(temp_dir, 'audio.mp3')
        ydl_opts = {'format': 'bestaudio/best', 'outtmpl': os.path.join(temp_dir, 'audio.%(ext)s'),
                    'cookiefile': cookie_path, 'postprocessors': [{'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3', 'preferredquality': '192'}], 'quiet': True, 'noplaylist': True,
                    'logger': SafeYdlLogger()}
        try:
            progress_message = await event.reply('🎵 Starting audio extraction...')
            info = await extract_audio_async(ydl_opts, url)
            title = str(info.get('title') or 'Extracted Audio')[:900]
            if not os.path.isfile(download_path):
                await event.reply('⚠️ Audio file was not produced.')
                return
            await progress_message.edit('🎵 Preparing audio...')
            def edit_metadata():
                audio_file = MP3(download_path, ID3=ID3)
                try:
                    audio_file.add_tags()
                except Exception:
                    pass
                audio_file.tags['TIT2'] = TIT2(encoding=3, text=title)
                audio_file.tags['TPE1'] = TPE1(encoding=3, text='Tgwithrohit')
                audio_file.tags['COMM'] = COMM(encoding=3, lang='eng', desc='Comment', text='Processed by Tgwithrohit')
                audio_file.save()
            await await_thread_completion(edit_metadata)
            await progress_message.delete()
            prog = await client.send_message(event.chat_id, '⬆️ Starting upload...')
            uploaded = await fast_upload(client, download_path, reply=prog, name=None,
                progress_bar_function=lambda done, total: progress_callback(done, total, event.chat_id))
            await client.send_file(event.chat_id, uploaded, caption=title, parse_mode=None)
            await prog.delete()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Audio download or delivery failed: %s', type(exc).__name__)
            await event.reply('⚠️ Audio download or delivery failed. Check the destination before retrying.')


@client.on(events.NewMessage(pattern="/adl"))
async def audio_handler(event):
    if not event.is_private or not ENABLE_EXTERNAL_DOWNLOADS:
        await event.reply("⚠️ External downloads are disabled or unavailable in this chat.")
        return
    user_id = event.sender_id
    if user_id in ongoing_downloads:
        await event.reply("**You already have an ongoing download. Please wait until it completes!**")
        return

    if len(event.message.text.split()) < 2:
        await event.reply("**Usage:** `/adl <video-link>`\n\nPlease provide a valid video link!")
        return

    url = event.message.text.split()[1]
    ongoing_downloads[user_id] = True

    try:
        await process_audio(client, event, url, cookies_for_url(url))
    except Exception:
        await event.reply('⚠️ Audio download failed. Check the destination before retrying.')
    finally:
        ongoing_downloads.pop(user_id, None)
        user_progress.pop(user_id, None)



async def fetch_video_info(url, ydl_opts, progress_message, check_duration_and_size):
    def extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    info_dict = await await_thread_completion(extract)
    if check_duration_and_size:

        duration = info_dict.get('duration', 0)
        if duration and duration > 3 * 3600:
            await progress_message.edit("**❌ __Video is longer than 3 hours. Download aborted...__**")
            return None


        estimated_size = info_dict.get('filesize_approx', 0)
        if estimated_size and estimated_size > 2 * 1024 * 1024 * 1024:
            await progress_message.edit("**🤞 __Video size is larger than 2GB. Aborting download.__**")
            return None

    return info_dict

def download_video(url, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


@client.on(events.NewMessage(pattern="/dl"))
async def video_handler(event):
    if not event.is_private or not ENABLE_EXTERNAL_DOWNLOADS:
        await event.reply("⚠️ External downloads are disabled or unavailable in this chat.")
        return
    user_id = event.sender_id


    if user_id in ongoing_downloads:
        await event.reply("**You already have an ongoing ytdlp download. Please wait until it completes!**")
        return

    if len(event.message.text.split()) < 2:
        await event.reply("**Usage:** `/dl <video-link>`\n\nPlease provide a valid video link!")
        return

    url = event.message.text.split()[1]


    ongoing_downloads[user_id] = True
    try:
        await process_video(client, event, url, cookies_for_url(url), check_duration_and_size=True)
    except Exception:
        await event.reply('⚠️ Video download failed. Check the destination before retrying.')
    finally:
        ongoing_downloads.pop(user_id, None)
        user_progress.pop(user_id, None)





user_progress = {}

def progress_callback(done, total, user_id):

    if user_id not in user_progress:
        user_progress[user_id] = {
            'previous_done': 0,
            'previous_time': time.time()
        }


    user_data = user_progress[user_id]


    percent = (done / total) * 100 if total else 0


    completed_blocks = int(percent // 10)
    remaining_blocks = 10 - completed_blocks
    progress_bar = "♦" * completed_blocks + "◇" * remaining_blocks


    done_mb = done / (1024 * 1024)
    total_mb = total / (1024 * 1024)


    speed = done - user_data['previous_done']
    elapsed_time = time.time() - user_data['previous_time']

    if elapsed_time > 0:
        speed_bps = speed / elapsed_time
        speed_mbps = (speed_bps * 8) / (1024 * 1024)
    else:
        speed_bps = 0
        speed_mbps = 0


    if speed_bps > 0:
        remaining_time = (total - done) / speed_bps
    else:
        remaining_time = 0


    remaining_time_min = remaining_time / 60


    final = (
        f"╭──────────────────╮\n"
        f"│        **__Uploading...__**       \n"
        f"├──────────\n"
        f"│ {progress_bar}\n\n"
        f"│ **__Progress:__** {percent:.2f}%\n"
        f"│ **__Done:__** {done_mb:.2f} MB / {total_mb:.2f} MB\n"
        f"│ **__Speed:__** {speed_mbps:.2f} Mbps\n"
        f"│ **__Time Remaining:__** {remaining_time_min:.2f} min\n"
        f"╰──────────────────╯\n\n"
        f"**__Powered by Tgwithrohit__**"
    )


    user_data['previous_done'] = done
    user_data['previous_time'] = time.time()

    return final

async def process_video(client, event, url, cookies_env_var=None, check_duration_and_size=False, quality='auto'):
    if not ENABLE_EXTERNAL_DOWNLOADS:
        await event.reply('⚠️ External downloads are disabled.')
        return
    if not valid_external_url(url):
        await event.reply('⚠️ Please provide a valid HTTP(S) URL.')
        return
    if quality not in QUALITY_FORMATS:
        await event.reply('⚠️ Invalid video quality.')
        return
    # No thumbnails fetched from extractor URLs. A user's existing thumbnail may
    # be used, while all downloader output and cookies are kept in a temp dir.
    with tempfile.TemporaryDirectory(prefix='tg_video_') as temp_dir:
        cookie_path = None
        if cookies_env_var:
            cookie_path = os.path.join(temp_dir, 'cookies.txt')
            with open(cookie_path, 'w', encoding='utf-8') as cookie_file:
                cookie_file.write(cookies_env_var)
        download_path = os.path.join(temp_dir, 'video.mp4')
        ydl_opts = {'outtmpl': os.path.join(temp_dir, 'video.%(ext)s'),
                    'format': QUALITY_FORMATS[quality], 'merge_output_format': 'mp4',
                    'cookiefile': cookie_path, 'noplaylist': True, 'quiet': True, 'logger': SafeYdlLogger()}
        try:
            progress_message = await event.reply('🎥 Starting video download...')
            info = await fetch_video_info(url, ydl_opts, progress_message, check_duration_and_size)
            if not info:
                return
            await await_thread_completion(download_video, url, ydl_opts)
            if not os.path.isfile(download_path):
                await event.reply('⚠️ The requested MP4 format was not produced.')
                return
            title = str(info.get('title') or 'Video')[:900]
            meta = await get_video_metadata(download_path)
            chat_id = event.chat_id
            if os.path.getsize(download_path) > 2 * 1024 * 1024 * 1024:
                prog = await client.send_message(chat_id, '⬆️ Starting upload...')
                await split_and_upload_file(app, chat_id, download_path, title)
                await prog.delete()
                await progress_message.delete()
                return
            await progress_message.delete()
            prog = await client.send_message(chat_id, '⬆️ Starting upload...')
            uploaded = await fast_upload(client, download_path, reply=prog,
                progress_bar_function=lambda done, total: progress_callback(done, total, chat_id))
            await client.send_file(chat_id, uploaded, caption=title, parse_mode=None,
                attributes=[DocumentAttributeVideo(duration=meta['duration'], w=meta['width'],
                            h=meta['height'], supports_streaming=True)],
                thumb=thumbnail(str(event.sender_id)))
            await prog.delete()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Video download or delivery failed: %s', type(exc).__name__)
            await event.reply('⚠️ Video download or delivery failed, or the selected MP4 quality is unavailable. Check the destination before retrying.')


async def split_and_upload_file(app, sender, file_path, caption):
    """Split on disk with bounded buffers, and remove each part on failure."""
    if not os.path.isfile(file_path):
        await app.send_message(sender, "❌ File not found!")
        return
    part_size = int(1.9 * 1024 * 1024 * 1024)
    chunk_size = 1024 * 1024
    part_number = 0
    async with aiofiles.open(file_path, mode="rb") as source:
        while True:
            first_chunk = await source.read(chunk_size)
            if not first_chunk:
                break
            base, ext = os.path.splitext(file_path)
            part_file = f"{base}.part{part_number:03d}{ext}"
            try:
                async with aiofiles.open(part_file, mode="wb") as part:
                    await part.write(first_chunk)
                    written = len(first_chunk)
                    while written < part_size:
                        data = await source.read(min(chunk_size, part_size - written))
                        if not data:
                            break
                        await part.write(data)
                        written += len(data)
                status = await app.send_message(sender, f"⬆️ Uploading part {part_number + 1}...")
                try:
                    await app.send_document(sender, document=part_file,
                        caption=f"{caption}\n\nPart: {part_number + 1}", parse_mode=ParseMode.DISABLED,
                        progress=progress_bar,
                        progress_args=("╭─────────────────────╮\n│      **__Uploader__**\n├─────────────────────", status, time.time()))
                finally:
                    await status.delete()
            finally:
                if os.path.exists(part_file):
                    os.remove(part_file)
            part_number += 1


PROGRESS_BAR = """
│ **__Completed:__** {1}/{2}
│ **__Bytes:__** {0}%
│ **__Speed:__** {3}/s
│ **__ETA:__** {4}
╰─────────────────────╯
"""

async def get_seconds(time_string: str) -> int:
    """
    Converts a time string (e.g., '5min', '2hour') into seconds.
    """
    def extract_value_and_unit(ts: str):
        value = ''.join(filter(str.isdigit, ts))
        unit = ts[len(value):].strip()
        return int(value) if value else 0, unit

    value, unit = extract_value_and_unit(time_string)
    time_units = {
        's': 1,
        'min': 60,
        'hour': 3600,
        'day': 86400,
        'month': 86400 * 30,
        'year': 86400 * 365
    }

    return value * time_units.get(unit, 0)

async def progress_bar(current: int, total: int, ud_type: str, message, start: float):
    """
    Updates the progress bar for an ongoing process.
    """
    now = time.time()
    diff = now - start

    if round(diff % 10) == 0 or current == total:
        percentage = (current * 100) / total
        speed = current / diff if diff else 0
        elapsed_time = round(diff * 1000)
        time_to_completion = round((total - current) / speed) * 1000 if speed else 0
        estimated_total_time = elapsed_time + time_to_completion

        elapsed_time_str = TimeFormatter(elapsed_time)
        estimated_total_time_str = TimeFormatter(estimated_total_time)

        progress = "".join(["♦" for _ in range(math.floor(percentage / 10))]) + \
                   "".join(["◇" for _ in range(10 - math.floor(percentage / 10))])

        progress_text = progress + PROGRESS_BAR.format(
            round(percentage, 2),
            humanbytes(current),
            humanbytes(total),
            humanbytes(speed),
            estimated_total_time_str if estimated_total_time_str else "0 s"
        )
        try:
            await message.edit(text=f"{ud_type}\n│ {progress_text}")
        except:
            pass

def humanbytes(size: int) -> str:
    """
    Converts bytes into a human-readable format.
    """
    if not size:
        return ""

    power = 2**10
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    n = 0
    while size > power and n < len(units) - 1:
        size /= power
        n += 1

    return f"{round(size, 2)} {units[n]}"

def TimeFormatter(milliseconds: int) -> str:
    """
    Formats milliseconds into a human-readable duration.
    """
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if seconds: parts.append(f"{seconds}s")
    if milliseconds: parts.append(f"{milliseconds}ms")

    return ', '.join(parts)

def convert(seconds: int) -> str:
    """
    Converts seconds into HH:MM:SS format.
    """
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
