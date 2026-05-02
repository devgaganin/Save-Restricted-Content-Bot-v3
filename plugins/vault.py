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


def _short_text(text: str, max_len: int = 40) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _human_size(size: int) -> str:
    size = size or 0
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.2f} {units[idx]}"


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


async def _show_collection_page(message, collection, files, page=1, edit=False):
    per_page = 10
    total_files = len(files)
    total_pages = max(1, (total_files + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_files = files[start_idx:end_idx]

    lines = [
        f"📁 **{collection['name']}**",
        f"📄 {total_files} files (page {page}/{total_pages})",
        "-------------------------",
    ]
    for item in page_files:
        mime = (item.get("mime_type") or "").lower()
        if mime.startswith("video"):
            kind = "video"
        elif mime.startswith("image"):
            kind = "image"
        elif mime.startswith("audio"):
            kind = "audio"
        else:
            kind = "file"
        lines.append(f"• `{_short_text(item.get('file_name') or 'unnamed file')}`")
        lines.append(f"  └ {kind} | {_human_size(item.get('file_size') or 0)}")
    lines.append(f"🔑 `{collection['access_key']}`")
    text = "\n".join(lines)

    buttons = [
        [InlineKeyboardButton(f"⬇️ Send This Page ({len(page_files)})", callback_data=f"vault_send_{collection['access_key']}_{page}")]
    ]
    if total_files > per_page:
        buttons.append([InlineKeyboardButton(f"🚀 Send All ({total_files})", callback_data=f"vault_all_{collection['access_key']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"vault_page_{collection['access_key']}_{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"vault_page_{collection['access_key']}_{page+1}"))
    if nav:
        buttons.append(nav)

    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


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


@app.on_callback_query(filters.regex(r"^vault_(page|send|all)_"))
async def vault_callback_handler(_, callback):
    if not _is_owner(callback.from_user.id):
        await callback.answer("Private bot. Owner only.", show_alert=True)
        return

    parts = callback.data.split("_")
    action = parts[1]
    access_key = "_".join(parts[2:-1]) if action in {"page", "send"} else "_".join(parts[2:])
    page = int(parts[-1]) if action in {"page", "send"} else 1

    collection = await get_vault_collection_by_key(access_key)
    if not collection:
        await callback.answer("Collection not found.", show_alert=True)
        return
    files = await get_vault_collection_files(collection["_id"])

    if action == "page":
        await _show_collection_page(callback.message, collection, files, page=page, edit=True)
        await callback.answer()
        return

    if action == "send":
        start_idx = (page - 1) * 10
        end_idx = start_idx + 10
        sent = await _send_vault_files(callback.message.chat.id, files[start_idx:end_idx])
        await callback.answer(f"Sent {sent} files.")
        return

    sent = await _send_vault_files(callback.message.chat.id, files)
    await callback.answer(f"Sent {sent} files.")


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
        await _show_collection_page(message, collection, files, page=1, edit=False)
        return

    file_info = await get_vault_file_by_key(text)
    if file_info:
        sent = await _send_vault_files(message.chat.id, [file_info])
        await message.reply_text(f"Sent {sent}/1 file.")


async def run_vault_plugin():
    return
