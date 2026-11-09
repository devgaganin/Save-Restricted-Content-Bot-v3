# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

"""Telegram Stars checkout with stored invoice snapshots and retry-safe grants."""
import logging
import secrets
from datetime import datetime, timedelta
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard, LabeledPrice
from config import P0, OWNER_ID
from shared_client import app
from utils.func import db, premium_users_collection
from utils.billing import check_premium_indexes_async

logger = logging.getLogger(__name__)
invoices = db['stars_invoices']
DURATION_DAYS = {'days': 1, 'weeks': 7, 'month': 30, 'year': 365}


async def show_plans(message):
    rows = [[Button(f"⭐ {p['l']} — {p['s']} Stars", callback_data=f"stars:{key}")] for key, p in P0.items() if p['s'] > 0 and p['u'] in DURATION_DAYS and p['du'] > 0]
    rows.append([Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')])
    await message.reply_text('💎 <b>𝗧𝗘𝗟𝗘𝗚𝗥𝗔𝗠 𝗦𝗧𝗔𝗥𝗦</b>\n\n<blockquote>⭐ <b>Choose a plan to receive an invoice.</b></blockquote>', reply_markup=Keyboard(rows), parse_mode=ParseMode.HTML)


@app.on_message(filters.command('pay') & filters.private)
async def pay_command(client, message):
    await show_plans(message)


@app.on_callback_query(filters.regex(r'^stars:(d|w|m)$'))
async def create_invoice(client, query):
    if not query.message or query.message.chat.type.value != 'private':
        await query.answer('Please use a private chat.', show_alert=True)
        return
    key = query.data.split(':', 1)[1]
    plan = P0[key]
    if plan['s'] <= 0 or plan['du'] <= 0 or plan['u'] not in DURATION_DAYS:
        await query.answer('This plan is not configured.', show_alert=True)
        return
    await query.answer('Creating invoice…')
    token = secrets.token_urlsafe(18)
    payload = f'stars:{token}'
    invoice = {'_id': payload, 'user_id': query.from_user.id, 'amount': plan['s'], 'days': plan['du'] * DURATION_DAYS[plan['u']], 'label': plan['l'], 'created_at': datetime.utcnow(), 'status': 'created'}
    try:
        await check_premium_indexes_async(premium_users_collection)
        # Required for atomic idempotency, including concurrent first purchases.
        await premium_users_collection.create_index('user_id', unique=True)
        await invoices.insert_one(invoice)
        await client.send_invoice(chat_id=query.from_user.id, title=f"Premium {plan['l']}", description=f"{invoice['days']} days of premium", payload=payload, currency='XTR', prices=[LabeledPrice(label=f"Premium {plan['l']}", amount=plan['s'])])
    except Exception:
        logger.exception('Failed to create Stars invoice')
        await query.message.reply_text('⚠️ Could not create invoice. Contact the administrator.')


def matches_invoice(invoice, user_id, currency, amount):
    return bool(invoice and invoice['user_id'] == user_id and currency == 'XTR' and invoice['amount'] == amount)


@app.on_pre_checkout_query()
async def pre_checkout(client, query):
    try:
        invoice = await invoices.find_one({'_id': query.payload})
        valid = matches_invoice(invoice, query.from_user.id, query.currency, query.total_amount) and invoice['status'] == 'created'
        if valid:
            await check_premium_indexes_async(premium_users_collection)
    except Exception:
        logger.exception('Stars pre-checkout validation failed')
        valid = False
    await query.answer(ok=valid, error_message=None if valid else 'Invoice is invalid or no longer available.')


async def grant_invoice(invoice, charge_id):
    """Use one atomic premium-document update so redelivery cannot extend twice."""
    await check_premium_indexes_async(premium_users_collection)
    uid = invoice['user_id']
    now = datetime.utcnow()
    duration_ms = invoice['days'] * 86400 * 1000
    pipeline = [{'$set': {
        'user_id': uid,
        'subscription_start': {'$ifNull': ['$subscription_start', now]},
        'subscription_end': {'$add': [{'$max': [{'$ifNull': ['$subscription_end', now]}, now]}, duration_ms]},
        'expireAt': {'$add': [{'$max': [{'$ifNull': ['$subscription_end', now]}, now]}, duration_ms]},
        'applied_payments': {'$concatArrays': [{'$ifNull': ['$applied_payments', []]}, [charge_id]]},
    }}]
    try:
        await premium_users_collection.update_one({'user_id': uid, 'applied_payments': {'$ne': charge_id}}, pipeline, upsert=True)
    except DuplicateKeyError:
        # Either a concurrent first grant created the document or this payment
        # was applied already. Retry without upsert to distinguish these cases.
        await premium_users_collection.update_one({'user_id': uid, 'applied_payments': {'$ne': charge_id}}, pipeline)
    return await premium_users_collection.find_one({'user_id': uid})


@app.on_message(filters.successful_payment & filters.private)
async def successful_payment(client, message):
    payment = message.successful_payment
    uid = message.from_user.id
    charge = payment.telegram_payment_charge_id
    try:
        invoice = await invoices.find_one({'_id': payment.payload})
        if not matches_invoice(invoice, uid, payment.currency, payment.total_amount):
            raise ValueError('Invoice details do not match payment')
        if invoice.get('status') == 'completed':
            if invoice.get('charge_id') != charge:
                raise ValueError('Invoice was already paid with another charge')
            return
        # Bind the first charge to this invoice; do not accidentally apply a
        # second payment against an invoice already being processed.
        invoice = await invoices.find_one_and_update(
            {'_id': payment.payload, 'status': {'$in': ['created', 'processing']}, '$or': [{'charge_id': {'$exists': False}}, {'charge_id': charge}]},
            {'$set': {'status': 'processing', 'charge_id': charge}}, return_document=ReturnDocument.AFTER)
        if not invoice:
            raise ValueError('Invoice already bound to another payment')
        premium = await grant_invoice(invoice, charge)
        if not premium or charge not in premium.get('applied_payments', []):
            raise RuntimeError('Premium activation not confirmed')
        await invoices.update_one({'_id': payment.payload, 'charge_id': charge}, {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}})
        expiry = premium['subscription_end'] + timedelta(hours=5, minutes=30)
        await message.reply_text(f'✅ <b>𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗦𝗨𝗖𝗖𝗘𝗦𝗦𝗙𝗨𝗟</b>\n\n<blockquote>💎 <b>Premium active until:</b> {expiry:%d %b %Y %H:%M} IST\n⭐ <b>Paid:</b> {payment.total_amount} Stars</blockquote>', parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception('Stars payment needs manual review: %s', charge)
        await message.reply_text('⚠️ Payment received, but activation needs review. Please contact support with your Telegram payment receipt.')
        for owner in OWNER_ID:
            try: await client.send_message(owner, f'⚠️ Stars payment needs review. User {uid}; charge {charge}.')
            except Exception: logger.exception('Could not notify owner of payment failure')
