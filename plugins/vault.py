from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared_client import app
from config import OWNER_ID
from utils.func import (
    get_user_vault_collections,
    get_vault_collection_by_key,
    get_vault_collection_files,
    get_vault_file_by_key,
)


def _is_owner(user_id: int) -> bool:
    return user_id in OWNER_ID


async def _send_vault_files(chat_id: int, files: list):
    sent = 0
    for file_info in files:
        try:
            await app.copy_message(chat_id, file_info["storage_chat_id"], file_info["storage_message_id"])
            sent += 1
            continue
        except Exception:
            pass

        try:
            await app.send_cached_media(chat_id, file_info["file_id"], caption=file_info.get("caption") or "")
            sent += 1
        except Exception:
            continue
    return sent


@app.on_message(filters.command("mycollections") & filters.private)
async def mycollections_handler(_, message):
    if not _is_owner(message.from_user.id):
        await message.reply_text("Private bot. Owner only.")
        return

    collections = await get_user_vault_collections(message.from_user.id)
    if not collections:
        await message.reply_text("No collections yet.")
        return

    lines = ["📁 **My Collections**\n"]
    for col in collections[:30]:
        files = await get_vault_collection_files(col["_id"])
        lines.append(f"• **{col['name']}**")
        lines.append(f"  🔑 `{col['access_key']}`")
        lines.append(f"  📄 {len(files)} files\n")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.text & filters.private)
async def vault_key_handler(_, message):
    if not _is_owner(message.from_user.id):
        return

    text = message.text.strip()
    if not text.startswith("file_store"):
        return

    collection = await get_vault_collection_by_key(text)
    if collection:
        files = await get_vault_collection_files(collection["_id"])
        if not files:
            await message.reply_text(f"📁 {collection['name']}\n\nNo files in this collection.")
            return

        summary = (
            f"📁 **{collection['name']}**\n"
            f"📄 {len(files)} files\n"
            f"🔑 `{collection['access_key']}`"
        )
        await message.reply_text(summary)
        sent = await _send_vault_files(message.chat.id, files)
        await message.reply_text(f"Sent {sent}/{len(files)} files from collection.")
        return

    file_info = await get_vault_file_by_key(text)
    if file_info:
        sent = await _send_vault_files(message.chat.id, [file_info])
        await message.reply_text(f"Sent {sent}/1 file.")


async def run_vault_plugin():
    return
