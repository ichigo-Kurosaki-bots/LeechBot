import os
import uuid
import time
import shutil
import asyncio
import threading
import aria2p
import yt_dlp
from flask import Flask
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, UserIsBlocked, PeerIdInvalid

from config import Config
from database import (
    add_user, has_started_bot, total_users_count,
    add_fsub_channel, remove_fsub_channel, get_fsub_channels
)
from utils import (
    ACTIVE_TASKS, human_bytes, human_time, progress_bar,
    inspect_media_file, split_large_file
)

os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# --- 24/7 Health Check Server (Render / Koyeb / Heroku) ---
web_app = Flask(__name__)

@web_app.route("/")
def health_check():
    return {
        "status": "online",
        "max_limit": "4GB" if Config.SESSION_STRING else "2GB",
        "active_tasks": len(ACTIVE_TASKS)
    }, 200

def run_flask():
    web_app.run(host="0.0.0.0", port=Config.PORT)

# --- Aria2 RPC Client ---
aria2 = aria2p.API(
    aria2p.Client(host="http://localhost", port=6800, secret="")
)

# --- Pyrogram Bot & Optional 4GB User Session Client ---
app = Client(
    "LeechBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

user_client = None
if Config.SESSION_STRING and Config.LOG_CHANNEL:
    user_client = Client(
        "LeechUser4GB",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=Config.SESSION_STRING
    )

# --- Access & Pre-Flight Checks ---
def is_allowed_group(message: Message) -> bool:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    return message.chat.id in Config.ALLOWED_GROUPS

async def check_fsub(client: Client, message: Message) -> bool:
    channels = await get_fsub_channels()
    if not channels or message.from_user.id == Config.OWNER_ID:
        return True

    not_joined = []
    for ch_id in channels:
        try:
            await client.get_chat_member(ch_id, message.from_user.id)
        except UserNotParticipant:
            chat = await client.get_chat(ch_id)
            invite = chat.invite_link or f"https://t.me/{chat.username}"
            not_joined.append([InlineKeyboardButton(f"Join {chat.title}", url=invite)])
        except Exception:
            continue

    if not_joined:
        await message.reply_text(
            "⚠️ **Access Restricted**\nPlease join our required channels below first:",
            reply_markup=InlineKeyboardMarkup(not_joined)
        )
        return False
    return True

async def verify_user_pm_started(client: Client, message: Message) -> bool:
    user_id = message.from_user.id
    bot_info = await client.get_me()
    start_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚀 Start Bot in PM", url=f"https://t.me/{bot_info.username}?start=group")]]
    )

    if not await has_started_bot(user_id):
        await message.reply_text(
            f"👋 {message.from_user.mention}, **you haven't started me in PM yet!**\n\n"
            "Click the button below, press **Start**, and then run your leech command again so I can send your files privately.",
            reply_markup=start_btn
        )
        return False

    try:
        await client.send_chat_action(user_id, action="typing")
        return True
    except (UserIsBlocked, PeerIdInvalid):
        await message.reply_text(
            f"⚠️ {message.from_user.mention}, I cannot message you in PM (blocked or not started).\n"
            "Please unblock/start me in PM first, then try again.",
            reply_markup=start_btn
        )
        return False

async def preflight_checks(client: Client, message: Message) -> bool:
    if not is_allowed_group(message):
        await message.reply_text(
            "🚫 **Leeching is disabled in Private Chat.**\n"
            "Please use `/leech` or `/ytdl` inside our authorized group."
        )
        return False

    if not await verify_user_pm_started(client, message):
        return False

    if not await check_fsub(client, message):
        return False

    return True

# --- PM / Admin Commands ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await add_user(message.from_user.id, message.from_user.first_name)
    limit_label = "4GB" if user_client else "2GB (auto-split >2GB)"
    await message.reply_text(
        f"✅ **Bot Activated!** You can now use leech commands in the group.\n\n"
        f"• **Max File Support:** `{limit_label}`\n"
        f"• **Group Commands:** `/leech <url/magnet>` and `/ytdl <url>`\n"
        f"• **Delivery:** All leeched files will be sent directly here in your PM."
    )

@app.on_message(filters.command("stats") & filters.user(Config.OWNER_ID))
async def stats_cmd(client: Client, message: Message):
    total_users = await total_users_count()
    total, used, free = shutil.disk_usage(Config.DOWNLOAD_DIR)
    mode = "4GB Premium Session" if user_client else "2GB Standard Bot"
    await message.reply_text(
        f"📊 **System & Bot Statistics**\n\n"
        f"• **Upload Mode:** `{mode}`\n"
        f"• **Registered PM Users:** `{total_users}`\n"
        f"• **Active Tasks:** `{len(ACTIVE_TASKS)}`\n"
        f"• **Disk Free:** `{human_bytes(free)}` / `{human_bytes(total)}`"
    )

@app.on_message(filters.command("addfsub") & filters.user(Config.OWNER_ID))
async def add_fsub_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/addfsub -100xxxxxxxxxx`")
    ch_id = int(message.command[1])
    await add_fsub_channel(ch_id)
    await message.reply_text(f"✅ Added `{ch_id}` to Force-Subscribe list.")

@app.on_message(filters.command("removefsub") & filters.user(Config.OWNER_ID))
async def rem_fsub_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/removefsub -100xxxxxxxxxx`")
    ch_id = int(message.command[1])
    await remove_fsub_channel(ch_id)
    await message.reply_text(f"🗑️ Removed `{ch_id}` from Force-Subscribe list.")

@app.on_message(filters.command("fsublist") & filters.user(Config.OWNER_ID))
async def list_fsub_cmd(client: Client, message: Message):
    channels = await get_fsub_channels()
    text = "\n".join([f"• `{c}`" for c in channels]) if channels else "No FSub channels configured."
    await message.reply_text(f"📢 **Force-Subscribe Channels:**\n{text}")

@app.on_message(filters.command("status"))
async def status_cmd(client: Client, message: Message):
    if not ACTIVE_TASKS:
        return await message.reply_text("💤 No active tasks running.")
    lines = ["⚙️ **Active Tasks:**\n"]
    for gid, info in ACTIVE_TASKS.items():
        lines.append(f"• **ID:** `{gid}` | **User:** `{info['user']}` | **Stage:** `{info['stage']}`")
    await message.reply_text("\n".join(lines))

@app.on_message(filters.command("cancel"))
async def cancel_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/cancel <gid>`")
    gid = message.command[1].strip()
    if gid in ACTIVE_TASKS:
        task = ACTIVE_TASKS[gid]
        if message.from_user.id not in (task["user"], Config.OWNER_ID):
            return await message.reply_text("⚠️ You can only cancel your own tasks.")
        ACTIVE_TASKS.pop(gid, None)
        try:
            dl = aria2.get_download(gid)
            dl.remove(force=True, files=True)
        except Exception:
            pass
        await message.reply_text(f"🛑 Task `{gid}` cancelled.")
    else:
        await message.reply_text("❌ Invalid or already completed Task ID.")

# --- 4GB / 2GB Upload Engine -> User PM ---
async def upload_to_user_pm(
    client: Client,
    message: Message,
    status_msg: Message,
    target_path: str,
    gid: str
) -> bool:
    ACTIVE_TASKS[gid]["stage"] = "Uploading to PM"
    user_id = message.from_user.id
    files_to_upload = []

    if os.path.isdir(target_path):
        for root, _, files in os.walk(target_path):
            for f in sorted(files):
                files_to_upload.append(os.path.join(root, f))
    else:
        files_to_upload.append(target_path)

    for file_path in files_to_upload:
        if gid not in ACTIVE_TASKS:
            raise asyncio.CancelledError("Cancelled during upload.")

        fsize = os.path.getsize(file_path)
        if fsize > 4000 * 1024 * 1024:
            await status_msg.edit_text(f"❌ File (`{human_bytes(fsize)}`) exceeds Telegram's maximum 4GB limit.")
            return False

        if fsize > Config.MAX_UPLOAD_SIZE:
            await status_msg.edit_text(
                f"✂️ File exceeds 2GB (`{human_bytes(fsize)}`). Splitting into 7z volumes..."
            )
            parts = await asyncio.to_thread(split_large_file, file_path)
            os.remove(file_path)
        else:
            parts = [file_path]

        for part in parts:
            start_t = time.time()
            part_size = os.path.getsize(part)
            fname = os.path.basename(part)

            media = await asyncio.to_thread(inspect_media_file, part)

            caption_lines = [
                f"📁 **File:** `{fname}`",
                f"📦 **Size:** `{human_bytes(part_size)}`",
                f"🎵 **Audio:** `{media['audio_summary']}`",
            ]
            if media["duration"] > 0:
                caption_lines.append(f"⏱️ **Duration:** `{human_time(media['duration'])}`")
            caption = "\n".join(caption_lines)

            use_4gb_relay = (part_size > 1990 * 1024 * 1024) and (user_client is not None)
            uploader = user_client if use_4gb_relay else client
            dest_chat = Config.LOG_CHANNEL if use_4gb_relay else user_id
            thumb = media["thumb"]

            try:
                if media["kind"] == "video":
                    sent = await uploader.send_video(
                        chat_id=dest_chat,
                        video=part,
                        caption=caption,
                        duration=media["duration"],
                        width=media["width"],
                        height=media["height"],
                        thumb=thumb,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status_msg, start_t, f"⬆️ Uploading {fname}", gid)
                    )
                elif media["kind"] == "audio":
                    sent = await uploader.send_audio(
                        chat_id=dest_chat,
                        audio=part,
                        caption=caption,
                        duration=media["duration"],
                        thumb=thumb,
                        progress=progress_bar,
                        progress_args=(status_msg, start_t, f"⬆️ Uploading {fname}", gid)
                    )
                else:
                    sent = await uploader.send_document(
                        chat_id=dest_chat,
                        document=part,
                        caption=caption,
                        thumb=thumb,
                        progress=progress_bar,
                        progress_args=(status_msg, start_t, f"⬆️ Uploading {fname}", gid)
                    )

                if use_4gb_relay:
                    await client.copy_message(
                        chat_id=user_id,
                        from_chat_id=Config.LOG_CHANNEL,
                        message_id=sent.id
                    )
                elif Config.LOG_CHANNEL:
                    try:
                        await sent.copy(Config.LOG_CHANNEL)
                    except Exception:
                        pass

            except (UserIsBlocked, PeerIdInvalid):
                bot_info = await client.get_me()
                btn = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🚀 Start Bot in PM", url=f"https://t.me/{bot_info.username}?start=group")]]
                )
                await status_msg.edit_text(
                    f"⚠️ Could not deliver to {message.from_user.mention}'s PM.\n"
                    "Please start the bot in PM first and try again.",
                    reply_markup=btn
                )
                return False
            finally:
                if thumb and os.path.exists(thumb):
                    os.remove(thumb)
                if os.path.exists(part):
                    os.remove(part)

    return True

# --- /leech Command (Group Only -> Aria2 Direct + Magnet/Torrent) ---
@app.on_message(filters.command("leech"))
async def leech_cmd(client: Client, message: Message):
    if not await preflight_checks(client, message):
        return
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/leech <direct_url_or_magnet>`")

    url = message.command[1]
    task_dir = os.path.join(Config.DOWNLOAD_DIR, uuid.uuid4().hex[:8])
    os.makedirs(task_dir, exist_ok=True)

    status_msg = await message.reply_text("⏳ **Adding task to Aria2 engine...**")
    gid = None

    try:
        if url.startswith("magnet:"):
            download = aria2.add_magnet(url, options={"dir": task_dir})
        else:
            download = aria2.add_uris([url], options={"dir": task_dir})

        gid = download.gid
        ACTIVE_TASKS[gid] = {
            "user": message.from_user.id,
            "stage": "Downloading",
            "last_edit": 0
        }

        while True:
            if gid not in ACTIVE_TASKS:
                raise asyncio.CancelledError("Task cancelled.")
            download.update()

            if download.has_failed:
                raise RuntimeError(download.error_message or "Aria2 download failed.")

            if download.is_complete:
                if download.followed_by_ids:
                    new_gid = download.followed_by_ids[0]
                    ACTIVE_TASKS[new_gid] = ACTIVE_TASKS.pop(gid)
                    gid = new_gid
                    download = aria2.get_download(gid)
                else:
                    break

            bar_len = int(download.progress // 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            text = (
                f"⬇️ **Downloading:** `{download.name}`\n"
                f"`[{bar}]` **{download.progress:.1f}%**\n"
                f"**Done:** `{download.completed_length_string()}` / `{download.total_length_string()}`\n"
                f"**Speed:** `{download.download_speed_string()}` | **ETA:** `{download.eta_string()}`\n"
                f"**Cancel:** `/cancel {gid}`"
            )
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass
            await asyncio.sleep(4)

        delivered = await upload_to_user_pm(client, message, status_msg, task_dir, gid)
        await status_msg.delete()
        if delivered:
            bot_info = await client.get_me()
            btn = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📥 Open Bot PM", url=f"https://t.me/{bot_info.username}")]]
            )
            await message.reply_text(
                f"✅ Done, {message.from_user.mention}! Your file has been delivered to your **PM**.",
                reply_markup=btn
            )

    except asyncio.CancelledError:
        await status_msg.edit_text("🛑 **Task cancelled and cleaned up.**")
    except Exception as e:
        await status_msg.edit_text(f"❌ **Error:** `{e}`")
    finally:
        if gid:
            ACTIVE_TASKS.pop(gid, None)
        shutil.rmtree(task_dir, ignore_errors=True)

# --- /ytdl Command (Group Only -> yt-dlp Multi-Platform Downloader) ---
@app.on_message(filters.command("ytdl"))
async def ytdl_cmd(client: Client, message: Message):
    if not await preflight_checks(client, message):
        return
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/ytdl <video_url>`")

    url = message.command[1]
    gid = uuid.uuid4().hex[:8]
    task_dir = os.path.join(Config.DOWNLOAD_DIR, gid)
    os.makedirs(task_dir, exist_ok=True)

    ACTIVE_TASKS[gid] = {
        "user": message.from_user.id,
        "stage": "Extracting",
        "last_edit": 0
    }
    status_msg = await message.reply_text(
        f"🎬 **Extracting media via yt-dlp...**\n**Cancel:** `/cancel {gid}`"
    )

    def run_ytdlp():
        ydl_opts = {
            "outtmpl": f"{task_dir}/%(title).80s.%(ext)s",
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        await asyncio.to_thread(run_ytdlp)
        if gid not in ACTIVE_TASKS:
            raise asyncio.CancelledError("Task cancelled.")

        delivered = await upload_to_user_pm(client, message, status_msg, task_dir, gid)
        await status_msg.delete()
        if delivered:
            bot_info = await client.get_me()
            btn = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📥 Open Bot PM", url=f"https://t.me/{bot_info.username}")]]
            )
            await message.reply_text(
                f"✅ Done, {message.from_user.mention}! Your video has been delivered to your **PM**.",
                reply_markup=btn
            )

    except asyncio.CancelledError:
        await status_msg.edit_text("🛑 **yt-dlp task cancelled.**")
    except Exception as e:
        await status_msg.edit_text(f"❌ **yt-dlp Error:** `{e}`")
    finally:
        ACTIVE_TASKS.pop(gid, None)
        shutil.rmtree(task_dir, ignore_errors=True)

async def resolve_allowed_groups():
    for entry in Config.ALLOWED_GROUP_LINKS:
        try:
            chat = await app.get_chat(entry)
            if chat.id not in Config.ALLOWED_GROUPS:
                Config.ALLOWED_GROUPS.append(chat.id)
                print(f"✅ Resolved allowed group '{entry}' -> {chat.id}")
        except Exception as e:
            print(f"⚠️ Could not resolve group '{entry}': {e}")

async def start_services():
    threading.Thread(target=run_flask, daemon=True).start()
    await app.start()
    await resolve_allowed_groups()
    if user_client:
        await user_client.start()
        print("✅ 4GB User Session Client connected!")
    print("🚀 LeechBot is running 24/7...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(start_services())
