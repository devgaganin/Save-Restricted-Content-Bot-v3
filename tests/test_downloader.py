"""Downloader and menu tests with no external services or network access."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, menu, ytdl
from utils.custom_filters import user_steps
import config
import shared_client


def callback(action):
    msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123),
                          edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123),
                           data='ui:' + action, answer=AsyncMock())


def input_message(text):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), id=55, text=text, photo=None,
                           reply_text=AsyncMock(), delete=AsyncMock(), stop_propagation=MagicMock())


def event(text='/dl https://example.com/video'):
    return SimpleNamespace(is_private=True, sender_id=123, chat_id=123,
                           message=SimpleNamespace(text=text), reply=AsyncMock())


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        language_patch = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')
        language_patch.start()
        self.addCleanup(language_patch.stop)
        menu.waiting.clear()
        menu.busy_inputs.clear()
        menu.download_quality.clear()
        ytdl.ongoing_downloads.clear()
        ytdl.user_progress.clear()
        batch.Z.clear()
        batch_queue.locks.clear()
        user_steps.clear()

    def test_cookie_hostname_not_url_substring(self):
        with patch.object(ytdl, 'YT_COOKIES', '#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tname\tvalue'), patch.object(ytdl, 'INSTA_COOKIES', 'insta'):
            for url in ['https://youtube.com/watch?v=x', 'https://www.youtube.com/watch', 'https://youtu.be/x']:
                self.assertTrue(ytdl.cookies_for_url(url))
            self.assertEqual(ytdl.cookies_for_url('https://m.instagram.com/p/1'), 'insta')
            for url in ['https://youtube.com.evil.test/x', 'https://evil.test/youtube.com',
                        'https://youtube.com@evil.test/', 'https://evil.test@youtube.com/',
                        'file://youtube.com/path', 'https://notinstagram.com']:
                self.assertIsNone(ytdl.cookies_for_url(url), url)
        with patch.object(ytdl, 'YT_COOKIES', '# example only\n'):
            self.assertIsNone(ytdl.cookies_for_url('https://youtube.com/'))

    def test_quality_selectors_are_valid_and_bounded(self):
        import yt_dlp
        self.assertEqual(set(ytdl.QUALITY_FORMATS), {'auto', '360', '480', '720', '1080'})
        with yt_dlp.YoutubeDL({'quiet': True}) as downloader:
            for quality, selector in ytdl.QUALITY_FORMATS.items():
                downloader.build_format_selector(selector)
                if quality != 'auto':
                    self.assertEqual(selector.count('height<=' + quality), 2)

    async def test_disabled_video_and_audio_do_not_start(self):
        e = event()
        with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', False), patch.object(ytdl, 'fetch_video_info', new_callable=AsyncMock) as fetch, patch.object(ytdl, 'extract_audio_async', new_callable=AsyncMock) as audio:
            await ytdl.process_video(None, e, 'https://example.com', quality='720')
            await ytdl.process_audio(None, e, 'https://example.com')
        fetch.assert_not_awaited()
        audio.assert_not_awaited()
        self.assertEqual(e.reply.await_count, 2)

    async def test_menu_quality_selection_navigation_and_invalid(self):
        q = callback('video')
        with patch.object(config, 'ENABLE_EXTERNAL_DOWNLOADS', True):
            await menu.menu_callback(None, q)
            self.assertIn('maximum resolution', q.message.edit_text.await_args.args[0])
            q.data = 'ui:vq:720'
            await menu.menu_callback(None, q)
            self.assertEqual(menu.waiting[123], 'video')
            self.assertEqual(menu.download_quality[123], '720')
            q.data = 'ui:home'
            await menu.menu_callback(None, q)
            self.assertNotIn(123, menu.download_quality)
            self.assertNotIn(123, menu.waiting)
            q.data = 'ui:vq:9999'
            await menu.menu_callback(None, q)
            self.assertNotIn(123, menu.waiting)

    async def test_menu_disabled_does_not_prompt(self):
        with patch.object(config, 'ENABLE_EXTERNAL_DOWNLOADS', False):
            for action in ('video', 'audio', 'vq:720'):
                q = callback(action)
                await menu.menu_callback(None, q)
                self.assertNotIn(123, menu.waiting)
                self.assertIn('disabled', q.message.edit_text.await_args.args[0])

    async def test_menu_download_reserves_before_lookup_and_passes_quality(self):
        menu.waiting[123] = 'video'
        menu.download_quality[123] = '480'
        msg = input_message('https://youtube.com/watch?v=x')
        e = event()
        entered, release = asyncio.Event(), asyncio.Event()
        async def lookup(*args, **kwargs):
            entered.set()
            await release.wait()
            return e
        telethon = SimpleNamespace(get_messages=AsyncMock(side_effect=lookup))
        with patch.object(config, 'ENABLE_EXTERNAL_DOWNLOADS', True), patch.object(shared_client, 'client', telethon), patch.object(ytdl, 'process_video', new_callable=AsyncMock) as process:
            task = asyncio.create_task(menu.menu_input(None, msg))
            await entered.wait()
            self.assertIn(123, ytdl.ongoing_downloads)
            duplicate = event()
            with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', True):
                await ytdl.video_handler(duplicate)
            self.assertIn('ongoing', duplicate.reply.await_args.args[0])
            release.set()
            await task
        process.assert_awaited_once()
        self.assertEqual(process.await_args.kwargs['quality'], '480')
        self.assertNotIn(123, ytdl.ongoing_downloads)
        self.assertNotIn(123, menu.download_quality)

    async def test_legacy_reservation_released_on_error(self):
        e = event()
        entered, release = asyncio.Event(), asyncio.Event()
        async def work(*args, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError('sensitive URL')
        with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', True), patch.object(ytdl, 'process_video', side_effect=work):
            task = asyncio.create_task(ytdl.video_handler(e))
            await entered.wait()
            other = event('/adl https://example.com')
            await ytdl.audio_handler(other)
            self.assertIn('ongoing', other.reply.await_args.args[0])
            release.set()
            await task
        self.assertNotIn(123, ytdl.ongoing_downloads)
        self.assertNotIn('sensitive URL', e.reply.await_args.args[0])

    async def test_video_options_temp_cleanup_and_plain_caption(self):
        e = event()
        status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        e.reply.return_value = status
        client = SimpleNamespace(send_message=AsyncMock(return_value=status), send_file=AsyncMock())
        seen = {}
        async def fetch(url, opts, *args):
            seen['opts'] = opts.copy()
            self.assertTrue(os.path.exists(opts['cookiefile']))
            return {'title': '<b>not markup</b>'}
        def download(url, opts):
            with open(opts['outtmpl'].replace('%(ext)s', 'mp4'), 'wb') as f:
                f.write(b'video')
        with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', True), patch.object(ytdl, 'fetch_video_info', side_effect=fetch), patch.object(ytdl, 'download_video', side_effect=download), patch.object(ytdl, 'get_video_metadata', new_callable=AsyncMock, return_value={'duration': 3, 'width': 2, 'height': 2}), patch.object(ytdl, 'fast_upload', new_callable=AsyncMock, return_value='upload'):
            await ytdl.process_video(client, e, 'https://example.com', 'secret', quality='720')
        self.assertEqual(seen['opts']['format'], ytdl.QUALITY_FORMATS['720'])
        self.assertFalse(os.path.exists(seen['opts']['cookiefile']))
        self.assertFalse(os.path.exists(os.path.dirname(seen['opts']['outtmpl'])))
        self.assertEqual(client.send_file.await_args.kwargs['caption'], '<b>not markup</b>')
        self.assertIsNone(client.send_file.await_args.kwargs['parse_mode'])

    async def test_invalid_quality_rejected_without_extracting(self):
        e = event()
        with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', True), patch.object(ytdl, 'fetch_video_info', new_callable=AsyncMock) as fetch:
            await ytdl.process_video(None, e, 'https://example.com', quality='999')
        fetch.assert_not_awaited()
        self.assertIn('Invalid', e.reply.await_args.args[0])

    def test_progress_handles_zero_duration_or_size(self):
        with patch.object(ytdl.time, 'time', return_value=1):
            self.assertIn('0.00%', ytdl.progress_callback(0, 0, 123))

    def test_external_url_checks_are_syntax_only(self):
        for url in ('https://example.com/watch?v=1', 'http://127.0.0.1/private'):
            self.assertTrue(ytdl.valid_external_url(url))
        for url in ('file:///etc/passwd', 'https://', 'https://user:secret@example.com',
                    'https://example.com:99999/', 'https://example.com/\nfoo', 'https://example.com/' + 'a'*2048):
            self.assertFalse(ytdl.valid_external_url(url))

    async def test_audio_temp_cleanup_and_plain_caption(self):
        e = event()
        status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        e.reply.return_value = status
        client = SimpleNamespace(send_message=AsyncMock(return_value=status), send_file=AsyncMock())
        seen = {}
        async def extract(opts, url):
            seen.update(opts)
            self.assertTrue(os.path.isfile(opts['cookiefile']))
            with open(opts['outtmpl'].replace('%(ext)s', 'mp3'), 'wb') as f:
                f.write(b'audio')
            return {'title': '<b>Audio</b>'}
        tags = {}
        mp3 = SimpleNamespace(tags=tags, add_tags=MagicMock(), save=MagicMock())
        with patch.object(ytdl, 'ENABLE_EXTERNAL_DOWNLOADS', True), patch.object(ytdl, 'extract_audio_async', side_effect=extract), patch.object(ytdl, 'MP3', return_value=mp3), patch.object(ytdl, 'fast_upload', new_callable=AsyncMock, return_value='upload'):
            await ytdl.process_audio(client, e, 'https://example.com', 'secret')
        self.assertFalse(os.path.exists(os.path.dirname(seen['outtmpl'])))
        self.assertEqual(client.send_file.await_args.kwargs['caption'], '<b>Audio</b>')
        self.assertIsNone(client.send_file.await_args.kwargs['parse_mode'])

    async def test_cancel_waits_for_worker_before_cleanup(self):
        import threading
        started, release = threading.Event(), threading.Event()
        def worker():
            started.set()
            release.wait(5)
        task = asyncio.create_task(ytdl.await_thread_completion(worker))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

if __name__ == '__main__':
    unittest.main()
