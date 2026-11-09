"""Owner-only manual premium grants with explicit, non-retrying failure states."""
import html
import logging
import re
from datetime import datetime, timezone

from pyrogram.enums import ParseMode
from utils.billing import UnsafePremiumIndexError, check_premium_indexes_async
from utils.func import db, premium_users_collection
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

logger = logging.getLogger(__name__)


def _text(language, english, hindi):
    return hindi if language == 'hi' else english


def parse_grant(text):
    """A positive Telegram user ID and a bounded whole number of days."""
    parts = text.split()
    if len(parts) != 2 or not all(re.fullmatch(r'[0-9]{1,19}', x) for x in parts):
        return None
    uid, days = map(int, parts)
    if not (1 <= uid <= 2**63 - 1 and 1 <= days <= 3650):
        return None
    return uid, days


async def _reply_safely(message, text, **kwargs):
    try:
        await message.reply_text(text, **kwargs)
    except Exception:
        # A Telegram response failure must not turn a completed grant into an
        # instruction to submit it again.
        logger.warning('Could not deliver owner grant result')


async def apply_owner_grant(client, message, owner_id, text, language='en'):
    """Return False only for invalid input, so it can be corrected or cancelled.

    Caller verifies owner authorization and reserves the owner's input flow.
    A processing audit entry is created *before* a single idempotent grant.
    Any failure after that point is treated as uncertain, not auto-retried.
    """
    parsed = parse_grant(text)
    if not parsed:
        await _reply_safely(message, _text(language,
            '⚠️ Send a positive numeric user ID and 1–3650 days, for example: 123456789 30.',
            '⚠️ सही संख्यात्मक यूज़र ID और 1–3650 दिन भेजें, जैसे: 123456789 30.'))
        return False
    target, days = parsed
    try:
        # Historical TTL indexes delete payment idempotency history. Never
        # remove or bypass them automatically just to make a grant succeed.
        await check_premium_indexes_async(premium_users_collection)
    except UnsafePremiumIndexError:
        await _reply_safely(message, _text(language,
            '⚠️ Premium was not changed: an unsafe TTL index exists on premium_users. Stop old workers, back up and review the README payment-history migration before removing it.',
            '⚠️ प्रीमियम नहीं बदला: premium_users पर असुरक्षित TTL index है। पुराने वर्कर रोकें, बैकअप लें और हटाने से पहले README का payment-history migration देखें।'))
        return True
    except Exception:
        logger.warning('Could not inspect premium indexes before manual grant')
        await _reply_safely(message, _text(language,
            '⚠️ Premium was not changed: could not inspect database indexes. Check MongoDB connectivity and permissions.',
            '⚠️ प्रीमियम नहीं बदला: डेटाबेस indexes की जाँच नहीं हुई। MongoDB कनेक्शन और permissions जाँचें।'))
        return True
    try:
        await premium_users_collection.create_index('user_id', unique=True)
    except DuplicateKeyError:
        await _reply_safely(message, _text(language,
            '⚠️ Premium was not changed: duplicate premium_users user IDs prevent the unique index. Back up and reconcile duplicates before retrying.',
            '⚠️ प्रीमियम नहीं बदला: premium_users में duplicate user IDs के कारण unique index नहीं बना। दोबारा कोशिश से पहले बैकअप लेकर duplicates जाँचें।'))
        return True
    except Exception as exc:
        # Some MongoDB/PyMongo versions report duplicate-index construction as
        # OperationFailure rather than DuplicateKeyError.
        if getattr(exc, 'code', None) in (11000, 11001, 12582):
            body = _text(language,
                '⚠️ Premium was not changed: duplicate premium_users user IDs prevent the unique index. Back up and reconcile duplicates before retrying.',
                '⚠️ प्रीमियम नहीं बदला: premium_users में duplicate user IDs के कारण unique index नहीं बना। दोबारा कोशिश से पहले बैकअप लेकर duplicates जाँचें।')
        else:
            logger.warning('Could not prepare premium user index')
            body = _text(language,
                '⚠️ Premium was not changed: could not prepare the database index. Check MongoDB permissions and index configuration.',
                '⚠️ प्रीमियम नहीं बदला: डेटाबेस index तैयार नहीं हुआ। MongoDB permissions और index configuration जाँचें।')
        await _reply_safely(message, body)
        return True
    audit = db['admin_audit']
    try:
        entry = await audit.insert_one({'owner_id': owner_id, 'action': 'grant', 'target_id': target,
                                        'days': days, 'status': 'processing', 'created_at': datetime.utcnow()})
        if not entry.acknowledged or entry.inserted_id is None:
            raise RuntimeError('Audit insert not acknowledged')
    except Exception:
        logger.warning('Could not confirm manual grant audit insertion')
        await _reply_safely(message, _text(language,
            '⚠️ Premium was not changed: the audit record could not be confirmed. Check MongoDB and try again.',
            '⚠️ प्रीमियम नहीं बदला: audit रिकॉर्ड की पुष्टि नहीं हुई। MongoDB जाँचकर फिर कोशिश करें।'))
        return True
    audit_id = str(entry.inserted_id)
    charge_id = 'manual:' + audit_id
    try:
        from plugins.pay import grant_invoice
        result = await grant_invoice({'user_id': target, 'days': days}, charge_id)
        if not result or charge_id not in result.get('applied_payments', []):
            raise RuntimeError('Grant is not confirmed')
    except Exception:
        logger.warning('Manual premium grant outcome uncertain; audit %s', audit_id)
        safe_id = html.escape(audit_id)
        body = _text(language,
            f'⚠️ Grant outcome is uncertain (audit <code>{safe_id}</code>). It may already be applied. Do not grant again until the user’s plan and this audit/payment ID are checked. MongoDB 4.2+ is required.',
            f'⚠️ Grant का परिणाम अनिश्चित है (audit <code>{safe_id}</code>)। यह लागू हो चुका हो सकता है। यूज़र का प्लान और यह audit/payment ID जाँचने से पहले दोबारा grant न करें। MongoDB 4.2+ चाहिए।')
        await _reply_safely(message, body, parse_mode=ParseMode.HTML)
        return True
    try:
        updated = await audit.update_one({'_id': entry.inserted_id, 'owner_id': owner_id, 'action': 'grant'},
                                         {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}})
        audit_ok = bool(updated.acknowledged and updated.matched_count)
    except Exception:
        audit_ok = False
    if not audit_ok:
        logger.warning('Manual grant confirmed but audit completion unconfirmed: %s', audit_id)
    expiry = result.get('subscription_end')
    if isinstance(expiry, datetime):
        if expiry.tzinfo is not None and expiry.utcoffset() is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        date = expiry.strftime('%d %b %Y %H:%M UTC')
        is_active = expiry > datetime.utcnow()
        if is_active:
            status = _text(language, f'Active until {date}', f'सक्रिय; समाप्ति: {date}')
        else:
            status = _text(language, f'Not currently active; recorded expiry {date}', f'अभी सक्रिय नहीं; दर्ज समाप्ति {date}')
    else:
        is_active = False
        status = _text(language, 'Current expiry could not be verified.', 'मौजूदा समाप्ति की पुष्टि नहीं हुई।')
    audit_notice = '' if audit_ok else _text(language,
        ' ⚠️ Grant recorded, but audit completion could not be confirmed; review the audit.',
        ' ⚠️ Grant दर्ज है, लेकिन audit पूरा होने की पुष्टि नहीं हुई; audit जाँचें।')
    notified = False
    if is_active and client is not None:
        try:
            await client.send_message(target,
                f'✅ Premium grant recorded. Active until {date} when checked. Check Account → Status for the latest plan status.')
            notified = True
        except Exception:
            logger.warning('Could not notify manual grant recipient')
    notify = _text(language, 'User notified.' if notified else 'User notification not confirmed.',
                   'यूज़र को सूचना भेजी गई।' if notified else 'यूज़र को सूचना भेजने की पुष्टि नहीं हुई।')
    body = _text(language,
        f'✅ Grant of {days} days recorded for user {target}. {status}. {notify} Reference: {audit_id}.{audit_notice}',
        f'✅ यूज़र {target} के लिए {days} दिनों का grant दर्ज हुआ। {status}। {notify} संदर्भ: {audit_id}।{audit_notice}')
    await _reply_safely(message, body)
    return True


async def check_owner_grant(message, owner_id, text, language='en'):
    """Read-only evidence for an owner's grant; never retries or changes it."""
    if not re.fullmatch(r'[0-9a-fA-F]{24}', text):
        await _reply_safely(message, _text(language,
            '⚠️ Send the 24-character grant reference shown after the request.',
            '⚠️ Grant अनुरोध के बाद दिखा 24-अक्षर का संदर्भ भेजें।'))
        return False
    try:
        object_id = ObjectId(text)
        item = await db['admin_audit'].find_one({'_id': object_id, 'owner_id': owner_id, 'action': 'grant'},
                                                {'_id': 1, 'target_id': 1, 'days': 1, 'status': 1})
        if not item:
            await _reply_safely(message, _text(language,
                '⚠️ Grant reference not found for your owner account. Do not grant again until it is reviewed.',
                '⚠️ इस ओनर अकाउंट के लिए grant संदर्भ नहीं मिला। जाँच पूरी होने से पहले दोबारा grant न करें।'))
            return True
        target, days = item.get('target_id'), item.get('days')
        if type(target) is not int or not 1 <= target <= 2**63 - 1 or type(days) is not int or not 1 <= days <= 3650:
            raise ValueError('Invalid audit record')
        charge = 'manual:' + str(object_id)
        premium = await premium_users_collection.find_one({'user_id': target, 'applied_payments': charge},
                                                           {'_id': 0, 'subscription_end': 1})
    except Exception:
        logger.warning('Could not review owner grant')
        await _reply_safely(message, _text(language,
            '⚠️ Could not review this grant. Do not grant again until its outcome is checked.',
            '⚠️ इस grant की जाँच नहीं हुई। परिणाम जाँचने से पहले दोबारा grant न करें।'))
        return True
    if premium is None:
        body = _text(language,
            f'⚠️ Grant for user {target}, {days} days is not confirmed in payment history. It may still be in flight or history may be incomplete. Review before granting again.',
            f'⚠️ यूज़र {target} के {days} दिन का grant payment history में पुष्ट नहीं है। यह अभी चल रहा हो सकता है या history अधूरी हो सकती है। दोबारा grant से पहले जाँच करें।')
    else:
        end = premium.get('subscription_end')
        if isinstance(end, datetime):
            if end.tzinfo is not None and end.utcoffset() is not None:
                end = end.astimezone(timezone.utc).replace(tzinfo=None)
            date = end.strftime('%d %b %Y %H:%M UTC')
            current = _text(language, ('Current plan active until ' if end > datetime.utcnow() else 'Current plan expired on ') + date,
                            ('मौजूदा प्लान सक्रिय, समाप्ति: ' if end > datetime.utcnow() else 'मौजूदा प्लान समाप्त: ') + date)
        else:
            current = _text(language, 'Current expiry is unknown.', 'मौजूदा समाप्ति अज्ञात है।')
        body = _text(language,
            f'✅ Grant for user {target}, {days} days is recorded in payment history. {current}',
            f'✅ यूज़र {target} के {days} दिन का grant payment history में दर्ज है। {current}')
    await _reply_safely(message, body)
    return True
