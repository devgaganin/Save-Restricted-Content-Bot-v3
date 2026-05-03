# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

from shared_client import app
from pyrogram import filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from config import LOG_GROUP, OWNER_ID, FORCE_SUB

async def subscribe(app, message):
    if not FORCE_SUB or FORCE_SUB == 0:
        return 0
    
    if not message.from_user:
        return 1
        
    try:
        user = await app.get_chat_member(FORCE_SUB, message.from_user.id)
        if str(user.status) == "ChatMemberStatus.BANNED":
            await message.reply_text("You are Banned. Contact -- Team SPY")
            return 1
    except UserNotParticipant:
        try:
            link = await app.export_chat_invite_link(FORCE_SUB)
            caption = f"Join our channel to use the bot"
            await message.reply_photo(photo="https://graph.org/file/d44f024a08ded19452152.jpg",caption=caption, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Now...", url=f"{link}")]]))
            return 1
        except Exception:
            await message.reply_text("Please join the required channel to use this bot.")
            return 1
    except Exception as ggn:
        print(f"Error in subscribe check: {ggn}")
        return 0
    
    return 0
     
@app.on_message(filters.command("set"))
async def set(_, message):
    if not message.from_user or message.from_user.id not in OWNER_ID:
        await message.reply("You are not authorized to use this command.")
        return
     
    await app.set_bot_commands([
        BotCommand("start", "🚀 Start the bot"),
        BotCommand("batch", "🫠 Extract in bulk"),
        BotCommand("login", "🔑 Get into the bot"),
        BotCommand("setbot", "🧸 Add your bot for handling files"),
        BotCommand("logout", "🚪 Get out of the bot"),
        BotCommand("adl", "👻 Download audio from 30+ sites"),
        BotCommand("dl", "💀 Download videos from 30+ sites"),
        BotCommand("rembot", "🤨 Remove your custom bot"),
        BotCommand("settings", "⚙️ Personalize things"),
        BotCommand("help", "❓ If you're a noob, still!"),
        BotCommand("cancel", "🚫 Cancel login/batch/settings process"),
        BotCommand("stop", "🚫 Cancel batch process")
    ])
 
    await message.reply("✅ Commands configured successfully!")
 
 
 
 
help_pages = [
    (
        "📝 **Bot Commands Overview (1/2)**:\n\n"
        "1. **/login**\n"
        "> Log into the bot for private channel access\n\n"
        "2. **/logout**\n"
        "> Logout from the bot\n\n"
        "3. **/batch**\n"
        "> Bulk extraction for posts (Free for all users, limit: 1000)\n\n"
        "4. **/single**\n"
        "> Process a single message link\n\n"
        "5. **/dl link**\n"
        "> Download videos from 30+ sites\n\n"
        "6. **/adl link**\n"
        "> Download audio from 30+ sites\n\n"
        "7. **/setbot**\n"
        "> Add your custom bot for handling files\n\n"
        "8. **/rembot**\n"
        "> Remove your custom bot\n\n"
    ),
    (
        "📝 **Bot Commands Overview (2/2)**:\n\n"
        "9. **/session**\n"
        "> Generate Pyrogram V2 session\n\n"
        "10. **/settings**\n"
        "> Personalize bot settings:\n"
        "> • SETCHATID: Upload directly to channel/group/DM\n"
        "> • SETRENAME: Add custom rename tag\n"
        "> • CAPTION: Add custom caption\n"
        "> • REPLACEWORDS: Replace words in filenames\n"
        "> • RESET: Reset to default settings\n\n"
        "11. **/stats**\n"
        "> Get bot statistics\n\n"
        "12. **/cancel** or **/stop**\n"
        "> Cancel ongoing batch process\n\n"
        "13. **/help**\n"
        "> Show this help message\n\n"
        "**__Powered by Team SPY__**\n"
        "**All features are FREE!**"
    )
]
 
 
async def send_or_edit_help_page(_, message, page_number):
    if page_number < 0 or page_number >= len(help_pages):
        return
 
     
    prev_button = InlineKeyboardButton("◀️ Previous", callback_data=f"help_prev_{page_number}")
    next_button = InlineKeyboardButton("Next ▶️", callback_data=f"help_next_{page_number}")
 
     
    buttons = []
    if page_number > 0:
        buttons.append(prev_button)
    if page_number < len(help_pages) - 1:
        buttons.append(next_button)
 
     
    keyboard = InlineKeyboardMarkup([buttons])
 
     
    await message.delete()
 
     
    await message.reply(
        help_pages[page_number],
        reply_markup=keyboard
    )
 
 
@app.on_message(filters.command("help"))
async def help(client, message):
    join = await subscribe(client, message)
    if join == 1:
        return
     
    await send_or_edit_help_page(client, message, 0)
 
 
@app.on_callback_query(filters.regex(r"help_(prev|next)_(\d+)"))
async def on_help_navigation(client, callback_query):
    action, page_number = callback_query.data.split("_")[1], int(callback_query.data.split("_")[2])
 
    if action == "prev":
        page_number -= 1
    elif action == "next":
        page_number += 1

    await send_or_edit_help_page(client, callback_query.message, page_number)
     
    await callback_query.answer()

 
