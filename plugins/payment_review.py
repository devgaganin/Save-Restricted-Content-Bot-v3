"""Owner-only payment review. Never create or extend an entitlement here.

A processing record can be marked completed only when its stored payment ID is
already present in that user's premium document. This is not provider or refund
verification and does not recover payments lacking a stored processing record.
"""
import html
import logging
import re
import secrets
import time
from datetime import datetime

from pyrogram.enums import ParseMode
from plugins import pay, razorpay
from utils.func import db, premium_users_collection
from utils.billing import check_premium_indexes_async

logger = logging.getLogger(__name__)
PAGE_SIZE = 5
MAX_PAGE = 100
CONFIRM_SECONDS = 600
confirmations = {}


def definition(kind):
    if kind == 's':
        return pay.invoices, 'charge_id', 'Stars', 'processing'
    if kind == 'r':
        return razorpay.links, 'payment_id', 'Razorpay', 'processing'
    if kind == 'c':
        return razorpay.links, 'payment_id', 'Razorpay link creation', 'creating'
    return None


def valid_id(kind, value):
    if not isinstance(value, str):
        return False
    if kind == 's':
        return bool(re.fullmatch(r'stars:[A-Za-z0-9_-]{24}', value))
    return kind in ('r', 'c') and bool(re.fullmatch(r'[a-f0-9]{32}', value))


def short_id(kind, value):
    return value[6:] if kind == 's' else value


def full_id(kind, value):
    result = 'stars:' + value if kind == 's' else value
    return result if valid_id(kind, result) else None


def safe(value, length=100):
    return html.escape(str(value if value is not None else 'Unknown')[:length])


def timestamp(value):
    return value.strftime('%d %b %Y %H:%M UTC') if isinstance(value, datetime) else 'Unknown'


async def show_dashboard(message):
    from plugins.menu import markup, page
    body = ('Choose a queue. Processing may still be active; check provider records before taking action. '
            'Creating links and unbound Stars invoices are not proof of payment. '
            'Refunds and disputes are not reconciled here.')
    rows = [[('⭐ 𝗦𝗧𝗔𝗥𝗦 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗜𝗡𝗚', 'owner_pay_list:s:0')],
            [('💳 𝗥𝗔𝗭𝗢𝗥𝗣𝗔𝗬 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗜𝗡𝗚', 'owner_pay_list:r:0')],
            [('⚠️ 𝗟𝗜𝗡𝗞 𝗖𝗥𝗘𝗔𝗧𝗜𝗢𝗡', 'owner_pay_list:c:0')]]
    await message.edit_text(page('🧾 𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗥𝗘𝗩𝗜𝗘𝗪', body), reply_markup=markup(rows, 'owner'), parse_mode=ParseMode.HTML)


async def show_list(message, kind, page_number):
    from plugins.menu import markup, page
    spec = definition(kind)
    if not spec or not 0 <= page_number <= MAX_PAGE:
        raise ValueError('Invalid payment review page')
    collection, _, label, status = spec
    projection = {'_id': 1, 'user_id': 1, 'status': 1, 'created_at': 1}
    cursor = collection.find({'status': status}, projection).sort([('created_at', -1), ('_id', -1)]).skip(page_number * PAGE_SIZE).limit(PAGE_SIZE + 1)
    docs = [doc async for doc in cursor]
    rows = []
    for doc in docs[:PAGE_SIZE]:
        identifier = doc.get('_id')
        if valid_id(kind, identifier):
            rows.append([(f"🧾 User {str(doc.get('user_id', '?'))[:20]} • {timestamp(doc.get('created_at'))}",
                          f'owner_pay_view:{kind}:{short_id(kind, identifier)}')])
    nav = []
    if page_number:
        nav.append(('⬅️ 𝗣𝗥𝗘𝗩', f'owner_pay_list:{kind}:{page_number - 1}'))
    if len(docs) > PAGE_SIZE and page_number < MAX_PAGE:
        nav.append(('𝗡𝗘𝗫𝗧 ➡️', f'owner_pay_list:{kind}:{page_number + 1}'))
    if nav:
        rows.append(nav)
    body = (f'<b>{safe(label)}</b> · page {page_number + 1}\n'
            'These are records, not proof of a current or successful payment. '
            'Latest 505 records are shown. Confirm provider status separately.')
    if not docs:
        body += '\n\nℹ️ No records on this page.'
    await message.edit_text(page('🧾 𝗥𝗘𝗩𝗜𝗘𝗪 𝗤𝗨𝗘𝗨𝗘', body), reply_markup=markup(rows, 'owner_payments'), parse_mode=ParseMode.HTML)


def snapshot(kind, doc):
    """Immutable confirmation data and exact MongoDB conditional-update fields."""
    spec = definition(kind)
    if not spec or kind == 'c' or not valid_id(kind, doc.get('_id')) or doc.get('status') != 'processing':
        return None
    field = spec[1]
    charge = doc.get(field)
    uid, amount, days = doc.get('user_id'), doc.get('amount'), doc.get('days')
    if (not isinstance(charge, str) or not 1 <= len(charge) <= 200
            or not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0
            or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0
            or not isinstance(days, int) or isinstance(days, bool) or days <= 0):
        return None
    result = {'_id': doc['_id'], 'status': 'processing', field: charge,
              'user_id': uid, 'amount': amount, 'days': days}
    if kind == 'r':
        if not isinstance(doc.get('provider_link_id'), str) or not doc['provider_link_id'] or doc.get('currency') != 'INR':
            return None
        result.update(provider_link_id=doc['provider_link_id'], currency='INR')
    return result


async def get_record(kind, identifier):
    spec = definition(kind)
    if not spec or not valid_id(kind, identifier):
        return None
    return await spec[0].find_one({'_id': identifier, 'status': spec[3]})


async def show_detail(message, kind, identifier):
    from plugins.menu import markup, page
    doc = await get_record(kind, identifier)
    spec = definition(kind)
    if not doc or not spec:
        await message.edit_text(page('⚠️ 𝗥𝗘𝗩𝗜𝗘𝗪', 'This record is unavailable or its status changed.'), reply_markup=markup([], 'owner_payments'), parse_mode=ParseMode.HTML)
        return
    field = spec[1]
    currency = 'Stars' if kind == 's' else 'paise (INR)'
    body = (f"🧾 <b>Provider:</b> {safe(spec[2])}\n"
            f"👤 <b>User:</b> <code>{safe(doc.get('user_id'))}</code>\n"
            f"📅 <b>Created:</b> {timestamp(doc.get('created_at'))}\n"
            f"💰 <b>Amount:</b> {safe(doc.get('amount'))} {currency}\n"
            f"⏳ <b>Plan:</b> {safe(doc.get('days'))} days\n"
            f"📌 <b>Status:</b> {safe(doc.get('status'))}\n"
            f"🔖 <b>Invoice reference:</b> <code>{safe(identifier)}</code>\n"
            f"💳 <b>Payment ID:</b> <code>{safe(doc.get(field))}</code>\n\n"
            '⚠️ Check the provider, refunds and buyer receipt independently. '
            'Creating records are not evidence of payment; consult the provider dashboard.')
    rows = []
    if snapshot(kind, doc):
        rows.append([('🔍 𝗖𝗛𝗘𝗖𝗞 𝗔𝗣𝗣𝗟𝗜𝗘𝗗', f'owner_pay_check:{kind}:{short_id(kind, identifier)}')])
    rows.append([('🔙 𝗤𝗨𝗘𝗨𝗘', f'owner_pay_list:{kind}:0')])
    await message.edit_text(page('🧾 𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗗𝗘𝗧𝗔𝗜𝗟𝗦', body), reply_markup=markup(rows, 'owner_payments'), parse_mode=ParseMode.HTML)


async def ask_complete(message, owner, kind, identifier):
    from plugins.menu import markup, page
    confirmations.pop(owner, None)
    doc = await get_record(kind, identifier)
    snap = snapshot(kind, doc or {})
    if not snap:
        await message.edit_text(page('⚠️ 𝗥𝗘𝗩𝗜𝗘𝗪', 'Record is unavailable, incomplete or changed. No action taken.'), reply_markup=markup([], 'owner_payments'), parse_mode=ParseMode.HTML)
        return
    field = definition(kind)[1]
    charge = snap[field] if kind == 's' else 'razorpay:' + snap[field]
    await check_premium_indexes_async(premium_users_collection)
    premium = await premium_users_collection.find_one({'user_id': snap['user_id'], 'applied_payments': charge}, {'_id': 1})
    if not premium:
        await message.edit_text(page('⚠️ 𝗡𝗢 𝗣𝗥𝗢𝗢𝗙 𝗢𝗙 𝗚𝗥𝗔𝗡𝗧', 'The payment ID is not present in this user’s premium record. No entitlement or status was changed. Review the provider and database manually.'), reply_markup=markup([], 'owner_payments'), parse_mode=ParseMode.HTML)
        return
    token = secrets.token_hex(8)
    confirmations[owner] = (token, time.monotonic() + CONFIRM_SECONDS, kind, snap)
    body = ('The stored payment ID is already in this user’s premium record. '
            'After independently checking the provider and refund status, you may mark this invoice completed. '
            'This does not grant or extend premium and does not confirm the current plan is active. Continue?')
    await message.edit_text(page('⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠 𝗥𝗘𝗩𝗜𝗘𝗪', body), reply_markup=markup([[('✅ 𝗠𝗔𝗥𝗞 𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘', 'owner_pay_confirm:' + token), ('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'owner_payments')]], 'owner_payments'), parse_mode=ParseMode.HTML)


async def complete(owner, token):
    """Consume confirmation, revalidate state and audit before changing status."""
    saved = confirmations.pop(owner, None)
    if not saved or not secrets.compare_digest(saved[0], token) or time.monotonic() > saved[1]:
        return False, 'Confirmation expired or already used. Open the record again.'
    _, _, kind, snap = saved
    collection, field, _, _ = definition(kind)
    charge = snap[field] if kind == 's' else 'razorpay:' + snap[field]
    # Fail closed if records, index health or the proof changed since confirmation.
    await check_premium_indexes_async(premium_users_collection)
    if not await collection.find_one(snap):
        return False, 'Record changed or was already processed. Refresh the queue.'
    if await collection.count_documents({field: snap[field]}, limit=2) != 1:
        return False, 'Payment ID is duplicated or unavailable. Review manually.'
    if not await premium_users_collection.find_one({'user_id': snap['user_id'], 'applied_payments': charge}, {'_id': 1}):
        return False, 'The recorded grant could not be confirmed. Review manually.'
    audit = db['admin_audit']
    entry = await audit.insert_one({'owner_id': owner, 'action': 'payment_mark_completed', 'provider': kind,
                                    'invoice_id': snap['_id'], 'user_id': snap['user_id'],
                                    'created_at': datetime.utcnow(), 'status': 'processing'})
    result = await collection.update_one(snap, {'$set': {'status': 'completed', 'completed_at': datetime.utcnow(), 'completed_by_review': owner}})
    outcome = 'completed' if result.matched_count == 1 else 'changed'
    try:
        await audit.update_one({'_id': entry.inserted_id}, {'$set': {'status': outcome, 'finished_at': datetime.utcnow()}})
    except Exception:
        logger.exception('Payment review audit finalization failed')
    if outcome != 'completed':
        return False, 'Record changed or was already processed. Refresh the queue.'
    return True, 'Invoice marked completed. No premium was granted or extended; verify current status separately.'
