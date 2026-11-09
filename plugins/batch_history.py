"""Private, read-only archive views and explicit history cleanup.

The currently saved batch is deliberately separate: history deletion must not
remove a job needed for resume/retry. One worker instance is required.
"""
import html
import re
import secrets
import time
from datetime import datetime

from pyrogram.enums import ParseMode
from plugins import batch_queue as queue, localization

PAGE_SIZE = 5
MAX_PAGE = 200
CONFIRM_SECONDS = 600
# Short-lived confirmation tokens; never contain user data or credentials.
confirmations = {}
JOB_ID = re.compile(r'^[0-9a-f]{24}$')


def display_time(value, language='en'):
    return value.strftime('%d %b %Y %H:%M UTC') if isinstance(value, datetime) else ('अज्ञात' if language == 'hi' else 'Unknown')


def value_text(value, limit=100):
    text = str(value if value is not None else 'Unknown')
    if len(text) > limit:
        text = text[:limit] + '…'
    return html.escape(text)


async def archived_page(uid, page_number):
    """Query only this user; exclude any current job archived before replacement."""
    if not 0 <= page_number <= MAX_PAGE:
        return None
    async with queue.lock_for(uid):
        current = await queue.get_job(uid)
        selector = {'user_id': uid}
        if current and current.get('job_id'):
            selector['_id'] = {'$ne': current['job_id']}
        projection = {'_id': 1, 'status': 1, 'created_at': 1, 'archived_at': 1,
                      'total': 1, 'current': 1, 'success': 1, 'failed': 1,
                      'skipped': 1, 'uncertain': 1}
        cursor = queue.history.find(selector, projection).sort([('archived_at', -1), ('_id', -1)]).skip(page_number * PAGE_SIZE).limit(PAGE_SIZE + 1)
        return [entry async for entry in cursor]


async def show_history(message, uid, page_number=0, language='en'):
    from plugins.menu import markup, page
    entries = await archived_page(uid, page_number)
    if entries is None:
        body = 'इतिहास का पेज मान्य नहीं है।' if language == 'hi' else 'Invalid history page.'
        await message.edit_text(page('⚠️ इतिहास' if language == 'hi' else '⚠️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', body), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
        return
    rows = []
    for entry in entries[:PAGE_SIZE]:
        job_id = entry.get('_id')
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            continue
        status = localization.status_label(entry.get('status', 'unknown'), language)[:32]
        rows.append([(f"📄 {display_time(entry.get('archived_at'), language)} • {status}", f'history_view:{job_id}')])
    nav = []
    if page_number > 0:
        nav.append(('⬅️ 𝗣𝗥𝗘𝗩', f'history_page:{page_number - 1}'))
    if len(entries) > PAGE_SIZE and page_number < MAX_PAGE:
        nav.append(('𝗡𝗘𝗫𝗧 ➡️', f'history_page:{page_number + 1}'))
    if nav:
        rows.append(nav)
    rows.append([('📊 𝗖𝗨𝗥𝗥𝗘𝗡𝗧 𝗧𝗔𝗦𝗞', 'task')])
    rows.append([('🗑️ 𝗖𝗟𝗘𝗔𝗥 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'history_clear')])
    if language == 'hi':
        body = (f'📁 <b>आर्काइव बैच · पेज {page_number + 1}</b>\n'
                'केवल नवीनतम 1,005 आर्काइव बैच दिखते हैं। मौजूदा बैच टास्क स्थिति में अलग है; आर्काइव सिर्फ़ देखे जा सकते हैं।')
        if not entries:
            body += '\n\nℹ️ इस पेज पर कोई आर्काइव बैच नहीं है।'
    else:
        body = (f'📁 <b>Archived batches · page {page_number + 1}</b>\n'
                'Latest 1,005 archived batches only. The current batch is separate under Task Status; archives are view-only.')
        if not entries:
            body += '\n\nℹ️ No archived batches on this page.'
    await message.edit_text(page('🗂️ बैच इतिहास' if language == 'hi' else '🗂️ 𝗕𝗔𝗧𝗖𝗛 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', body), reply_markup=markup(rows, 'save', language), parse_mode=ParseMode.HTML)


async def show_detail(message, uid, job_id, language='en'):
    from plugins.menu import markup, page
    if not JOB_ID.fullmatch(job_id):
        entry = None
    else:
        # Ownership is part of the query, not inferred from the callback ID.
        entry = await queue.history.find_one({'_id': job_id, 'user_id': uid},
            {'source': 1, 'destination': 1, 'status': 1, 'created_at': 1,
             'archived_at': 1, 'total': 1, 'current': 1, 'success': 1,
             'failed': 1, 'skipped': 1, 'uncertain': 1, 'source_topic': 1})
    if not entry:
        body = 'यह आर्काइव बैच उपलब्ध नहीं है।' if language == 'hi' else 'This archived batch is unavailable.'
    else:
        status = html.escape(localization.status_label(entry.get('status'), language)[:24])
        if language == 'hi':
            topic = (f"🧵 <b>स्रोत टॉपिक:</b> {value_text(entry['source_topic'], 20)} · ID स्कैन की गईं\n"
                     if entry.get('source_topic') is not None else '')
            body = (f"📌 <b>स्थिति:</b> {status}\n"
                    f"📅 <b>बनाया:</b> {display_time(entry.get('created_at'), language)}\n"
                    f"🗂️ <b>आर्काइव किया:</b> {display_time(entry.get('archived_at'), language)}\n"
                    f"📥 <b>स्रोत:</b> <code>{value_text(entry.get('source'))}</code>\n"
                    f"📤 <b>डेस्टिनेशन:</b> <code>{value_text(entry.get('destination'))}</code>\n"
                    f"{topic}\n{html.escape(queue.summary(entry, language))}\n\n"
                    'ℹ️ आर्काइव सिर्फ़ देखे जा सकते हैं। मौजूदा बैच के लिए टास्क स्थिति देखें।')
        else:
            topic = (f"🧵 <b>Source topic:</b> {value_text(entry['source_topic'], 20)} · IDs scanned\n"
                     if entry.get('source_topic') is not None else '')
            body = (f"📌 <b>Status:</b> {status}\n"
                    f"📅 <b>Created:</b> {display_time(entry.get('created_at'), language)}\n"
                    f"🗂️ <b>Archived:</b> {display_time(entry.get('archived_at'), language)}\n"
                    f"📥 <b>Source:</b> <code>{value_text(entry.get('source'))}</code>\n"
                    f"📤 <b>Destination:</b> <code>{value_text(entry.get('destination'))}</code>\n"
                    f"{topic}\n{html.escape(queue.summary(entry))}\n\n"
                    'ℹ️ Archived records are view-only. Use Task Status for the current batch.')
    await message.edit_text(page('📄 बैच विवरण' if language == 'hi' else '📄 𝗕𝗔𝗧𝗖𝗛 𝗗𝗘𝗧𝗔𝗜𝗟𝗦', body), reply_markup=markup([], 'history', language), parse_mode=ParseMode.HTML)


async def ask_clear(message, uid, language='en'):
    from plugins.menu import markup, page
    async with queue.lock_for(uid):
        token = secrets.token_hex(8)
        confirmations[uid] = (token, time.monotonic() + CONFIRM_SECONDS)
    body = (('🗑️ इससे आपके सभी आर्काइव रिकॉर्ड हटेंगे, जिनमें पुष्टि से पहले आर्काइव किए गए रिकॉर्ड भी शामिल हैं। '
             'आपका मौजूदा टास्क और उसकी सुरक्षित आर्काइव कॉपी बनी रहेगी। '
             'टास्क बदलने पर वह बाद में इतिहास में आ सकता है। जारी रखें?') if language == 'hi' else
            ('🗑️ This deletes your archived records, including any archived before you confirm. '
             'Your current task and any protected archive copy of it will be kept. '
             'That task may appear in history later when replaced. Continue?'))
    await message.edit_text(page('⚠️ इतिहास हटाएँ' if language == 'hi' else '⚠️ 𝗖𝗟𝗘𝗔𝗥 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', body), reply_markup=markup([[('✅ 𝗗𝗘𝗟𝗘𝗧𝗘', 'history_clear_yes:' + token), ('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'history')]], 'save', language), parse_mode=ParseMode.HTML)


async def cancel_confirmation(uid):
    async with queue.lock_for(uid):
        confirmations.pop(uid, None)


async def clear_history(uid, token):
    async with queue.lock_for(uid):
        saved = confirmations.get(uid)
        if saved and saved[1] < time.monotonic():
            confirmations.pop(uid, None)
            saved = None
        if not saved or not re.fullmatch(r'[0-9a-f]{16}', token) or not secrets.compare_digest(saved[0], token):
            return False, 'Confirmation expired. Open History and try again.'
        confirmations.pop(uid, None)  # one-shot even if a database write is uncertain
        # The current job may also have been archived just before an interrupted
        # replacement, so preserve its archived duplicate too.
        current = await queue.get_job(uid)
        selector = {'user_id': uid}
        if current and current.get('job_id'):
            selector['_id'] = {'$ne': current['job_id']}
        result = await queue.history.delete_many(selector)
        return True, f'Deleted {result.deleted_count} archived records. Your current task is unchanged.'
