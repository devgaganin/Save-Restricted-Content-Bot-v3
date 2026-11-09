"""Offline history privacy, confirmation and settings preview tests."""
import copy
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch_history as history, batch_queue as queue, settings_view, menu


class Cursor:
    def __init__(self, docs):
        self.docs = docs
    def sort(self, fields):
        self.docs.sort(key=lambda d: (d.get('archived_at', datetime.min), d['_id']), reverse=True)
        return self
    def skip(self, n):
        self.docs = self.docs[n:]
        return self
    def limit(self, n):
        self.docs = self.docs[:n]
        return self
    def __aiter__(self):
        self.iterator = iter(self.docs)
        return self
    async def __anext__(self):
        try:
            return copy.deepcopy(next(self.iterator))
        except StopIteration:
            raise StopAsyncIteration


class HistoryCollection:
    def __init__(self, docs):
        self.docs = copy.deepcopy(docs)
        self.last_query = None
    def matches(self, doc, query):
        for key, value in query.items():
            if isinstance(value, dict) and '$ne' in value:
                if doc.get(key) == value['$ne']:
                    return False
            elif doc.get(key) != value:
                return False
        return True
    def find(self, query, projection):
        self.last_query = query
        return Cursor([d for d in self.docs if self.matches(d, query)])
    async def find_one(self, query, projection=None):
        self.last_query = query
        return next((copy.deepcopy(d) for d in self.docs if self.matches(d, query)), None)
    async def delete_many(self, query):
        self.last_query = query
        original = len(self.docs)
        self.docs = [d for d in self.docs if not self.matches(d, query)]
        return SimpleNamespace(deleted_count=original - len(self.docs))


def doc(number, uid=123):
    return {'_id': f'{number:024x}', 'user_id': uid, 'source': '<secret>',
            'destination': '-100/1', 'status': 'completed', 'total': 2,
            'current': 2, 'success': 1, 'failed': 1,
            'archived_at': datetime(2026, 1, 1) + timedelta(minutes=number)}


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        queue.locks.clear()
        history.confirmations.clear()
        self.message = SimpleNamespace(edit_text=AsyncMock(), chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123))

    async def test_pagination_scoped_and_current_not_duplicated(self):
        records = [doc(i) for i in range(1, 8)] + [doc(9, 456)]
        db = HistoryCollection(records)
        with patch.object(queue, 'history', db), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': doc(7)['_id']}):
            first = await history.archived_page(123, 0)
            second = await history.archived_page(123, 1)
            await history.show_history(self.message, 123, 0)
        self.assertEqual([r['_id'] for r in first], [doc(i)['_id'] for i in (6, 5, 4, 3, 2, 1)])
        self.assertEqual([r['_id'] for r in second], [doc(1)['_id']])
        self.assertEqual(db.last_query['user_id'], 123)
        self.assertEqual(db.last_query['_id'], {'$ne': doc(7)['_id']})
        buttons = self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertIn('ui:history_page:1', [b.callback_data for row in buttons for b in row])

    async def test_details_scoped_and_html_escaped(self):
        db = HistoryCollection([doc(1, 456), doc(2, 123)])
        with patch.object(queue, 'history', db):
            await history.show_detail(self.message, 123, doc(1)['_id'])
            self.assertIn('unavailable', self.message.edit_text.await_args.args[0])
            self.assertEqual(db.last_query['user_id'], 123)
            await history.show_detail(self.message, 123, doc(2)['_id'])
        body = self.message.edit_text.await_args.args[0]
        self.assertIn('&lt;secret&gt;', body)
        self.assertNotIn('<secret>', body)

    async def test_clear_requires_one_shot_confirmation_and_preserves_current_and_other_users(self):
        db = HistoryCollection([doc(1), doc(2), doc(3, 456)])
        with patch.object(queue, 'history', db), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': doc(2)['_id']}):
            await history.ask_clear(self.message, 123)
            token = history.confirmations[123][0]
            ok, _ = await history.clear_history(123, 'wrong')
            self.assertFalse(ok)
            self.assertEqual(len(db.docs), 3)
            ok, _ = await history.clear_history(123, token)
            self.assertTrue(ok)
            self.assertEqual({r['_id'] for r in db.docs}, {doc(2)['_id'], doc(3)['_id']})
            ok, _ = await history.clear_history(123, token)
            self.assertFalse(ok)

    async def test_clear_confirmation_expiry_and_cancel(self):
        await history.ask_clear(self.message, 123)
        token = history.confirmations[123][0]
        history.confirmations[123] = (token, 0)
        ok, _ = await history.clear_history(123, token)
        self.assertFalse(ok)
        await history.ask_clear(self.message, 123)
        token = history.confirmations[123][0]
        await history.cancel_confirmation(123)
        ok, _ = await history.clear_history(123, token)
        self.assertFalse(ok)

    async def test_invalid_and_foreign_callbacks_never_read_history(self):
        db = HistoryCollection([doc(1, 456)])
        q = SimpleNamespace(message=self.message, from_user=SimpleNamespace(id=456), data='ui:history', answer=AsyncMock())
        with patch.object(queue, 'history', db), patch.object(queue, 'get_job', new_callable=AsyncMock) as current:
            await menu.menu_callback(None, q)
            current.assert_not_awaited()
            await history.show_detail(self.message, 123, '../invalid')
        self.assertIsNone(db.last_query)


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_escapes_text_and_omits_credentials(self):
        message = SimpleNamespace(edit_text=AsyncMock())
        data = {'caption': '<b>hello</b>', 'prefix': 'x' * 200, 'bot_token': 'SECRET',
                'session_string': 'SENSITIVE', 'delete_words': ['x', 'y'],
                'replacement_words': {'foo': 'bar'}, 'remove_caption': True}
        with patch.object(settings_view.users_collection, 'find_one', new_callable=AsyncMock, return_value=data) as find, patch.object(settings_view.os.path, 'isfile', return_value=False):
            await settings_view.show_settings(message, 123)
        projection = find.await_args.args[1]
        self.assertNotIn('bot_token', projection)
        self.assertNotIn('session_string', projection)
        body = message.edit_text.await_args.args[0]
        self.assertIn('&lt;b&gt;hello&lt;/b&gt;', body)
        self.assertNotIn('SECRET', body)
        self.assertNotIn('SENSITIVE', body)
        self.assertIn('…', body)
        self.assertIn('Removed', body)

    async def test_preview_database_error_shows_error_not_empty_settings(self):
        message = SimpleNamespace(edit_text=AsyncMock(), chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123))
        q = SimpleNamespace(message=message, from_user=SimpleNamespace(id=123), data='ui:settings_view', answer=AsyncMock())
        with patch.object(settings_view.users_collection, 'find_one', new_callable=AsyncMock, side_effect=RuntimeError('offline')), self.assertLogs(menu.logger, level='ERROR'):
            await menu.menu_callback(None, q)
        self.assertIn('Could not load settings', message.edit_text.await_args.args[0])


class ClearSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from plugins import settings_clear
        self.clear = settings_clear
        self.clear.confirmations.clear()
        queue.locks.clear()
        self.msg = SimpleNamespace(edit_text=AsyncMock())
        self.docs = {123: {'caption': 'old', 'bot_token': 'secret'}, 456: {'caption': 'other'}}

    async def lookup(self, selector, projection):
        doc = self.docs.get(selector['user_id'], {})
        return {k: copy.deepcopy(doc[k]) for k in projection if k in doc and projection[k]}

    async def unset(self, selector, update):
        doc = self.docs.get(selector['user_id'], {})
        condition = selector['$and']
        key = next(iter(condition[0]))
        expected = condition[1][key]['$eq']
        if key in doc and doc[key] == expected:
            del doc[key]
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def test_confirmation_clears_only_selected_user_field(self):
        with patch.object(self.clear.users_collection, 'find_one', new_callable=AsyncMock, side_effect=self.lookup), patch.object(self.clear.users_collection, 'update_one', new_callable=AsyncMock, side_effect=self.unset) as update:
            await self.clear.ask_clear(self.msg, 123, 'caption')
            token = self.clear.confirmations[123][0]
            ok, _ = await self.clear.clear(123, token)
            self.assertTrue(ok)
            self.assertEqual(self.docs[123], {'bot_token': 'secret'})
            self.assertEqual(self.docs[456]['caption'], 'other')
            ok, _ = await self.clear.clear(123, token)
            self.assertFalse(ok)
            self.assertEqual(update.await_count, 1)

    async def test_changed_setting_is_not_removed_by_old_confirmation(self):
        with patch.object(self.clear.users_collection, 'find_one', new_callable=AsyncMock, side_effect=self.lookup), patch.object(self.clear.users_collection, 'update_one', new_callable=AsyncMock, side_effect=self.unset):
            await self.clear.ask_clear(self.msg, 123, 'caption')
            token = self.clear.confirmations[123][0]
            self.docs[123]['caption'] = 'new'
            ok, reason = await self.clear.clear(123, token)
        self.assertFalse(ok)
        self.assertIn('changed', reason)
        self.assertEqual(self.docs[123]['caption'], 'new')

    async def test_cancel_and_expiry_do_not_clear(self):
        with patch.object(self.clear.users_collection, 'find_one', new_callable=AsyncMock, side_effect=self.lookup), patch.object(self.clear.users_collection, 'update_one', new_callable=AsyncMock) as update:
            await self.clear.ask_clear(self.msg, 123, 'caption')
            token = self.clear.confirmations[123][0]
            await self.clear.show_choices(self.msg, 123)
            ok, _ = await self.clear.clear(123, token)
            self.assertFalse(ok)
            await self.clear.ask_clear(self.msg, 123, 'caption')
            token, key, original, _ = self.clear.confirmations[123]
            self.clear.confirmations[123] = (token, key, original, 0)
            ok, _ = await self.clear.clear(123, token)
            self.assertFalse(ok)
        update.assert_not_awaited()

    async def test_credentials_are_not_clearable_via_callback(self):
        with patch.object(self.clear.users_collection, 'find_one', new_callable=AsyncMock) as find:
            await self.clear.ask_clear(self.msg, 123, 'bot_token')
        find.assert_not_awaited()
        self.assertIn('Invalid setting', self.msg.edit_text.await_args.args[0])

    async def test_unset_field_does_not_issue_confirmation(self):
        with patch.object(self.clear.users_collection, 'find_one', new_callable=AsyncMock, side_effect=self.lookup):
            await self.clear.ask_clear(self.msg, 123, 'prefix')
        self.assertNotIn(123, self.clear.confirmations)
        self.assertIn('not set', self.msg.edit_text.await_args.args[0])
