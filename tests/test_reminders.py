"""Reminder scheduling and payment-history guards, entirely offline."""
import asyncio
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

from plugins import reminders, menu, pay
from utils.billing import check_premium_indexes

NOW = datetime(2026, 11, 9, 12, 0)


class Cursor:
    def __init__(self, records):
        self.records = iter(copy.deepcopy(records))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.records)
        except StopIteration:
            raise StopAsyncIteration


class Premium:
    def __init__(self, records):
        self.records = copy.deepcopy(records)

    def find(self, selector, projection):
        window = selector['subscription_end']
        return Cursor([r for r in self.records if window['$gt'] < r['subscription_end'] <= window['$lte']])

    async def update_one(self, selector, update):
        for record in self.records:
            if (record['_id'] == selector['_id'] and record['user_id'] == selector['user_id']
                    and record['subscription_end'] == selector['subscription_end']
                    and record.get('reminder_claimed_for') != selector['reminder_claimed_for']['$ne']):
                record.update(update['$set'])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def record(uid=123, expiry=NOW + timedelta(hours=3)):
    return {'_id': str(uid), 'user_id': uid, 'subscription_end': expiry}


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reminders.lock_for(123)  # same lock used by preference/deletion actions
        self.env = patch.dict(os.environ, {'EXPIRY_REMINDER_HOURS': '24', 'EXPIRY_REMINDER_INTERVAL_SECONDS': '900'})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_opted_in_user_gets_only_one_attempt_per_expiry(self):
        premium = Premium([record()])
        users = SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'u'}))
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock) as send, patch.object(reminders.asyncio, 'sleep', new_callable=AsyncMock):
            await reminders.scan_once(NOW)
            await reminders.scan_once(NOW)
            self.assertEqual(send.await_count, 1)
            self.assertIn('UTC', send.await_args.args[1])
            premium.records[0]['subscription_end'] = NOW + timedelta(hours=5)
            await reminders.scan_once(NOW)
            self.assertEqual(send.await_count, 2)

    async def test_claim_is_written_before_telegram_send(self):
        premium = Premium([record()])
        users = SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'u'}))
        async def send(*args, **kwargs):
            self.assertEqual(premium.records[0]['reminder_claimed_for'], premium.records[0]['subscription_end'])
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock, side_effect=send), patch.object(reminders.asyncio, 'sleep', new_callable=AsyncMock):
            await reminders.scan_once(NOW)

    async def test_premium_status_labels_expired_record(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123), edit_text=AsyncMock())
        q = SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123), data='ui:status', answer=AsyncMock())
        from utils import func
        with patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en'), patch.object(menu, 'OWNER_ID', []), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={}), patch.object(func.premium_users_collection, 'find_one', new_callable=AsyncMock, return_value={'subscription_end': datetime(2020, 1, 1)}):
            await menu.menu_callback(None, q)
        self.assertIn('Expired on', msg.edit_text.await_args.args[0])

    async def test_no_consent_expired_far_future_and_owner_never_sent(self):
        premium = Premium([record(123), record(456, NOW - timedelta(seconds=1)),
                           record(789, NOW + timedelta(days=2)), record(999)])
        users = SimpleNamespace(find_one=AsyncMock(return_value=None))
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', [999]), patch.object(reminders.app, 'send_message', new_callable=AsyncMock) as send:
            await reminders.scan_once(NOW)
        send.assert_not_awaited()
        self.assertEqual(users.find_one.await_count, 1)
        self.assertNotIn('reminder_claimed_for', premium.records[0])

    async def test_renewal_before_claim_does_not_send_stale_reminder(self):
        premium = Premium([record()])
        async def renew(*args):
            premium.records[0]['subscription_end'] = NOW + timedelta(days=3)
            return {'_id': 'u'}
        users = SimpleNamespace(find_one=AsyncMock(side_effect=renew))
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock) as send:
            await reminders.scan_once(NOW)
        send.assert_not_awaited()

    async def test_ambiguous_failure_is_not_retried(self):
        premium = Premium([record()])
        users = SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'u'}))
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock, side_effect=RuntimeError('ambiguous')) as send, patch.object(reminders.asyncio, 'sleep', new_callable=AsyncMock), self.assertLogs(reminders.logger, level='WARNING'):
            await reminders.scan_once(NOW)
            await reminders.scan_once(NOW)
        self.assertEqual(send.await_count, 1)
        self.assertEqual(premium.records[0]['reminder_claimed_for'], premium.records[0]['subscription_end'])

    async def test_database_failure_before_claim_does_not_send(self):
        premium = Premium([record()])
        users = SimpleNamespace(find_one=AsyncMock(side_effect=RuntimeError('database offline')))
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock) as send, self.assertLogs(reminders.logger, level='ERROR'):
            await reminders.scan_once(NOW)
        send.assert_not_awaited()
        self.assertNotIn('reminder_claimed_for', premium.records[0])

    async def test_opt_out_waits_for_inflight_send_and_prevents_later_sends(self):
        premium = Premium([record()])
        consent = {'enabled': True}
        users = SimpleNamespace(find_one=AsyncMock(side_effect=lambda *args: {'_id': 'u'} if consent['enabled'] else None))
        started, release = asyncio.Event(), asyncio.Event()
        async def send(*args, **kwargs):
            started.set()
            await release.wait()
        async def opt_out():
            async with reminders.lock_for(123):
                consent['enabled'] = False
        with patch.object(reminders, 'premium_users_collection', premium), patch.object(reminders, 'users_collection', users), patch.object(reminders, 'OWNER_ID', []), patch.object(reminders.app, 'send_message', new_callable=AsyncMock, side_effect=send) as send_mock:
            scanning = asyncio.create_task(reminders.scan_once(NOW))
            await started.wait()
            opting_out = asyncio.create_task(opt_out())
            await asyncio.sleep(0)
            self.assertFalse(opting_out.done())
            release.set()
            await asyncio.gather(scanning, opting_out)
            premium.records[0]['subscription_end'] = NOW + timedelta(hours=4)
            await reminders.scan_once(NOW)
        self.assertEqual(send_mock.await_count, 1)

    async def test_startup_disabled_never_creates_worker_or_index(self):
        db = SimpleNamespace(create_index=AsyncMock())
        with patch.object(reminders, 'premium_users_collection', db), patch.object(reminders, 'enabled', return_value=False):
            await reminders.run_reminders_plugin()
        db.create_index.assert_not_awaited()
        self.assertIsNone(reminders.worker)

    async def test_startup_enables_worker_and_shutdown_drains_it(self):
        db = SimpleNamespace(create_index=AsyncMock())
        async def loop():
            await asyncio.Event().wait()
        with patch.object(reminders, 'premium_users_collection', db), patch.object(reminders, 'enabled', return_value=True), patch.object(reminders, 'reminder_loop', side_effect=loop):
            await reminders.run_reminders_plugin()
            db.create_index.assert_awaited_once_with('user_id', unique=True)
            task = reminders.worker
            self.assertIsNotNone(task)
            await reminders.shutdown_reminders()
            self.assertTrue(task.cancelled())
            self.assertIsNone(reminders.worker)

    async def test_worker_is_cancelled_on_shutdown(self):
        started = asyncio.Event()
        async def waiting():
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(waiting())
        await started.wait()
        reminders.worker = task
        await reminders.shutdown_reminders()
        self.assertTrue(task.cancelled())
        self.assertIsNone(reminders.worker)

    def test_invalid_schedule_is_rejected(self):
        with patch.dict(os.environ, {'EXPIRY_REMINDER_HOURS': '0'}):
            with self.assertRaises(ValueError):
                reminders.settings()
        with patch.dict(os.environ, {'EXPIRY_REMINDER_INTERVAL_SECONDS': '10'}):
            with self.assertRaises(ValueError):
                reminders.settings()

    async def test_user_can_opt_in_and_out(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123), edit_text=AsyncMock())
        q = SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123), data='ui:reminders_on', answer=AsyncMock())
        with patch.object(reminders, 'enabled', return_value=True), patch.object(menu, 'save_user_data', new_callable=AsyncMock) as save, patch.object(menu, 'get_user_data', new_callable=AsyncMock, return_value={'expiry_reminders': True}):
            await menu.menu_callback(None, q)
            save.assert_awaited_once_with(123, 'expiry_reminders', True)
            self.assertIn('ON', msg.edit_text.await_args.args[0])
            q.data = 'ui:reminders_off'
            await menu.menu_callback(None, q)
            save.assert_awaited_with(123, 'expiry_reminders', False)

    async def test_operator_disabled_does_not_accept_opt_in(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123), edit_text=AsyncMock())
        q = SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123), data='ui:reminders_on', answer=AsyncMock())
        with patch.object(reminders, 'enabled', return_value=False), patch.object(menu, 'save_user_data', new_callable=AsyncMock) as save, patch.object(menu, 'get_user_data', new_callable=AsyncMock, return_value={}):
            await menu.menu_callback(None, q)
        save.assert_not_awaited()
        self.assertIn('disabled', msg.edit_text.await_args.args[0])


class BillingGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_ttl_index_on_any_field_is_unsafe(self):
        check_premium_indexes({'_id_': {'key': [('_id', 1)]}})
        with self.assertRaises(RuntimeError):
            check_premium_indexes({'expireAt_1': {'key': [('expireAt', 1)], 'expireAfterSeconds': 0}})

    async def test_unsafe_index_rejects_precheckout(self):
        invoice = {'user_id': 123, 'amount': 5, 'status': 'created'}
        q = SimpleNamespace(payload='x', from_user=SimpleNamespace(id=123), currency='XTR', total_amount=5, answer=AsyncMock())
        with patch.object(pay.invoices, 'find_one', new_callable=AsyncMock, return_value=invoice), patch.object(pay.premium_users_collection, 'index_information', new_callable=AsyncMock, return_value={'ttl': {'expireAfterSeconds': 0}}), self.assertLogs(pay.logger, level='ERROR'):
            await pay.pre_checkout(None, q)
        self.assertFalse(q.answer.await_args.kwargs['ok'])

    async def test_unsafe_index_blocks_new_stars_invoice(self):
        message = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private')), reply_text=AsyncMock())
        query = SimpleNamespace(message=message, from_user=SimpleNamespace(id=123), data='stars:d', answer=AsyncMock())
        client = SimpleNamespace(send_invoice=AsyncMock())
        plan = {'d': {'s': 5, 'du': 1, 'u': 'days', 'l': 'Daily'}}
        with patch.object(pay, 'P0', plan), patch.object(pay.premium_users_collection, 'index_information', new_callable=AsyncMock, return_value={'ttl': {'expireAfterSeconds': 0}}), patch.object(pay.invoices, 'insert_one', new_callable=AsyncMock) as insert, self.assertLogs(pay.logger, level='ERROR'):
            await pay.create_invoice(client, query)
        insert.assert_not_awaited()
        client.send_invoice.assert_not_awaited()

    async def test_unsafe_index_blocks_grant(self):
        with patch.object(pay.premium_users_collection, 'index_information', new_callable=AsyncMock, return_value={'ttl': {'expireAfterSeconds': 0}}), patch.object(pay.premium_users_collection, 'update_one', new_callable=AsyncMock) as update:
            with self.assertRaises(RuntimeError):
                await pay.grant_invoice({'user_id': 123, 'days': 7}, 'charge')
        update.assert_not_awaited()
