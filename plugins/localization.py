"""Bilingual main-menu and input-prompt strings. Other workflows remain English."""
import logging

from config import FREEMIUM_LIMIT, PREMIUM_LIMIT
from utils.func import users_collection

log = logging.getLogger(__name__)
SUPPORTED = ('en', 'hi')


async def language_for(uid):
    """Read only the preference; never fetch credentials to render a menu."""
    try:
        doc = await users_collection.find_one({'user_id': uid}, {'language': 1, '_id': 0}) or {}
        return doc.get('language') if doc.get('language') in SUPPORTED else 'en'
    except Exception:
        log.warning('Could not load language preference; using English')
        return 'en'


HI_PAGES = {
    'home': ('🔒 𝗦𝗔𝗩𝗘 𝗖𝗢𝗡𝗧𝗘𝗡𝗧', '👋 <b>नमस्ते! नीचे से विकल्प चुनें।</b>\n\n📌 <b>केवल वही सामग्री सेव करें जिसकी आपको अनुमति है।</b>'),
    'save': ('📥 पोस्ट सेव करें', '📌 <b>एक पोस्ट, बैच या टॉपिक बैच चुनें।</b>\n🔐 <b>पहले अपना अकाउंट और कस्टम बॉट जोड़ें।</b>'),
    'download': ('🎬 डाउनलोड', '🌐 <b>फ़ॉर्मैट चुनें और समर्थित लिंक भेजें।</b>\n⚠️ <b>उपलब्धता स्रोत पर निर्भर है।</b>'),
    'account': ('🔐 अकाउंट', '👤 <b>अपना टेलीग्राम सेशन और कस्टम बॉट प्रबंधित करें।</b>\n⚠️ <b>सिर्फ़ भरोसेमंद बॉट ऑपरेटर को अकाउंट से जोड़ें।</b>'),
    'custombot': ('🤖 कस्टम बॉट', '🔒 <b>बॉट टोकन से आपके बॉट पर नियंत्रण मिलता है। इसे सिर्फ़ भरोसेमंद ऑपरेटर से साझा करें।</b>'),
    'settings': ('⚙️ सेटिंग्स', '🛠️ <b>जिस सेटिंग को बदलना है उसे चुनें।</b>'),
    'premium': ('💎 प्रीमियम', f'📚 <b>फ़्री बैच सीमा:</b> {FREEMIUM_LIMIT}\n🚀 <b>प्रीमियम बैच सीमा:</b> {PREMIUM_LIMIT}\n\n⭐ <b>भुगतान विकल्प चुनें।</b>'),
    'filters': ('🔽 मीडिया फ़िल्टर', 'वे संदेश प्रकार चुनें जिन्हें प्रोसेस करना है।'),
    'owner': ('👑 ओनर पैनल', '🛡️ <b>केवल ओनर के लिए नियंत्रण। बदलावों का रिकॉर्ड रखा जाता है।</b>'),
    'help': ('❓ सहायता', '1️⃣ <b>अपना अकाउंट और कस्टम बॉट जोड़ें।</b>\n2️⃣ <b>पोस्ट सेव करें में सिंगल, बैच या टॉपिक बैच चुनें।</b>\n3️⃣ <b>माँगा गया लिंक और संख्या भेजें।</b>\n\n🔒 <b>लॉगिन कोड, पासवर्ड, टोकन या गिफ़्ट कार्ड कोड किसी अविश्वसनीय ऑपरेटर को न दें।</b>'),
    'language': ('🌐 भाषा / Language', 'मुख्य मेनू और कुछ इनपुट निर्देशों के लिए भाषा चुनें। अन्य स्क्रीन अभी अंग्रेज़ी में हो सकती हैं।'),
}

HI_BUTTONS = {
    'save': '📥 पोस्ट सेव करें', 'download': '🎬 डाउनलोड', 'settings': '⚙️ सेटिंग्स',
    'account': '🔐 अकाउंट', 'premium': '💎 प्रीमियम', 'help': '❓ सहायता', 'owner': '👑 ओनर',
    'single': '1️⃣ एक पोस्ट', 'batch': '📚 बैच', 'topic_batch': '🧵 टॉपिक बैच',
    'task': '📊 टास्क स्थिति', 'history': '🗂️ इतिहास', 'stop': '🛑 टास्क रोकें',
    'pause': '⏸️ रोकें', 'resume': '▶️ फिर शुरू करें', 'mirror': '🪞 लाइव मिरर', 'mirror_stop': '⏹️ मिरर रोकें', 'mirror_status': '📊 मिरर स्थिति',
    'video': '🎥 वीडियो', 'audio': '🎵 ऑडियो', 'status': '📊 स्थिति', 'login': '🔑 लॉगिन',
    'logout_confirm': '🚪 लॉगआउट', 'custombot': '🤖 कस्टम बॉट', 'delete_data_confirm': '🗑️ डेटा हटाएँ',
    'addbot': '➕ बॉट जोड़ें', 'removebot_confirm': '🗑️ बॉट हटाएँ', 'settings_view': '👀 सेटिंग्स देखें',
    'settings_clear': '🧹 एक सेटिंग हटाएँ', 'destination': '📍 डेस्टिनेशन', 'rename': '🏷️ नाम टैग',
    'caption': '💬 कैप्शन', 'thumbnail': '🖼️ थंबनेल', 'deletewords': '🗑️ शब्द हटाएँ',
    'replacewords': '🔄 शब्द बदलें', 'prefix': '⬆️ प्रीफ़िक्स', 'suffix': '⬇️ सफ़िक्स',
    'filters': '🔽 मीडिया फ़िल्टर', 'toggle_caption': '💬 मूल कैप्शन', 'reset_confirm': '♻️ रीसेट',
    'language': '🌐 भाषा / Language', 'stars': '⭐ टेलीग्राम स्टार्स', 'razorpay': '💳 रेज़रपे',
    'gift': '🎁 गिफ़्ट कार्ड', 'reminders': '⏰ रिमाइंडर', 'filter_all': '📑 सभी',
    'filter_photo': '🖼️ फोटो', 'filter_video': '🎬 वीडियो', 'filter_document': '📄 दस्तावेज़',
    'filter_audio': '🎵 ऑडियो', 'filter_text': '📝 टेक्स्ट', 'owner_stats': '📊 आँकड़े',
    'owner_grant': '➕ प्रीमियम दें', 'owner_revoke': '🗑️ प्रीमियम हटाएँ', 'owner_grant_check': '🔎 Grant जाँचें',
    'owner_broadcast': '📢 प्रसारण', 'owner_gifts': '🎁 लंबित अनुरोध', 'owner_payments': '🧾 भुगतान समीक्षा',
    'cancel': '❌ रद्द करें', 'home': '🏠 होम', 'retry_failed': '🔁 असफल फिर चलाएँ',
    'history_clear': '🗑️ इतिहास हटाएँ', 'logout': '✅ हाँ, लॉगआउट',
    'removebot': '✅ हाँ, बॉट हटाएँ', 'reset': '✅ हाँ, रीसेट', 'delete_data': '✅ हाँ, डेटा हटाएँ',
}

HI_PROMPTS = {
    'destination': ('📍 डेस्टिनेशन', 'संख्यात्मक चैट ID भेजें; चाहें तो उसके बाद /टॉपिक ID लिखें। उदाहरण: -1001234567890/12'),
    'rename': ('🏷️ नाम टैग', 'फ़ाइल के नाम के लिए टैग भेजें।'),
    'caption': ('💬 कैप्शन', 'कैप्शन का टेक्स्ट भेजें।'),
    'prefix': ('⬆️ प्रीफ़िक्स', 'हर संदेश से पहले जोड़ने वाला टेक्स्ट भेजें।'),
    'suffix': ('⬇️ सफ़िक्स', 'हर संदेश के बाद जोड़ने वाला टेक्स्ट भेजें।'),
    'thumbnail': ('🖼️ थंबनेल', 'थंबनेल के लिए फोटो भेजें।'),
    'deletewords': ('🗑️ शब्द हटाएँ', 'हटाने वाले शब्द स्पेस से अलग करके भेजें।'),
    'replacewords': ('🔄 शब्द बदलें', 'नियम old=new फ़ॉर्मैट में भेजें।'),
    'addbot': ('🤖 बॉट जोड़ें', 'टोकन इस निजी चैट में केवल भरोसेमंद ऑपरेटर को भेजें; इससे आपके बॉट पर नियंत्रण मिलता है। टोकन एन्क्रिप्ट करके सेव होगा। संदेश हटाने का प्रयास होगा, गारंटी नहीं। बदलने पर लाइव मिरर बंद होगा। केवल फ़ॉर्मैट जाँचा जाएगा, टेलीग्राम वैधता नहीं। उजागर टोकन BotFather से रद्द करें।'),
    'owner_grant': ('➕ प्रीमियम दें', 'यूज़र ID और दिनों की संख्या भेजें, जैसे 123456789 30। सक्रिय प्लान बढ़ाया जाएगा।'),
    'owner_revoke': ('🗑️ प्रीमियम हटाएँ', 'संख्यात्मक यूज़र ID भेजें।'),
    'owner_grant_check': ('🔎 Grant जाँचें', 'Grant के बाद मिला 24-अक्षर का संदर्भ भेजें। यह केवल history जाँचता है, प्रीमियम नहीं देता।'),
    'owner_broadcast': ('📢 प्रसारण', 'सक्रिय प्रीमियम यूज़र्स को भेजने वाला टेक्स्ट भेजें। संवेदनशील जानकारी न भेजें।'),
    'mirror': ('🪞 लाइव मिरर', 'पहले अकाउंट, कस्टम बॉट और डेस्टिनेशन जोड़ें। ओनर के अलावा सक्रिय प्रीमियम चाहिए। स्रोत का @username या संख्यात्मक चैट ID भेजें; स्रोत और डेस्टिनेशन अलग हों। केवल ऑनलाइन रहते समय नए संदेश प्रोसेस होते हैं; छूटे संदेश नहीं मिलते।'),
    'video': ('🎥 वीडियो', 'समर्थित वीडियो लिंक भेजें।'),
    'audio': ('🎵 ऑडियो', 'ऑडियो के लिए समर्थित वीडियो लिंक भेजें।'),
}


HI_SETTING_LABELS = {
    'destination': 'डेस्टिनेशन', 'rename': 'नाम टैग', 'caption': 'कैप्शन',
    'prefix': 'प्रीफ़िक्स', 'suffix': 'सफ़िक्स', 'filter': 'मीडिया फ़िल्टर',
    'original_caption': 'मूल कैप्शन की पसंद', 'deletewords': 'हटाए जाने वाले शब्द',
    'replacewords': 'शब्द बदलने के नियम',
}


def status_label(status, language):
    value = str(status if status is not None else 'unknown')
    if language == 'hi':
        return {'running': 'चल रहा है', 'paused': 'रुका हुआ', 'interrupted': 'बाधित',
                'cancelling': 'रुक रहा है', 'cancelled': 'रद्द', 'completed': 'पूरा',
                'unknown': 'अज्ञात'}.get(value, value)
    return value


def button_label(action, original, language):
    if language != 'hi':
        return original
    if original.startswith('❌'):
        return '❌ रद्द करें'
    if action.startswith('confirm_yes:'):
        kind = action.split(':', 2)[1]
        return {'logout': '✅ हाँ, लॉगआउट', 'removebot': '✅ हाँ, बॉट हटाएँ', 'reset': '✅ हाँ, रीसेट'}.get(kind, original)
    if action.startswith('delete_data_yes:'):
        return '✅ हाँ, डेटा हटाएँ'
    if action.startswith('history_page:'):
        return '⬅️ पिछला' if original.startswith('⬅️') else 'अगला ➡️'
    if action.startswith('history_clear_yes:'):
        return '✅ इतिहास हटाएँ'
    if action.startswith('setting_clear_yes:'):
        return '✅ सेटिंग हटाएँ'
    if action.startswith('setting_clear:'):
        return HI_SETTING_LABELS.get(action.partition(':')[2], original)
    if action.startswith('ri:s:'):
        return '⏭️ आइटम छोड़ें'
    if action.startswith('ri:r:'):
        return '🔁 आइटम फिर भेजें'
    if action.startswith('rf:'):
        return '✅ फिर कोशिश करें'
    return HI_BUTTONS.get(action, original)

# Translation is presentation-only: queue and delivery code keep their stable
# English result strings for state decisions and backwards compatibility.
HI_MESSAGES = {
    'This bot does not provide free servies, get subscription from OWNER': 'फ़्री सेवा उपलब्ध नहीं है। सदस्यता के लिए ओनर से संपर्क करें।',
    'Doing some checks hold on...': '⏳ जाँच चल रही है, कृपया प्रतीक्षा करें...',
    'An input is already in progress. Cancel it before starting another.': '⚠️ एक इनपुट पहले से चल रहा है। नया शुरू करने से पहले उसे रद्द करें।',
    'An unfinished task exists. Open Task Status to resume or stop it.': '⚠️ एक टास्क अधूरा है। उसे जारी रखने या रोकने के लिए टास्क स्थिति खोलें।',
    '⚠️ Add your custom bot from the Account menu first.': '⚠️ पहले अकाउंट मेनू से अपना कस्टम बॉट जोड़ें।',
    'Send the message link.': 'संदेश का लिंक भेजें।',
    'Send the first message link. Normal batch scans consecutive message IDs across the whole chat, including other topics. Use Topic Batch to restrict a forum topic.': 'पहले संदेश का लिंक भेजें। सामान्य बैच पूरी चैट में लगातार message ID स्कैन करता है, जिनमें दूसरे टॉपिक भी शामिल हो सकते हैं। एक फ़ोरम टॉपिक तक सीमित रखने के लिए टॉपिक बैच चुनें।',
    'Send a link containing BOTH topic and post IDs, such as https://t.me/c/123456/12/99. Set a destination forum topic first. You will choose how many consecutive message IDs to scan.': 'ऐसा लिंक भेजें जिसमें टॉपिक और पोस्ट दोनों की ID हों, जैसे https://t.me/c/123456/12/99। पहले डेस्टिनेशन फ़ोरम टॉपिक सेट करें। फिर आप स्कैन करने वाली लगातार message ID की संख्या चुनेंगे।',
    '⚠️ Invalid link. Send a Telegram message link or cancel.': '⚠️ लिंक मान्य नहीं है। टेलीग्राम संदेश का लिंक भेजें या रद्द करें।',
    '⚠️ Send a link containing both topic and post IDs, for example https://t.me/c/123456/12/99.': '⚠️ टॉपिक और पोस्ट दोनों की ID वाला लिंक भेजें, जैसे https://t.me/c/123456/12/99।',
    '⚠️ This link includes a topic. Cancel and choose Topic Batch to avoid scanning other topics.': '⚠️ इस लिंक में टॉपिक है। दूसरे टॉपिक स्कैन होने से बचाने के लिए रद्द करें और टॉपिक बैच चुनें।',
    'How many consecutive message IDs should be scanned? Other topics are skipped; this is not the number of posts copied.': 'कितनी लगातार message ID स्कैन करनी हैं? दूसरे टॉपिक छोड़े जाएँगे; यह कॉपी होने वाली पोस्ट की संख्या नहीं है।',
    'How many messages?': 'कितने संदेश स्कैन करने हैं?',
    'Invalid link format.': '⚠️ लिंक का फ़ॉर्मैट मान्य नहीं है।',
    '📥 Preparing the post...': '📥 पोस्ट तैयार हो रही है...',
    '⚠️ Finish your existing login or task first.': '⚠️ पहले चल रहा लॉगिन या टास्क पूरा करें।',
    '⚠️ Connect your custom bot and account first.': '⚠️ पहले अपना कस्टम बॉट और अकाउंट जोड़ें।',
    '🛑 Post processing was cancelled before delivery.': '🛑 डिलीवरी से पहले पोस्ट की प्रोसेसिंग रद्द हो गई।',
    'Message not found': 'संदेश नहीं मिला।',
    'Sent directly.': 'सीधे भेज दिया गया।',
    'Sent.': 'भेज दिया गया।',
    'Done.': 'पूरा हुआ।',
    'Done (Large file).': 'बड़ी फ़ाइल पूरी हुई।',
    'Skipped: media filter.': 'मीडिया फ़िल्टर के कारण छोड़ दिया गया।',
    'Failed: direct delivery result unknown.': 'सीधी डिलीवरी का परिणाम अज्ञात है। दोबारा कोशिश से पहले डेस्टिनेशन जाँचें।',
    'Failed.': 'असफल। दोबारा कोशिश से पहले डेस्टिनेशन जाँचें।',
    '⚠️ Could not process this post. Delivery may be uncertain; check your destination before retrying.': '⚠️ पोस्ट प्रोसेस नहीं हो पाई। डिलीवरी अनिश्चित हो सकती है; फिर से कोशिश करने से पहले डेस्टिनेशन जाँचें।',
    'Enter a valid batch size.': '⚠️ बैच के लिए सही संख्या (अंग्रेज़ी अंक) भेजें।',
    '📚 Preparing batch...': '📚 बैच तैयार हो रहा है...',
    '⚠️ Could not start or update the batch. Check Task Status before trying again.': '⚠️ बैच शुरू या अपडेट नहीं हो पाया। दोबारा कोशिश से पहले टास्क स्थिति जाँचें।',
    'Finish or cancel login first.': 'पहले लॉगिन पूरा करें या रद्द करें।',
    'An unfinished batch exists. Open Task Status to resume or stop it.': 'एक बैच अधूरा है। उसे जारी रखने या रोकने के लिए टास्क स्थिति खोलें।',
    'Invalid message range.': 'संदेश ID की सीमा मान्य नहीं है।',
    'Connect your custom bot and account first.': 'पहले कस्टम बॉट और अकाउंट जोड़ें।',
    'Batch input was cancelled.': 'बैच इनपुट रद्द हो गया।',
    'Invalid source topic ID.': 'स्रोत टॉपिक ID मान्य नहीं है।',
    'Set a destination forum chat ID and topic ID in Settings first.': 'पहले सेटिंग्स में डेस्टिनेशन फ़ोरम चैट ID और टॉपिक ID सेट करें।',
    'Invalid destination topic ID.': 'डेस्टिनेशन टॉपिक ID मान्य नहीं है।',
    'Source and destination must be accessible forum groups.': 'स्रोत और डेस्टिनेशन ऐसे फ़ोरम ग्रुप होने चाहिए जिन्हें खोला जा सके।',
    'Source and destination groups must be different.': 'स्रोत और डेस्टिनेशन ग्रुप अलग होने चाहिए।',
    'Could not verify the selected topics.': 'चुने हुए टॉपिक सत्यापित नहीं हो पाए।',
    'Destination topic is closed.': 'डेस्टिनेशन टॉपिक बंद है।',
    'Cannot verify forum access or topics. Check that your account and custom bot can access them.': 'फ़ोरम की पहुँच या टॉपिक सत्यापित नहीं हुए। जाँचें कि अकाउंट और कस्टम बॉट को पहुँच है।',
    'The first post could not be verified in that source topic. Check the link and permissions.': 'पहली पोस्ट उस स्रोत टॉपिक में सत्यापित नहीं हो पाई। लिंक और अनुमतियाँ जाँचें।',
    'Batch started.': 'बैच शुरू हो गया।',
    'A batch is already running.': 'एक बैच पहले से चल रहा है।',
    'No saved batch to resume.': 'जारी करने के लिए कोई सेव बैच नहीं है।',
    'Your current plan does not allow this batch size.': 'आपका वर्तमान प्लान इस बैच आकार की अनुमति नहीं देता।',
    'This choice is outdated. Open Task Status and review the current item.': 'यह विकल्प पुराना है। टास्क स्थिति खोलकर वर्तमान आइटम जाँचें।',
    'Batch changed; open Task Status and try again.': 'बैच बदल गया। टास्क स्थिति खोलकर फिर कोशिश करें।',
    'Saved batch resumed.': 'सेव बैच फिर शुरू हो गया।',
    'Stop or finish the active batch first.': 'पहले चल रहा बैच रोकें या पूरा करें।',
    'No failed items to retry.': 'दोबारा कोशिश के लिए कोई असफल आइटम नहीं है।',
    'Resume the remaining items before retrying failures.': 'असफल आइटम दोबारा चलाने से पहले बाकी आइटम जारी करें।',
    'This confirmation is outdated. Review the current failed items first.': 'यह पुष्टि पुरानी है। पहले वर्तमान असफल आइटम जाँचें।',
    'Your current plan does not allow this retry size.': 'आपका वर्तमान प्लान इतनी दोबारा कोशिशों की अनुमति नहीं देता।',
    'Retry started.': 'दोबारा कोशिश शुरू हो गई।',
    'Batch resumed.': 'बैच फिर शुरू हो गया।',
    'Batch is already running or stopping.': 'बैच पहले से चल रहा है या रुक रहा है।',
    'Pause requested; the current file may finish first.': 'रोकने का अनुरोध भेजा गया; मौजूदा फ़ाइल पहले पूरी हो सकती है।',
    'No active batch.': 'कोई बैच चालू नहीं है।',
    'Stop requested; the current file may finish first.': 'बंद करने का अनुरोध भेजा गया; मौजूदा फ़ाइल पहले पूरी हो सकती है।',
    'No active or interrupted batch.': 'कोई चालू या बाधित बैच नहीं है।',
    'This choice is invalid or outdated. Open Task Status again.': 'यह विकल्प अमान्य या पुराना है। टास्क स्थिति दोबारा खोलें।',
    'Task checkpoint is invalid. Contact support.': 'टास्क चेकपॉइंट अमान्य है। सहायता से संपर्क करें।',
    'Task changed or the item is invalid. Open Task Status again.': 'टास्क बदल गया या आइटम अमान्य है। टास्क स्थिति दोबारा खोलें।',
    'This confirmation is invalid. Open Task Status again.': 'यह पुष्टि अमान्य है। टास्क स्थिति दोबारा खोलें।',
    'This old button has expired. Open Task Status and review the current batch.': 'यह पुराना बटन समाप्त हो गया है। टास्क स्थिति खोलकर वर्तमान बैच जाँचें।',
    'Input cancelled. An operation already in progress may have finished; check Task or Account Status.': 'इनपुट रद्द हुआ। पहले से चल रही कोई कार्रवाई पूरी हो चुकी हो सकती है; टास्क या अकाउंट स्थिति जाँचें।',
    'Please cancel the login flow first.': 'पहले लॉगिन प्रक्रिया रद्द करें।',
    '⚠️ Stop your active task before logging in.': '⚠️ लॉगिन से पहले चालू टास्क रोकें।',
    '⚠️ Cancel the current input first.': '⚠️ पहले मौजूदा इनपुट रद्द करें।',
    '⚠️ A login is already in progress. Finish it or tap Cancel.': '⚠️ लॉगिन पहले से चल रहा है। इसे पूरा करें या रद्द करें दबाएँ।',
    'A mirror is already running.': 'मिरर पहले से चालू है।',
    'Previous mirror needs a confirmed stop. Tap Stop Mirror and try again.': 'पिछले मिरर के रुकने की पुष्टि चाहिए। मिरर रोकें दबाकर फिर कोशिश करें।',
    'An active premium plan is required.': 'सक्रिय प्रीमियम प्लान चाहिए।',
    'Connect your own account and custom bot first.': 'पहले अपना अकाउंट और कस्टम बॉट जोड़ें।',
    'Set a destination in Settings first.': 'पहले सेटिंग्स में डेस्टिनेशन सेट करें।',
    'Destination is invalid.': 'डेस्टिनेशन मान्य नहीं है।',
    'Could not connect your account and custom bot.': 'अकाउंट और कस्टम बॉट से कनेक्ट नहीं हो पाया।',
    'Cannot access source. Check membership and the ID or username.': 'स्रोत उपलब्ध नहीं है। सदस्यता और ID या यूज़रनेम जाँचें।',
    'Source and destination must be different.': 'स्रोत और डेस्टिनेशन अलग होने चाहिए।',
    'Could not start mirror.': 'मिरर शुरू नहीं हो पाया।',
    'Mirror start failed. Tap Stop Mirror to confirm cleanup.': 'मिरर शुरू नहीं हुआ। सफ़ाई की पुष्टि के लिए मिरर रोकें दबाएँ।',
    'Mirror started. Only new messages received while online will be processed.': 'मिरर शुरू हुआ। केवल ऑनलाइन रहते समय नए संदेश प्रोसेस होंगे।',
    '⚠️ Could not stop the existing mirror. Try again later.': '⚠️ चालू मिरर बंद नहीं हो पाया। बाद में फिर कोशिश करें।',
    'Stop your active task or finish login before logging out.': 'लॉगआउट से पहले चालू टास्क रोकें या लॉगिन पूरा करें।',
    'Could not check stored session. Try again later.': 'सेव सेशन की जाँच नहीं हो पाई। बाद में फिर कोशिश करें।',
    'No stored session found.': 'कोई सेव सेशन नहीं मिला।',
    'Telegram session revoked and removed.': 'टेलीग्राम सेशन रद्द और स्टोरेज से हटाया गया।',
    'Stored session removed, but Telegram revocation failed. Revoke it in Telegram Settings → Devices.': 'सेव सेशन हटा दिया गया, लेकिन टेलीग्राम पर रद्द करना असफल रहा। Telegram Settings → Devices में इसे रद्द करें।',
    '⚠️ Could not confirm removal from storage. Check Telegram Settings → Devices and contact the operator.': '⚠️ स्टोरेज से हटने की पुष्टि नहीं हुई। Telegram Settings → Devices जाँचें और ऑपरेटर से संपर्क करें।',
    'Stop your active task or finish login before removing your bot.': 'बॉट हटाने से पहले चालू टास्क रोकें या लॉगिन पूरा करें।',
    'No stored custom bot token found. Revoke any unwanted token with BotFather.': 'कोई सेव कस्टम बॉट टोकन नहीं मिला। अनचाहा टोकन BotFather से रद्द करें।',
    'Custom bot removed. Revoke its token with BotFather if necessary.': 'कस्टम बॉट हटा दिया गया। ज़रूरत हो तो BotFather से उसका टोकन रद्द करें।',
    '⚠️ Could not confirm token and local session removal. Check storage, retry, and revoke the token with BotFather.': '⚠️ टोकन और लोकल सेशन हटने की पुष्टि नहीं हुई। स्टोरेज जाँचें, फिर कोशिश करें और BotFather से टोकन रद्द करें।',
    '⚠️ Could not confirm token removal from storage. Retry and revoke the token with BotFather.': '⚠️ स्टोरेज से टोकन हटने की पुष्टि नहीं हुई। फिर कोशिश करें और BotFather से टोकन रद्द करें।',
    'Confirmation expired. Open History and try again.': 'पुष्टि की अवधि समाप्त हो गई। इतिहास खोलकर फिर कोशिश करें।',
    'Deletion could not be confirmed; check history before trying again.': 'हटाने की पुष्टि नहीं हुई; दोबारा कोशिश से पहले इतिहास जाँचें।',
    'Confirmation expired. Select the setting again.': 'पुष्टि की अवधि समाप्त हो गई। सेटिंग दोबारा चुनें।',
    'Setting changed or was already cleared. Review it and try again.': 'सेटिंग बदल गई है या पहले ही हट चुकी है। जाँचकर फिर कोशिश करें।',
    'Setting cleared.': 'सेटिंग हटा दी गई।',
    'Could not confirm the change. Check your settings before retrying.': 'बदलाव की पुष्टि नहीं हुई। दोबारा कोशिश से पहले सेटिंग्स जाँचें।',
    '🛑 Batch stopped.': '🛑 बैच रुक गया।',
    '✅ Batch completed.': '✅ बैच पूरा हुआ।',
    '⚠️ Batch interrupted. Open Task Status to review and resume.': '⚠️ बैच बाधित हो गया। जाँचने और फिर शुरू करने के लिए टास्क स्थिति खोलें।',
}


def translate(text, language):
    if language != 'hi':
        return text
    if text in HI_MESSAGES:
        return HI_MESSAGES[text]
    # Numeric limits are generated by trusted server configuration.
    import re
    match = re.fullmatch(r'Maximum limit is (\d+)\.', text)
    if match:
        return f'अधिकतम सीमा {match.group(1)} है।'
    match = re.fullmatch(r'Maximum batch size is (\d+)\.', text)
    if match:
        return f'अधिकतम बैच आकार {match.group(1)} है।'
    match = re.fullmatch(r'Deleted (\d+) archived records\. Your current task is unchanged\.', text)
    if match:
        return f"{match.group(1)} आर्काइव रिकॉर्ड हटाए गए। आपका मौजूदा टास्क नहीं बदला।"
    match = re.fullmatch(r'Your current plan allows at most (\d+) items\.', text)
    if match:
        return f'आपके मौजूदा प्लान में अधिकतम {match.group(1)} आइटम हैं।'
    return text
