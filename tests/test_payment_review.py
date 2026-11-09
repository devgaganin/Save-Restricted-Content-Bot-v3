"""Payment review is owner-only, snapshot-bound and never creates grants."""
import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, menu, payment_review as review

STAR_ID = 'stars:' + 'a' * 24
RZP_ID = 'b' * 32


def invoice(kind='s'):
    common = {'_id': STAR_ID if kind == 's' else RZP_ID, 'user_id': 123,
              'status': 'processing', 'amount': 200, 'days': 30}
    if kind == 's':
        common['charge_id'] = 'charge_1'
    else:
        common.update(payment_id='pay_1', provider_link_id='plink_1', currency='INR')
    return common


def message():
    return SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=999),
                           edit_text=AsyncMock(), reply_text=AsyncMock())


def callback(action, uid=999):
    return SimpleNamespace(message=message(), from_user=SimpleNamespace(id=uid),
                           data='ui:' + action, answer=AsyncMock())


class AsyncCursor:
    def __init__(self, docs): self.docs = docs
    def sort(self, *args): return self
    def skip(self, *args): return self
    def limit(self, *args): return self
    def __aiter__(self):
        async def iterate():
            for doc in self.docs:
                yield doc
        return iterate()


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        language_patch = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')
        language_patch.start()
        self.addCleanup(language_patch.stop)
        review.confirmations.clear()
        menu.waiting.clear()
        menu.busy_inputs.clear()
        batch.Z.clear()

    def make_mocks(self, kind='s'):
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=invoice(kind))
        collection.count_documents = AsyncMock(return_value=1)
        collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1))
        premium = MagicMock()
        premium.index_information = AsyncMock(return_value={'_id_': {}})
        premium.find_one = AsyncMock(return_value={'_id': 123})
        premium.update_one = AsyncMock()
        audit = MagicMock()
        audit.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id='audit'))
        audit.update_one = AsyncMock()
        database = MagicMock()
        database.__getitem__.return_value = audit
        return collection, premium, audit, database

    def test_callback_ids_fit_telegram_limit_and_reject_forged_ids(self):
        for kind, identifier in [('s', STAR_ID), ('r', RZP_ID), ('c', RZP_ID)]:
            short = review.short_id(kind, identifier)
            self.assertEqual(review.full_id(kind, short), identifier)
            self.assertLessEqual(len(('ui:owner_pay_view:' + kind + ':' + short).encode()), 64)
            self.assertLessEqual(len(('ui:owner_pay_check:' + kind + ':' + short).encode()), 64)
        for value in ('../', '', 'stars:' + 'a' * 24, 'b' * 33):
            self.assertIsNone(review.full_id('r', value))
        self.assertIsNone(review.full_id('x', 'a' * 24))

    async def test_owner_only_even_for_guessed_callback(self):
        q = callback('owner_pay_check:s:' + 'a' * 24, uid=123)
        q.message.chat.id = 123
        with patch.object(menu, 'OWNER_ID', [999]), patch.object(review, 'ask_complete', new_callable=AsyncMock) as ask:
            await menu.menu_callback(None, q)
        ask.assert_not_awaited()
        self.assertTrue(q.answer.await_args.kwargs['show_alert'])

    async def test_wrong_private_chat_is_rejected(self):
        q = callback('owner_payments', uid=999)
        q.message.chat.id = 123
        with patch.object(menu, 'OWNER_ID', [999]), patch.object(review, 'show_dashboard', new_callable=AsyncMock) as show:
            await menu.menu_callback(None, q)
        show.assert_not_awaited()

    async def test_list_queries_status_and_paginates(self):
        col = MagicMock()
        col.find.return_value = AsyncCursor([invoice() for _ in range(6)])
        msg = message()
        with patch.object(review.pay, 'invoices', col):
            await review.show_list(msg, 's', 0)
        self.assertEqual(col.find.call_args.args[0], {'status': 'processing'})
        markup = msg.edit_text.await_args.kwargs['reply_markup']
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn('ui:owner_pay_list:s:1', callbacks)
        self.assertIn('ui:owner_pay_view:s:' + 'a' * 24, callbacks)
        self.assertTrue(all(len(x.encode()) <= 64 for x in callbacks))

    async def test_detail_escapes_external_values(self):
        doc = invoice()
        doc['charge_id'] = '<bad>&'
        msg = message()
        with patch.object(review, 'get_record', new_callable=AsyncMock, return_value=doc):
            await review.show_detail(msg, 's', STAR_ID)
        text = msg.edit_text.await_args.args[0]
        self.assertIn('&lt;bad&gt;&amp;', text)
        self.assertNotIn('<bad>', text)

    async def test_missing_applied_payment_cannot_offer_confirmation(self):
        col, premium, audit, db = self.make_mocks()
        premium.find_one.return_value = None
        msg = message()
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium):
            await review.ask_complete(msg, 999, 's', STAR_ID)
        self.assertNotIn(999, review.confirmations)
        self.assertIn('No entitlement or status was changed', msg.edit_text.await_args.args[0])
        col.update_one.assert_not_awaited()
        premium.update_one.assert_not_awaited()

    async def test_confirm_existing_stars_grant_is_audited_and_never_extended(self):
        col, premium, audit, db = self.make_mocks()
        msg = message()
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(msg, 999, 's', STAR_ID)
            token = review.confirmations[999][0]
            ok, reason = await review.complete(999, token)
            again, _ = await review.complete(999, token)
        self.assertTrue(ok, reason)
        self.assertFalse(again)
        premium.update_one.assert_not_awaited()
        col.update_one.assert_awaited_once()
        self.assertEqual(col.update_one.await_args.args[0], review.snapshot('s', invoice()))
        self.assertEqual(col.update_one.await_args.args[1]['$set']['status'], 'completed')
        self.assertEqual(audit.insert_one.await_args.args[0]['action'], 'payment_mark_completed')
        self.assertEqual(audit.update_one.await_args.args[1]['$set']['status'], 'completed')
        self.assertIn('No premium was granted', reason)

    async def test_razorpay_uses_prefixed_charge_and_expected_snapshot(self):
        col, premium, audit, db = self.make_mocks('r')
        with patch.object(review.razorpay, 'links', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(message(), 999, 'r', RZP_ID)
            ok, _ = await review.complete(999, review.confirmations[999][0])
        self.assertTrue(ok)
        self.assertEqual(premium.find_one.await_args.args[0]['applied_payments'], 'razorpay:pay_1')
        self.assertEqual(col.update_one.await_args.args[0]['provider_link_id'], 'plink_1')
        premium.update_one.assert_not_awaited()

    async def test_stale_changed_duplicate_or_missing_proof_fails_closed(self):
        for fault in ('changed', 'duplicate', 'missing', 'expired'):
            col, premium, audit, db = self.make_mocks()
            with self.subTest(fault=fault), patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
                await review.ask_complete(message(), 999, 's', STAR_ID)
                token, expiry, kind, snap = review.confirmations[999]
                if fault == 'changed': col.find_one.return_value = None
                if fault == 'duplicate': col.count_documents.return_value = 2
                if fault == 'missing': premium.find_one.return_value = None
                if fault == 'expired': review.confirmations[999] = (token, time.monotonic() - 1, kind, snap)
                ok, _ = await review.complete(999, token)
                self.assertFalse(ok)
                col.update_one.assert_not_awaited()
                audit.insert_one.assert_not_awaited()
                premium.update_one.assert_not_awaited()

    async def test_ttl_guard_and_audit_failure_prevent_status_change(self):
        for fault in ('ttl', 'audit'):
            col, premium, audit, db = self.make_mocks()
            with self.subTest(fault=fault), patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
                await review.ask_complete(message(), 999, 's', STAR_ID)
                token = review.confirmations[999][0]
                if fault == 'ttl': premium.index_information.return_value = {'bad': {'expireAfterSeconds': 0}}
                if fault == 'audit': audit.insert_one.side_effect = RuntimeError('offline')
                with self.assertRaises(RuntimeError):
                    await review.complete(999, token)
                col.update_one.assert_not_awaited()
                self.assertNotIn(999, review.confirmations)

    async def test_status_race_is_not_reported_as_success(self):
        col, premium, audit, db = self.make_mocks()
        col.update_one.return_value = SimpleNamespace(matched_count=0)
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(message(), 999, 's', STAR_ID)
            ok, reason = await review.complete(999, review.confirmations[999][0])
        self.assertFalse(ok)
        self.assertIn('changed', reason)
        self.assertEqual(audit.update_one.await_args.args[1]['$set']['status'], 'changed')

    async def test_two_simultaneous_confirmations_apply_only_once(self):
        col, premium, audit, db = self.make_mocks()
        entered, release = asyncio.Event(), asyncio.Event()
        async def update(*args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(matched_count=1)
        col.update_one.side_effect = update
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(message(), 999, 's', STAR_ID)
            token = review.confirmations[999][0]
            first = asyncio.create_task(review.complete(999, token))
            await entered.wait()
            second, _ = await review.complete(999, token)
            self.assertFalse(second)
            release.set()
            success, _ = await first
        self.assertTrue(success)
        col.update_one.assert_awaited_once()
        audit.insert_one.assert_awaited_once()
        premium.update_one.assert_not_awaited()

    async def test_unknown_write_result_preserves_processing_audit(self):
        col, premium, audit, db = self.make_mocks()
        col.update_one.side_effect = RuntimeError('connection lost')
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(message(), 999, 's', STAR_ID)
            token = review.confirmations[999][0]
            with self.assertRaises(RuntimeError):
                await review.complete(999, token)
        self.assertEqual(audit.insert_one.await_args.args[0]['status'], 'processing')
        audit.update_one.assert_not_awaited()
        self.assertNotIn(999, review.confirmations)

    async def test_navigation_and_cancel_drop_confirmation(self):
        for action in ('home', 'cancel', 'owner_payments'):
            with self.subTest(action=action):
                review.confirmations[999] = ('a' * 16, time.monotonic() + 100, 's', review.snapshot('s', invoice()))
                q = callback(action)
                with patch.object(menu, 'OWNER_ID', [999]):
                    await menu.menu_callback(None, q)
                self.assertNotIn(999, review.confirmations)

    async def test_confirmation_is_bound_to_owner(self):
        col, premium, audit, db = self.make_mocks()
        with patch.object(review.pay, 'invoices', col), patch.object(review, 'premium_users_collection', premium), patch.object(review, 'db', db):
            await review.ask_complete(message(), 999, 's', STAR_ID)
            token = review.confirmations[999][0]
            ok, _ = await review.complete(998, token)
            self.assertFalse(ok)
            self.assertIn(999, review.confirmations)
            col.update_one.assert_not_awaited()

    async def test_creating_record_has_no_check_button(self):
        doc = {**invoice('r'), 'status': 'creating'}
        msg = message()
        with patch.object(review, 'get_record', new_callable=AsyncMock, return_value=doc):
            await review.show_detail(msg, 'c', RZP_ID)
        buttons = [b.callback_data for row in msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any('owner_pay_check' in b for b in buttons))

    def test_creation_and_incomplete_records_never_generate_snapshots(self):
        self.assertIsNone(review.snapshot('c', {**invoice('r'), 'status': 'creating'}))
        for field in ('charge_id', 'user_id', 'amount', 'days'):
            doc = invoice()
            doc.pop(field)
            self.assertIsNone(review.snapshot('s', doc))

if __name__ == '__main__':
    unittest.main()
