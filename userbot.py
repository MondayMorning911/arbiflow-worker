import os
import asyncio
import logging
import json
import sys
import signal

# Настройка логирования до импорта pyrogram
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("userbot.log", mode='a', encoding='utf-8')
    ]
)
logging.info(f"[UserBot] Starting on Python {sys.version}")
logging.info(f"[UserBot] Python executable: {sys.executable}")

try:
    from pyrogram import Client, filters
    from pyrogram.types import Message
    logging.info("[UserBot] Pyrogram successfully imported.")
except ImportError as e:
    logging.error(f"[UserBot] ❌ Pyrogram import failed: {e}")
    logging.error(f"[UserBot] Please run: {sys.executable} -m pip install pyrogram tgcrypto")
    sys.exit(1)
from common.unique_utils import unique_video_single, unique_video_batch, create_zip
from datetime import datetime
from common.sqlite_db import load_task
from common.sqlite_db import save_task
from common.sqlite_db import mark_ready  # добавь вверху userbot.py, если ещё не добавил
import ffmpeg
import sqlite3
from pyrogram.errors import PeerIdInvalid
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")

# 🔐 Настройки
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.path.join(BASE_DIR, "sessions", "userbot")
MAIN_BOT_ID = int(os.getenv("MAIN_BOT_ID", "0"))

def fix_session_database(session_path):
    """Исправляет ошибки структуры для старых сессий Pyrogram (number, test_mode, user_id, is_bot)"""
    full_path = session_path if session_path.endswith(".session") else f"{session_path}.session"
    if not os.path.exists(full_path):
        return
    try:
        conn = sqlite3.connect(full_path)
        cursor = conn.cursor()
        
        # 1. Исправляем таблицу version (колонку number)
        cursor.execute("PRAGMA table_info(version)")
        columns_version = [row[1] for row in cursor.fetchall()]
        if "version" in columns_version and "number" not in columns_version:
            logging.info(f"[Fix] Исправляю таблицу 'version' в {full_path}...")
            cursor.execute("ALTER TABLE version RENAME COLUMN version TO number")
            conn.commit()

        # 2. Исправляем таблицу sessions (добавляем все недостающие колонки Pyrogram 2.x)
        cursor.execute("PRAGMA table_info(sessions)")
        columns_sessions = [row[1] for row in cursor.fetchall()]
        
        missing_columns = []
        if "api_id" not in columns_sessions:
            missing_columns.append("api_id INTEGER DEFAULT 0")
        if "test_mode" not in columns_sessions:
            missing_columns.append("test_mode INTEGER DEFAULT 0")
        if "date" not in columns_sessions:
            missing_columns.append("date INTEGER DEFAULT 0")
        if "user_id" not in columns_sessions:
            missing_columns.append("user_id INTEGER DEFAULT 0")
        if "is_bot" not in columns_sessions:
            missing_columns.append("is_bot INTEGER DEFAULT 0")
            
        for col_def in missing_columns:
            col_name = col_def.split()[0]
            logging.info(f"[Fix] Добавляю колонку '{col_name}' в таблицу 'sessions'...")
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_def}")
            
        if missing_columns:
            conn.commit()
            
        # 3. Проверяем наличие таблицы peers (нужна для Pyrogram 2.x)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='peers'")
        if not cursor.fetchone():
            logging.info(f"[Fix] Создаю таблицу 'peers'...")
            cursor.execute("""
                CREATE TABLE peers (
                    id INTEGER PRIMARY KEY,
                    access_hash INTEGER,
                    type INTEGER,
                    username TEXT,
                    phone_number TEXT,
                    last_update_on INTEGER DEFAULT (CAST(STRFTIME('%s', 'now') AS INTEGER))
                )
            """)
            conn.commit()
            
        conn.close()
        logging.info("[Fix] Проверка структуры сессии завершена.")
    except Exception as e:
        logging.error(f"[Fix] Ошибка при проверке сессии: {e}")

# Исправляем сессию перед созданием клиента
fix_session_database(SESSION_NAME)
app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

semaphore = asyncio.Semaphore(3)  # максимум 3 задачи одновременно

# 🧾 Логирование
# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s") # Удалено, так как уже настроено в начале


pending_tasks = {}      # file_unique_id -> video Message
pending_meta = {}       # file_unique_id -> meta dict


async def download_video(app: Client, message: Message, user_id: int) -> str:
    media = message.video or message.document
    if not media:
        raise ValueError("Файл не является видео")

    file_name = media.file_name or f"{media.file_unique_id}.mp4"
    user_dir = os.path.join(DOWNLOAD_DIR, f"user_{user_id}")
    os.makedirs(user_dir, exist_ok=True)
    file_path = os.path.join(user_dir, file_name)

    logging.info(f"[UserBot] download_video: Starting download for user_id={user_id}, file_unique_id={media.file_unique_id} → {file_path}")
    
    async def progress(current, total):
        if total > 0:
            percent = current * 100 / total
            if int(percent) % 20 == 0: # Log every 20%
                logging.info(f"[UserBot] download_video: Download progress for {media.file_unique_id}: {percent:.1f}%")

    await app.download_media(message, file_name=file_path, progress=progress)
    logging.info(f"[UserBot] download_video: Download complete for {media.file_unique_id}")

    return file_path

async def process_video(app: Client, video_msg: Message, meta: dict):
    logging.info(f"[UserBot] process_video: Starting processing for meta: {meta}")
    try:
        mode = meta.get("mode", "single")
        count = int(meta.get("count", 1))
        user_id = int(meta.get("user_id"))

        logging.info(f"[UserBot] process_video: user_id={user_id}, mode={mode}, count={count}")
        try:
            await video_msg.reply("⏬ Скачивание...")
        except Exception as e:
            logging.warning(f"[UserBot] process_video: Could not reply to video_msg: {e}")

        # Отправляем сообщение пользователю о начале скачивания
        progress_msg = None
        try:
            progress_msg = await app.send_message(user_id, "⏬ UserBot скачивает ваше видео, пожалуйста, подождите...")
        except Exception:
            pass

        async def download_progress(current, total):
            if total > 0 and progress_msg:
                percent = current * 100 / total
                if int(percent) % 20 == 0:
                    try:
                        await progress_msg.edit_text(f"⏬ UserBot скачивает ваше видео... {percent:.1f}%")
                    except Exception:
                        pass

        # Переопределяем функцию progress внутри download_video, чтобы она обновляла сообщение
        media = video_msg.video or video_msg.document
        file_name = media.file_name or f"{media.file_unique_id}.mp4"
        user_dir = os.path.join(DOWNLOAD_DIR, f"user_{user_id}")
        os.makedirs(user_dir, exist_ok=True)
        video_path = os.path.join(user_dir, file_name)

        await app.download_media(video_msg, file_name=video_path, progress=download_progress)
        
        try:
            if progress_msg:
                await progress_msg.delete()
        except Exception:
            pass

        logging.info(f"[UserBot] process_video: Video downloaded to {video_path}")

        if mode == "single":
            await video_msg.reply("⚙️ Уникализация (1 файл)...")
            unique_path = os.path.splitext(video_path)[0] + "_unique.mp4"
            await unique_video_single(video_path, unique_path)

            await mark_ready(user_id=user_id, path=unique_path, mode="single")

        elif mode.startswith("ai_subs_") or mode == "split_screen" or mode == "ai_translate" or mode == "watermark":
            await video_msg.reply(f"✅ Видео скачано для {mode}. Передаю боту...")
            await mark_ready(user_id=user_id, path=video_path, mode=mode)

        elif mode == "batch":
            progress_msg = None
            try:
                user = await app.get_users(user_id)
                progress_msg = await app.send_message(
                    chat_id=user.id,
                    text=f"🤖 UniUni уникализирует (0 / {count})\n▱▱▱▱▱▱▱▱▱▱"
                )
            except PeerIdInvalid:
                logging.warning(f"[WARN] User {user_id} недоступен (PeerIdInvalid) — прогресс не будет отображаться.")
            except Exception as e:
                logging.warning(f"[WARN] Не удалось отправить прогресс сообщение юзеру {user_id}: {e}")

            batch_dir = os.path.join(os.path.dirname(video_path), "batch")
            last_progress = {"value": -1}

            async def on_progress(done: int, total: int):
                if progress_msg:
                    try:
                        bar_length = 10
                        filled = round(bar_length * done / total)
                        bar = "▰" * filled + "▱" * (bar_length - filled)
                        await progress_msg.edit_text(f"🤖 UniUni уникализирует ({done} / {total})\n{bar}")
                    except Exception as e:
                        logging.warning(f"[Прогресс] Не удалось обновить сообщение: {e}")

            async with semaphore:
                unique_paths = await unique_video_batch(
                    video_path, count, batch_dir, on_progress=on_progress
                )

            archive_path = os.path.join(os.path.dirname(video_path), f"batch_{datetime.now().timestamp()}.zip")
            await create_zip(unique_paths, archive_path)
            await mark_ready(user_id=user_id, path=archive_path, mode="batch")

            logging.info("🟢 Вошли в режим batch")
            if progress_msg:
                try:
                    await progress_msg.delete()
                except Exception as e:
                    logging.warning(f"[Прогресс] Не удалось удалить сообщение: {e}")

        else:
            await video_msg.reply("❌ Неподдерживаемый режим.")

    except Exception as e:
        logging.exception("❌ Ошибка при обработке видео:")
        await video_msg.reply(f"❌ Ошибка: {str(e)}")
        try:
            user_id = int(json.loads(video_msg.caption).get("user_id"))
            await app.send_message(user_id, f"❌ Ошибка скачивания: {str(e)}")
        except Exception:
            pass

@app.on_message(filters.all)
async def debug_handler(client, message: Message):
    sender_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else None)
    logging.info(f"[DEBUG] Received message from {sender_id}. Type: {message.media if message.media else 'text'}")
    if message.text:
        logging.info(f"[DEBUG] Text: {message.text[:100]}")
    if message.video or message.document:
        media = message.video or message.document
        logging.info(f"[DEBUG] Media: {media.file_unique_id}, file_name: {getattr(media, 'file_name', 'N/A')}")
    
    # We don't return here, so other handlers can still process it
    message.continue_propagation()

@app.on_message(filters.private & (filters.video | filters.document))
async def on_video(client, message: Message):
    # Проверяем отправителя
    sender_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else None)
    logging.info(f"[UserBot] on_video: Received video/doc from {sender_id}.")
    
    media = message.video or message.document
    if not media:
        logging.warning("[UserBot] on_video: Message has no video or document media.")
        return
        
    file_unique_id = media.file_unique_id
    logging.info(f"[UserBot] on_video: Processing video/doc with file_unique_id={file_unique_id}")
    
    meta = None
    if message.caption:
        try:
            meta = json.loads(message.caption)
            logging.info(f"[UserBot] on_video: Found meta in caption: {meta}")
        except Exception:
            pass
    
    if not meta:
        # Try to find meta in pending_meta by file_unique_id
        meta = pending_meta.pop(file_unique_id, None)
        if meta:
            logging.info(f"[UserBot] on_video: Using pending meta for file_unique_id={file_unique_id}: {meta}")
    
    if not meta:
        # If still no meta, maybe it's a forwarded message and meta is coming later
        logging.info(f"[UserBot] on_video: Video received but no meta yet. Storing in pending_tasks by file_unique_id={file_unique_id}")
        pending_tasks[file_unique_id] = message
        return

    logging.info(f"[UserBot] on_video: Calling process_video for file_unique_id={file_unique_id}")
    await process_video(client, message, meta)


@app.on_message(filters.private & filters.text)
async def on_meta(client, message: Message):
    # Проверяем отправителя
    sender_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else None)
    logging.info(f"[UserBot] Received text from {sender_id}: {message.text[:50]}...")
    
    if message.text.lower() == "ping":
        await message.reply("pong")
        return

    logging.info(f"[UserBot] Processing meta message: {message.text}")
    try:
        meta = json.loads(message.text)
    except json.JSONDecodeError:
        logging.warning(f"[UserBot] Ignored non-JSON text message: {message.text}")
        return
    except Exception as e:
        logging.exception(f"[UserBot] Error parsing text message: {e}")
        return
        
    try:
        action = meta.get("action")
        user_id = meta.get("user_id")
        file_unique_id = meta.get("file_unique_id")
        logging.info(f"[UserBot] Action: {action}, user_id: {user_id}, file_unique_id: {file_unique_id}")
        
        if action == "upload":
            file_path = meta.get("path")
            logging.info(f"[UserBot] Uploading large file: {file_path} for user_id={user_id}")
            
            if not file_path or not os.path.exists(file_path):
                logging.error(f"[UserBot] File not found: {file_path}")
                await message.reply(f"❌ Файл не найден: {file_path}")
                return
            
            await message.reply(f"⏳ Загружаю большой файл для {user_id}...")
            
            forward_meta = {
                "action": "forward",
                "user_id": user_id,
                "original_path": file_path
            }
            
            try:
                logging.info(f"[UserBot] Sending file to MainBot with meta: {forward_meta}")
                
                async def progress(current, total):
                    if total > 0:
                        logging.info(f"[UserBot] Upload progress: {current * 100 / total:.1f}%")

                if file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    await client.send_video(
                        chat_id=MAIN_BOT_ID,
                        video=file_path,
                        caption=json.dumps(forward_meta),
                        supports_streaming=True,
                        progress=progress
                    )
                else:
                    await client.send_document(
                        chat_id=MAIN_BOT_ID,
                        document=file_path,
                        caption=json.dumps(forward_meta),
                        progress=progress
                    )
                logging.info(f"[UserBot] Successfully sent file to MainBot.")
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                # Try to remove parent directory if it's empty
                parent_dir = os.path.dirname(file_path)
                if os.path.exists(parent_dir) and os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                    try:
                        os.rmdir(parent_dir)
                    except:
                        pass
                    
                await message.reply(f"✅ Файл отправлен главному боту для {user_id}")
            except Exception as e:
                logging.error(f"[UserBot] ❌ Ошибка отправки файла: {e}", exc_info=True)
                await message.reply(f"❌ Ошибка отправки файла: {e}")
            return

        if not file_unique_id:
            logging.warning(f"[UserBot] on_meta: Received meta without file_unique_id: {meta}")
            return

        logging.info(f"[UserBot] on_meta: Looking for video with file_unique_id={file_unique_id} in pending_tasks. Current keys: {list(pending_tasks.keys())}")
        video_msg = pending_tasks.pop(file_unique_id, None)
        if video_msg is None:
            logging.info(f"[UserBot] on_meta: Видео ещё не получено. Сохраняю мету по file_unique_id={file_unique_id}")
            pending_meta[file_unique_id] = meta
            return

        logging.info(f"[UserBot] on_meta: Found video in pending_tasks for file_unique_id={file_unique_id}. Calling process_video.")
        await process_video(client, video_msg, meta)

    except Exception as e:
        logging.exception("Ошибка при получении мета:")
        await message.reply(f"❌ Ошибка: {str(e)}")


def main():
    logging.info("🤖 UserBot слушает входящие видео от основного бота...")
    app.run()  # 🔥 ОБЯЗАТЕЛЕН ЗАПУСК!

if __name__ == "__main__":
    main()
