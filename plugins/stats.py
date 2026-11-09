# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

from datetime import timedelta, datetime
from shared_client import client as bot_client
from telethon import events
from utils.func import get_premium_details, is_private_chat, get_user_data
from config import OWNER_ID
import logging
logging.basicConfig(format=
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger('tgwithrohit')


@bot_client.on(events.NewMessage(pattern='/status'))
async def status_handler(event):
    if not await is_private_chat(event):
        await event.respond("This command can only be used in private chats for security reasons.")
        return

    """Handle /status command to check user session and bot status"""
    user_id = event.sender_id
    user_data = await get_user_data(user_id)

    session_active = False
    bot_active = False

    if user_data and "session_string" in user_data:
            session_active = True

    # Check if user has a custom bot
    if user_data and "bot_token" in user_data:
        bot_active = True

    # Add premium status check
    premium_status = "❌ Not a premium member"
    premium_details = await get_premium_details(user_id)
    if premium_details:
        # Convert to IST timezone
        expiry_utc = premium_details["subscription_end"]
        expiry_ist = expiry_utc + timedelta(hours=5, minutes=30)
        formatted_expiry = expiry_ist.strftime("%d-%b-%Y %I:%M:%S %p")
        premium_status = (f"✅ Premium until {formatted_expiry} (IST)" if expiry_utc > datetime.utcnow()
                          else f"⌛ Expired on {formatted_expiry} (IST)")

    await event.respond(
        "**Your current status:**\n\n"
        f"**Login Status:** {'✅ Active' if session_active else '❌ Inactive'}\n"
        f"**Premium:** {premium_status}"
    )

@bot_client.on(events.NewMessage(pattern=r'^/transfer(?:@\w+)?(?:\s|$)'))
async def transfer_premium_handler(event):
    if await is_private_chat(event):
        # The old two-write transfer deleted payment idempotency history and
        # could duplicate or lose access on a partial failure.
        await event.respond('⚠️ Premium transfer is unavailable. Please contact the bot owner.')


@bot_client.on(events.NewMessage(pattern=r'^/rem(?:@\w+)?(?:\s|$)'))
async def remove_premium_handler(event):
    if await is_private_chat(event) and event.sender_id in OWNER_ID:
        await event.respond('👑 Use the Owner Panel → Revoke button to manage premium.')
