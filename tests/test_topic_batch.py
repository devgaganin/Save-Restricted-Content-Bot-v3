"""Topic-scoped batch tests; all Telegram and MongoDB calls are mocked."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from pyrogram.enums import ChatType
from plugins import batch, batch_queue as queue, menu
from utils.func import E, parse_message_link
from test_batch_queue import FakeCollection, job


def forum(mid, thread, service=None):
    return SimpleNamespace(id=mid, chat=SimpleNamespace(type=ChatType.FORUM), is_topic_message=True,
                           message_thread_id=thread, service=service)


def topic_clients(source_id=-100111, destination_id=-100222):
    user = SimpleNamespace(get_chat=AsyncMock(side_effect=[SimpleNamespace(id=source_id, type=ChatType.FORUM),
                                                            SimpleNamespace(id=destination_id, type=ChatType.FORUM)]),
                           get_forum_topics_by_id=AsyncMock(side_effect=[SimpleNamespace(id=4), SimpleNamespace(id=7, closed=False)]))
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=destination_id, type=ChatType.FORUM)))
    return bot, user


def input_message(text):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123), text=text,
                           reply_text=AsyncMock(return_value=SimpleNamespace(id=99, edit=AsyncMock())))


class TopicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        queue.locks.clear()
        batch.Z.clear()
        batch.ACTIVE_USERS.clear()
        menu.waiting.clear()
        menu.busy_inputs.clear()

    def test_topic_link_parsing_and_boundaries(self):
        self.assertEqual(parse_message_link('https://t.me/c/123456/4/99'), ('-100123456', 99, 'private', 4))
        self.assertEqual(parse_message_link('https://t.me/somegroup/1/12?single'), ('somegroup', 12, 'public', 1))
        self.assertEqual(parse_message_link('https://t.me/somegroup/12'), ('somegroup', 12, 'public', None))
        self.assertEqual(E('https://t.me/c/123456/4/99'), ('-100123456', 99, 'private'))
        for text in ('https://t.me/c/123456/0/99', 'https://t.me/c/123456/2147483648/99',
                     'https://t.me/group/4/0', 'https://t.me/group/4/99/x', 'https://t.me/group/4/99 more'):
            self.assertEqual(parse_message_link(text), (None, None, None, None))

    def test_membership_fails_closed(self):
        self.assertIs(queue.topic_membership(forum(15, 4), 4), True)
        self.assertIs(queue.topic_membership(forum(15, 5), 4), False)
        self.assertIs(queue.topic_membership(forum(15, 4, service='service'), 4), False)
        self.assertIsNone(queue.topic_membership(forum(15, None), 4))
        self.assertIsNone(queue.topic_membership(forum(15, 4), True))
        self.assertIsNone(queue.topic_membership(SimpleNamespace(chat=SimpleNamespace(type=ChatType.SUPERGROUP), is_topic_message=True, message_thread_id=4), 4))
        self.assertIsNone(queue.topic_membership(SimpleNamespace(chat=SimpleNamespace(type=ChatType.FORUM), is_topic_message=False, message_thread_id=4), 4))
        self.assertIsNone(queue.topic_membership(SimpleNamespace(), 4))

    async def test_route_requires_distinct_accessible_forums_and_open_topic(self):
        bot, user = topic_clients()
        source, error = await queue.validate_topic_route('src', 4, '-100222/7', bot, user)
        self.assertEqual(source, -100111)
        self.assertIsNone(error)
        bot, user = topic_clients(source_id=-100222)
        source, error = await queue.validate_topic_route('src', 4, '-100222/7', bot, user)
        self.assertIsNone(source)
        self.assertIn('different', error)
        bot, user = topic_clients()
        user.get_forum_topics_by_id.side_effect = [SimpleNamespace(id=4), SimpleNamespace(id=7, closed=True)]
        self.assertIn('closed', (await queue.validate_topic_route('src', 4, '-100222/7', bot, user))[1])
        bot, user = topic_clients()
        bot.get_chat.return_value.type = ChatType.SUPERGROUP
        self.assertIn('forum', (await queue.validate_topic_route('src', 4, '-100222/7', bot, user))[1])
        bot, user = topic_clients()
        user.get_chat.side_effect = RuntimeError('no access')
        self.assertIn('Cannot verify', (await queue.validate_topic_route('src', 4, '-100222/7', bot, user))[1])
        for dest in ('-100222', '-100222/0', '123/7', '-100222/2147483648', '-100222/7/8'):
            bot, user = topic_clients()
            source, error = await queue.validate_topic_route('src', 4, dest, bot, user)
            self.assertIsNone(source, dest)
            self.assertIsNotNone(error, dest)
            user.get_chat.assert_not_awaited()

    async def test_topic_start_persists_scope_and_destination_before_launch(self):
        db = FakeCollection()
        bot, user = topic_clients()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=bot), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=user), patch.object(queue, 'get_user_data_key', new_callable=AsyncMock, return_value='-100222/7'), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, return_value=forum(10, 4)), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, reason = await queue.start_new(123, 'src', 10, 3, 'public', SimpleNamespace(id=99), source_topic=4)
        self.assertTrue(ok, reason)
        self.assertEqual(db.value['source'], -100111)
        self.assertEqual(db.value['source_topic'], 4)
        self.assertEqual(db.value['destination'], '-100222/7')
        self.assertEqual(db.value['pending'], [10, 11, 12])
        launch.assert_awaited_once()

    async def test_invalid_route_or_cancel_never_starts_or_replaces_old_job(self):
        for cancel in (False, True):
            old = job(status='completed', pending=[])
            db = FakeCollection(old)
            bot, user = topic_clients()
            with patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=bot), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=user), patch.object(queue, 'get_user_data_key', new_callable=AsyncMock, return_value='-100222/7' if cancel else 'bad'), patch.object(queue, 'launch', new_callable=AsyncMock) as launch, patch.object(queue, 'archive', new_callable=AsyncMock) as archive:
                wanted = MagicMock(side_effect=[True, False] if cancel else None, return_value=True)
                ok, _ = await queue.start_new(123, 'src', 10, 3, 'public', SimpleNamespace(id=99), still_wanted=wanted, source_topic=4)
            self.assertFalse(ok)
            self.assertEqual(db.value, old)
            launch.assert_not_awaited()
            archive.assert_not_awaited()

    async def test_first_post_must_belong_to_requested_topic(self):
        for msg in (None, forum(10, 5), forum(10, None), forum(10, 4, service='service')):
            db = FakeCollection()
            bot, user = topic_clients()
            with self.subTest(msg=msg), patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=bot), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=user), patch.object(queue, 'get_user_data_key', new_callable=AsyncMock, return_value='-100222/7'), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, return_value=msg), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
                ok, reason = await queue.start_new(123, 'src', 10, 3, 'public', SimpleNamespace(id=99), source_topic=4)
                self.assertFalse(ok)
                self.assertIn('first post', reason)
                self.assertIsNone(db.value)
                launch.assert_not_awaited()

    async def test_worker_only_delivers_verified_source_topic(self):
        db = FakeCollection(job(pending=[10, 11, 12, 13], total=4, source_topic=4))
        msgs = [forum(10, 4), forum(11, 5), forum(12, None), forum(13, 4, service='service')]
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, side_effect=msgs), patch.object(queue.batch, 'process_msg', new_callable=AsyncMock, return_value='Sent.') as process, patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'update_batch_progress', new_callable=AsyncMock), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'edit_message_text', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock), patch.object(queue.asyncio, 'sleep', new_callable=AsyncMock):
            await queue.run_job(123, 'job1', object(), object())
        self.assertEqual(process.await_count, 1)
        self.assertIs(process.await_args.args[2], msgs[0])
        self.assertEqual(db.value['success'], 1)
        self.assertEqual(db.value['skipped'], 2)
        self.assertEqual(db.value['failed_ids'], [12])
        self.assertEqual(db.value['status'], 'completed')

    async def test_failed_retry_preserves_topic_scope(self):
        db = FakeCollection(job(status='completed', pending=[], failed_ids=[12], failed=1, source_topic=4))
        archive = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'history', archive), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock):
            ok, reason = await queue.retry_failed(123, SimpleNamespace(id=99), 'job1', queue.failed_fingerprint([12]))
        self.assertTrue(ok, reason)
        self.assertEqual(db.value['source_topic'], 4)
        self.assertEqual(db.value['pending'], [12])
        self.assertEqual(archive.value['source_topic'], 4)

    async def test_regular_batch_refuses_topic_link_and_topic_mode_requires_one(self):
        with patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()):
            batch.Z[123] = {'step': 'start', 'topic_mode': False}
            msg = input_message('https://t.me/c/123456/4/99')
            await batch.text_handler(None, msg)
            self.assertEqual(batch.Z[123]['step'], 'start')
            self.assertIn('Topic Batch', msg.reply_text.await_args.args[0])
            batch.Z[123] = {'step': 'start', 'topic_mode': True}
            msg = input_message('https://t.me/c/123456/99')
            await batch.text_handler(None, msg)
            self.assertEqual(batch.Z[123]['step'], 'start')
            self.assertIn('both topic and post', msg.reply_text.await_args.args[0])
            msg = input_message('https://t.me/c/123456/4/99')
            await batch.text_handler(None, msg)
            self.assertEqual(batch.Z[123]['step'], 'count')
            self.assertEqual(batch.Z[123]['source_topic'], 4)
            self.assertIn('consecutive message IDs', msg.reply_text.await_args.args[0])

    async def test_topic_count_submits_scope_to_queue(self):
        batch.Z[123] = {'step': 'count', 'topic_mode': True, 'cid': 'source', 'sid': 10, 'lt': 'public', 'source_topic': 4}
        msg = input_message('3')
        with patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(batch, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue, 'start_new', new_callable=AsyncMock, return_value=(True, 'started')) as start:
            await batch.text_handler(None, msg)
        self.assertEqual(start.await_args.kwargs['source_topic'], 4)
        self.assertEqual(start.await_args.args[3], 3)

    async def test_text_delivery_targets_existing_topic(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        msg = SimpleNamespace(photo=None, video=None, video_note=None, audio=None, voice=None,
                              document=None, text=SimpleNamespace(markdown='hello'), media=None)
        with patch.object(batch, 'get_user_data_key', new_callable=AsyncMock, return_value='all'), patch.object(batch, 'process_text_with_rules', new_callable=AsyncMock, return_value='hello'):
            result = await batch.process_msg(bot, object(), msg, '123', 'private', 123, -100111, destination_override='-100222/7')
        self.assertEqual(result, 'Sent.')
        self.assertEqual(bot.send_message.await_args.args[0], -100222)
        self.assertEqual(bot.send_message.await_args.kwargs['message_thread_id'], 7)
        self.assertNotIn('reply_to_message_id', bot.send_message.await_args.kwargs)

    async def test_topic_batch_is_accessible_from_menu(self):
        self.assertTrue(any(action == 'topic_batch' for row in menu.PAGES['save'][2] for _, action in row))
        msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123), edit_text=AsyncMock(), reply_text=AsyncMock())
        query = SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123), data='ui:topic_batch', answer=AsyncMock())
        with patch.object(batch, 'begin_processing', new_callable=AsyncMock) as begin:
            await menu.menu_callback(None, query)
        self.assertEqual(begin.await_args.args[-1], 'topic_batch')

    async def test_send_direct_uses_message_thread_id(self):
        bot = SimpleNamespace(send_photo=AsyncMock())
        msg = SimpleNamespace(video=None, video_note=None, voice=None, sticker=None, audio=None,
                              photo=SimpleNamespace(file_id='file'), document=None)
        self.assertTrue(await batch.send_direct(bot, msg, -100222, 'caption', 7))
        self.assertEqual(bot.send_photo.await_args.kwargs['message_thread_id'], 7)
        self.assertNotIn('reply_to_message_id', bot.send_photo.await_args.kwargs)

if __name__ == '__main__':
    unittest.main()
