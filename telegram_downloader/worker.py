import os
import asyncio
import time
from telethon.errors import FloodWaitError
from telethon import TelegramClient
from dotenv import load_dotenv
from .bot_pool import BotPool

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Use a separate session file for Telethon to avoid conflicting with Pyrogram's userbot.session
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_NAME = os.path.join(BASE_DIR, "sessions", "telethon_downloader")

DOWNLOADER_BOTS = [
    "@TTPapaBot",
    "@Gozilla_bot",
    "@SaveFromVkBot",
    "@Finesaverbot",
    "@MegaSaverBot",
    "@skachatt_youtube_bot"
]

QUALITY_PRIORITY = ["1080", "1080p", "Full HD", "FHD"]
FALLBACK_QUALITY = ["720", "720p"]

bot_pool = BotPool(DOWNLOADER_BOTS)

async def download_via_telegram(url: str, dest_path: str) -> str:
    """
    Returns path to downloaded video file
    Raises exception if all bots failed
    """
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("❌ [Telegram Downloader]: Client is not authorized. Please run a script to authorize the session first.", flush=True)
        await client.disconnect()
        raise Exception("Telegram client not authorized.")
    
    try:
        # Try bots in round-robin fashion. We'll try up to len(DOWNLOADER_BOTS) times.
        max_attempts = len(DOWNLOADER_BOTS)
        
        for attempt in range(max_attempts):
            bot_username = bot_pool.get_next_bot()
            print(f"🔄 [Telegram Downloader]: Trying bot {bot_username} (Attempt {attempt + 1}/{max_attempts})", flush=True)
            
            try:
                # Send the URL to the bot
                await client.send_message(bot_username, url)
                
                # Wait for the bot to reply with buttons
                start_time = time.time()
                buttons_message = None
                
                while time.time() - start_time < 60:
                    # Get the last few messages from the chat
                    messages = await client.get_messages(bot_username, limit=5)
                    for msg in messages:
                        if msg.out:
                            continue # Skip our own messages
                        if msg.buttons:
                            buttons_message = msg
                            break
                    
                    if buttons_message:
                        break
                    
                    await asyncio.sleep(2)
                
                if not buttons_message:
                    print(f"⚠️ [Telegram Downloader]: Bot {bot_username} did not send buttons within 60 seconds.", flush=True)
                    continue
                
                # Find the best quality button
                selected_button_text = None
                selected_i = None
                selected_j = None
                
                # 1. Try QUALITY_PRIORITY
                for i, row in enumerate(buttons_message.buttons):
                    for j, button in enumerate(row):
                        if any(q.lower() in button.text.lower() for q in QUALITY_PRIORITY):
                            selected_button_text = button.text
                            selected_i = i
                            selected_j = j
                            break
                    if selected_button_text:
                        break
                
                # 2. Try FALLBACK_QUALITY
                if not selected_button_text:
                    for i, row in enumerate(buttons_message.buttons):
                        for j, button in enumerate(row):
                            if any(q.lower() in button.text.lower() for q in FALLBACK_QUALITY):
                                selected_button_text = button.text
                                selected_i = i
                                selected_j = j
                                break
                        if selected_button_text:
                            break
                
                # 3. If still no button, just pick the first available one that looks like a download button
                if not selected_button_text:
                    for i, row in enumerate(buttons_message.buttons):
                        for j, button in enumerate(row):
                            selected_button_text = button.text
                            selected_i = i
                            selected_j = j
                            break
                        if selected_button_text:
                            break
                
                if not selected_button_text:
                    print(f"⚠️ [Telegram Downloader]: Could not find any buttons to click for {bot_username}.", flush=True)
                    continue
                    
                print(f"🖱️ [Telegram Downloader]: Clicking button '{selected_button_text}'", flush=True)
                try:
                    await buttons_message.click(text=selected_button_text)
                except Exception as e:
                    print(f"⚠️ [Telegram Downloader]: Failed to click button by text, trying by index: {e}", flush=True)
                    await buttons_message.click(selected_i, selected_j)
                
                # Wait for the video message
                print(f"⏳ [Telegram Downloader]: Waiting for video from {bot_username}...", flush=True)
                video_start_time = time.time()
                video_message = None
                
                while time.time() - video_start_time < 120:
                    messages = await client.get_messages(bot_username, limit=5)
                    for msg in messages:
                        if msg.out:
                            continue
                        if msg.video or msg.document:
                            # Check if it's a new message (after we clicked the button)
                            if msg.date > buttons_message.date:
                                video_message = msg
                                break
                    
                    if video_message:
                        break
                    
                    await asyncio.sleep(3)
                    
                if not video_message:
                    print(f"⚠️ [Telegram Downloader]: Bot {bot_username} did not send video within 120 seconds.", flush=True)
                    continue
                    
                print(f"📥 [Telegram Downloader]: Video received! Downloading to {dest_path}...", flush=True)
                
                # Download the media
                await client.download_media(video_message, file=dest_path)
                
                if os.path.exists(dest_path):
                    print(f"✅ [Telegram Downloader]: Download complete: {dest_path} ({os.path.getsize(dest_path) / 1024 / 1024:.2f} MB)", flush=True)
                    return dest_path
                else:
                    print(f"⚠️ [Telegram Downloader]: Download finished but file not found?", flush=True)
                    continue
                    
            except FloodWaitError as e:
                print(f"⚠️ [Telegram Downloader]: FloodWaitError: sleeping for {e.seconds} seconds.", flush=True)
                await asyncio.sleep(e.seconds)
                continue
            except Exception as e:
                print(f"⚠️ [Telegram Downloader]: Error with bot {bot_username}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                continue
                
        raise Exception("All Telegram bots failed to download the video.")
    finally:
        await client.disconnect()
