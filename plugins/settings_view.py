"""Private, read-only view of content settings; credentials are never displayed."""
import html
import os

from pyrogram.enums import ParseMode
from utils.func import users_collection


def safe(value, limit=160, default='Not set'):
    if not isinstance(value, str) or not value:
        return default
    # Telegram HTML does not accept arbitrary control characters.
    value = ''.join(c for c in value if ord(c) >= 32 or c in '\n\t')
    if len(value) > limit:
        value = value[:limit] + '…'
    return html.escape(value)


async def show_settings(message, uid):
    from plugins.menu import markup, page
    # Do not use a helper that converts a database error into an empty record.
    fields = ('chat_id', 'rename_tag', 'caption', 'prefix', 'suffix', 'media_filter',
              'remove_caption', 'delete_words', 'replacement_words', 'language')
    projection = {key: 1 for key in fields}
    projection['_id'] = 0
    data = await users_collection.find_one({'user_id': uid}, projection) or {}
    deleted = data.get('delete_words')
    replaced = data.get('replacement_words')
    word_count = len(deleted) if isinstance(deleted, list) else 0
    rule_count = len(replaced) if isinstance(replaced, dict) else 0
    language = 'hi' if data.get('language') == 'hi' else 'en'
    if language == 'hi':
        missing = 'सेट नहीं है'
        dest = safe(data.get('chat_id'), default=missing) if data.get('chat_id') else 'आपकी निजी चैट (डिफ़ॉल्ट)'
        body = (f"📍 <b>डेस्टिनेशन:</b> {dest}\n"
                f"🏷️ <b>नाम टैग:</b> {safe(data.get('rename_tag'), default=missing)}\n"
                f"💬 <b>कैप्शन:</b> {safe(data.get('caption'), default=missing)}\n"
                f"⬆️ <b>प्रीफ़िक्स:</b> {safe(data.get('prefix'), default=missing)}\n"
                f"⬇️ <b>सफ़िक्स:</b> {safe(data.get('suffix'), default=missing)}\n"
                f"🔽 <b>मीडिया फ़िल्टर:</b> {safe(data.get('media_filter') or 'all')}\n"
                f"📝 <b>मूल कैप्शन:</b> {'हटाया जाएगा' if data.get('remove_caption') else 'रखा जाएगा'}\n"
                f"🖼️ <b>थंबनेल:</b> {'सेट है' if os.path.isfile(f'{uid}.jpg') else missing}\n"
                f"🗑️ <b>हटाए जाने वाले शब्द:</b> {word_count}\n"
                f"🔄 <b>बदलने के नियम:</b> {rule_count}\n\n"
                'ℹ️ लंबे मान यहाँ छोटे दिखते हैं। बैच का डेस्टिनेशन शुरू होने पर तय होता है; अन्य सेटिंग्स प्रोसेसिंग के दौरान बदल सकती हैं।')
    else:
        dest = safe(data.get('chat_id')) if data.get('chat_id') else 'Your private chat (default)'
        body = (f"📍 <b>Destination:</b> {dest}\n"
                f"🏷️ <b>Rename tag:</b> {safe(data.get('rename_tag'))}\n"
                f"💬 <b>Caption:</b> {safe(data.get('caption'))}\n"
                f"⬆️ <b>Prefix:</b> {safe(data.get('prefix'))}\n"
                f"⬇️ <b>Suffix:</b> {safe(data.get('suffix'))}\n"
                f"🔽 <b>Media filter:</b> {safe(data.get('media_filter') or 'all')}\n"
                f"📝 <b>Original caption:</b> {'Removed' if data.get('remove_caption') else 'Kept'}\n"
                f"🖼️ <b>Thumbnail:</b> {'Set' if os.path.isfile(f'{uid}.jpg') else 'Not set'}\n"
                f"🗑️ <b>Delete words:</b> {word_count}\n"
                f"🔄 <b>Replacement rules:</b> {rule_count}\n\n"
                'ℹ️ Long values are shortened here. Batch destination is captured when a batch starts; other settings may change during processing.')
    await message.edit_text(page('👀 आपकी सेटिंग्स' if language == 'hi' else '👀 𝗬𝗢𝗨𝗥 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', body), reply_markup=markup([], 'settings', language), parse_mode=ParseMode.HTML)
