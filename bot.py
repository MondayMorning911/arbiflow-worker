import asyncio
import os
import sys
import uuid
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ContentType
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from common.unique_utils import (
    unique_video_single,
    get_random_filters,
    is_valid_mp4,
    hash_file,
    create_zip,
)
from pathlib import Path
import shutil  # Для удаления директорий и файлов
from typing import Union
from aiogram.types import Document, Video
import aiohttp
from aiogram.exceptions import TelegramBadRequest
from common.sqlite_db import init_db, save_task
import json
import time
import logging
from common.sqlite_db import save_task
import common.sqlite_db as sqlite_db  # добавь если не было
from common.unique_utils import handle_large_file_upload  # Импортируем функцию из unique_utils.py
import ffmpeg
from common.unique_utils import get_file_size_mb, upload_to_gofile
import requests
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
import shutil
from aiogram.types import CallbackQuery
from aiogram.filters import StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder
import random

async def animate_progress(msg: Message, stop_event: asyncio.Event):
    progress = 0
    while not stop_event.is_set() and progress < 90:
        progress += random.randint(15, 20)
        if progress > 90:
            progress = 90
            
        filled = int(10 * progress / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        
        text = (
            "⚙️ Обработка видео...\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"{bar} {progress}%\n"
            "⏳ Пожалуйста, подождите..."
        )
        try:
            await msg.edit_text(text)
        except Exception:
            pass
        
        if progress < 90:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=random.uniform(1.5, 2.5))
            except asyncio.TimeoutError:
                pass

async def send_file_safely(message: Message, file_path: str, caption: str = "✅ Готово!") -> bool:
    try:
        file_size = get_file_size_mb(file_path)
        if file_size > 49:
            await handle_large_file_upload(file_path, message.chat.id, message.bot)
            return False  # Delegated to userbot, do not delete locally yet
        else:
            if file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                width, height = None, None
                try:
                    def _get_info(path):
                        return ffmpeg.probe(path)
                    probe = await asyncio.to_thread(_get_info, file_path)
                    width, height = get_video_dimensions(probe)
                except Exception:
                    pass
                await message.answer_video(FSInputFile(file_path), caption=caption, width=width, height=height)
            else:
                await message.answer_document(FSInputFile(file_path), caption=caption)
            return True   # Sent successfully, safe to delete
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке файла. Попробуйте позже.\nПодробности: {e}")
        return True # Return True so it gets cleaned up on error

from new_modules.universal_loader import download_video_ytdlp
from new_modules.watermark_master import add_watermark
from new_modules.split_screen_generator import generate_split_screen
from new_modules.ai_object_remover import remove_object
from new_modules.runpod_client import process_heavy_task, upload_to_catbox

from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

VOICES = {
    "social": {
        "name": "📱 Соцсети",
        "voices": [
            {"id": "bqbHGIIO5oETYIqhWmfk", "name": "Александр", "desc": "Социальные сети, мужской", "demo_file_id": None},
            {"id": "xdADm9fX0hNasTe0AGW0", "name": "Алекс", "desc": "Социальные сети, мужской", "demo_file_id": None},
            {"id": "bg9LrEYQkRYwqkxA8VOy", "name": "Леонид", "desc": "Социальные сети, мужской", "demo_file_id": None},
            {"id": "ETBmMkYUh8i2exSl2h3P", "name": "Молли", "desc": "Социальные сети, женский", "demo_file_id": None},
            {"id": "FZGeNF7bE3syeQOynDKC", "name": "Виктория", "desc": "Социальные сети, женский", "demo_file_id": None},
        ]
    },
    "conversational": {
        "name": "🗣 Разговорный",
        "voices": [
            {"id": "rQOBu7YxCDxGiFdTm28w", "name": "Артем", "desc": "Разговорный, мужской", "demo_file_id": None},
            {"id": "foZmP0ldhGob3fHgegm1", "name": "Наталья", "desc": "Разговорный, женский", "demo_file_id": None},
            {"id": "qfvGliTkPdDybwni40JM", "name": "Артур", "desc": "Разговорный, мужской", "demo_file_id": None},
            {"id": "WxSABFURrEBEgQpoEAwt", "name": "Макс", "desc": "Разговорный, мужской", "demo_file_id": None},
            {"id": "aTTiK3YzK3dXETpuDE2h", "name": "Бен", "desc": "Разговорный, мужской", "demo_file_id": None},
        ]
    },
    "advertising": {
        "name": "📢 Реклама",
        "voices": [
            {"id": "McVZB9hVxVSk3Equu8EH", "name": "Андрия", "desc": "Рекламный, женский", "demo_file_id": None},
            {"id": "hU3rD0Yk7DoiYULTX1pD", "name": "Дмитрий", "desc": "Рекламный, уверенный", "demo_file_id": None},
            {"id": "Dnd9VXpAjEGXiRGBf1O6", "name": "Паркер", "desc": "Рекламный, мужской", "demo_file_id": None},
            {"id": "dXtC3XhB9GtPusIpNtQx", "name": "Егор", "desc": "Рекламный, мужской", "demo_file_id": None},
            {"id": "QttbagfgqUCm9K0VgUyT", "name": "Аида", "desc": "Рекламный, женский", "demo_file_id": None},
        ]
    }
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
BACKGROUNDS_DIR = os.path.join(BASE_DIR, "backgrounds")
os.makedirs(BACKGROUNDS_DIR, exist_ok=True)
API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
USERBOT_ID = int(os.getenv("USERBOT_ID", "0"))  # 🔁 Вставь ID своего userbot-аккаунта
TAGS_PER_PAGE = 6

class UniqueStates(StatesGroup):
    choosing_mode = State()
    waiting_file = State()
    waiting_count = State()

class DownloadStates(StatesGroup):
    waiting_url = State()

class WatermarkStates(StatesGroup):
    waiting_video = State()
    waiting_text = State()
    waiting_type = State()
    waiting_size = State()
    waiting_font = State()
    waiting_position = State()
    waiting_dynamic_type = State()

class SplitScreenStates(StatesGroup):
    waiting_user_video = State()

class VoiceStates(StatesGroup):
    choosing_category = State()
    choosing_voice = State()
    choosing_action = State()
    waiting_text = State()

class SubsStates(StatesGroup):
    waiting_position = State()
    waiting_video = State()

class TranslateStates(StatesGroup):
    waiting_language = State()
    waiting_video = State()

class UpscaleStates(StatesGroup):
    waiting_file = State()

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="☰ Главное меню")]],
        resize_keyboard=True,
        input_field_placeholder="Вставьте ссылку или файл..."
    )

def get_main_menu_text(user):
    username = f"@{user.username}" if user.username else user.first_name
    balance = 140  # Placeholder
    
    return (
        "🤖 <b>ArbitraFlow AI</b>\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"👤 Пользователь: {username}\n"
        f"💰 Баланс: {balance} кр.\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "Выберите нужный инструмент ниже:"
    )

def main_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Видео", callback_data="ignore")],
        [InlineKeyboardButton(text="🪄 Уникализатор", callback_data="menu_unique"), InlineKeyboardButton(text="✂️ Split-Screen", callback_data="menu_split")],
        [InlineKeyboardButton(text="💧 Вотермарки", callback_data="menu_watermark"), InlineKeyboardButton(text="📥 Скачать", callback_data="menu_download")],
        [InlineKeyboardButton(text="✨ Апскейл", callback_data="menu_upscale")],
        [InlineKeyboardButton(text="AI Голос и Текст", callback_data="ignore")],
        [InlineKeyboardButton(text="🎙 Озвучка", callback_data="menu_voice")],
        [InlineKeyboardButton(text="📝 Субтитры", callback_data="menu_subs"), InlineKeyboardButton(text="🌍 Перевод", callback_data="menu_translate")],
        [InlineKeyboardButton(text="Системные кнопки", callback_data="ignore")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"), InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")]
    ])

def unique_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⠀ 🎞 Одиночная ⠀", callback_data="unique_single"), InlineKeyboardButton(text="⠀ 📁 Массовая ⠀", callback_data="unique_mass")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_unique")]
    ])

def get_tool_preview_text(tool_id):
    previews = {
        "unique": {
            "title": "🪄 Уникализатор",
            "desc": "Делает ваше видео уникальным для алгоритмов TikTok, Instagram и YouTube (смена метаданных, невидимые фильтры, микро-изменения).",
            "price": "1 кр. / видео",
            "time": "~5-10 секунд"
        },
        "split": {
            "title": "✂️ Split-Screen",
            "desc": "Создает залипательное видео из двух частей (ваше видео сверху, фоновое снизу). Идеально для удержания внимания.",
            "price": "2 кр. / видео",
            "time": "~15-30 секунд"
        },
        "watermark": {
            "title": "💧 Вотермарки",
            "desc": "Наложение вашего водяного знака (текст или логотип) на видео для защиты авторских прав.",
            "price": "1 кр. / видео",
            "time": "~10-20 секунд"
        },
        "download": {
            "title": "📥 Скачать",
            "desc": "Скачивание видео без водяных знаков из TikTok, Instagram, YouTube, Pinterest и других соцсетей.",
            "price": "Бесплатно",
            "time": "~2-5 секунд"
        },
        "upscale": {
            "title": "✨ Апскейл видео и фото",
            "desc": "Улучшение качества (x2/x4) с помощью нейросети Real-ESRGAN.",
            "price": "5 кр. / фото, 20 кр. / видео",
            "time": "~1-5 минут"
        },
        "voice": {
            "title": "🎙 Озвучка",
            "desc": "Реалистичная AI-озвучка вашего текста профессиональными голосами.",
            "price": "10 кр. / мин.",
            "time": "~1-2 минуты"
        },
        "subs": {
            "title": "📝 Субтитры",
            "desc": "Автоматическая генерация динамичных субтитров для ваших видео (в стиле MrBeast/Hormozi).",
            "price": "15 кр. / мин.",
            "time": "~1-3 минуты"
        },
        "translate": {
            "title": "🌍 Перевод и Дубляж",
            "desc": "Полная замена голоса в видео на 50+ языков с сохранением оригинального тембра и смысла.",
            "price": "200 кр. / мин.",
            "time": "~2-3 минуты"
        }
    }
    
    info = previews.get(tool_id)
    if not info:
        return "Инструмент не найден."
        
    return (
        f"<b>{info['title']}</b>\n"
        f"{info['desc']}\n\n"
        f"💰 Стоимость: {info['price']}\n"
        f"⏱ Время: {info['time']}"
    )

def tool_preview_keyboard(tool_id, action_text="🎞 Загрузить видео"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=action_text, callback_data=f"action_{tool_id}")],
        [InlineKeyboardButton(text="🖼 Посмотреть пример", callback_data=f"example_{tool_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🤖 Добро пожаловать!", reply_markup=main_reply_keyboard())
    await message.answer(get_main_menu_text(message.from_user), reply_markup=main_inline_keyboard(), parse_mode="HTML")

@dp.message(F.text == "☰ Главное меню")
async def open_menu_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(get_main_menu_text(message.from_user), reply_markup=main_inline_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "menu_main")
async def open_menu_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(get_main_menu_text(call.from_user), reply_markup=main_inline_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("menu_"))
async def show_tool_preview(call: CallbackQuery, state: FSMContext):
    await state.clear()
    tool_id = call.data.replace("menu_", "")
    
    # Handle special cases where tool_id doesn't match exactly or needs different action text
    action_text = "🎞 Загрузить видео"
    if tool_id == "download":
        action_text = "🔗 Отправить ссылку"
    elif tool_id == "remove_ai":
        action_text = "🖼 Загрузить фото"
    elif tool_id == "voice":
        action_text = "📝 Ввести текст"
        
    preview_text = get_tool_preview_text(tool_id)
    if preview_text == "Инструмент не найден.":
        # Fallback for profile, help, etc.
        if tool_id == "profile":
            await show_profile(call)
            return
        elif tool_id == "help":
            await about_bot(call)
            return
        else:
            await call.answer("Функция в разработке", show_alert=True)
            return
            
    await call.message.edit_text(
        preview_text,
        reply_markup=tool_preview_keyboard(tool_id, action_text),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "menu_unique")
async def show_unique_preview(call: CallbackQuery):
    text = (
        "🪄 **Уникализатор видео**\n\n"
        "Создание уникальных копий видео для обхода алгоритмов соцсетей. "
        "Меняем метаданные, накладываем фильтры, меняем аудио и многое другое.\n\n"
        "💰 Стоимость: 10 кр / видео\n"
        "⏳ Время: 1-2 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_unique")],
        [InlineKeyboardButton(text="🎬 Пример", callback_data="example_unique")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "menu_split")
async def show_split_preview(call: CallbackQuery):
    text = (
        "✂️ **Split-Screen Generator**\n\n"
        "Разделение экрана на две части: ваше видео сверху, фоновое видео снизу. "
        "Идеально для TikTok и Reels.\n\n"
        "💰 Стоимость: 50 кр / видео\n"
        "⏳ Время: 2-3 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_split")],
        [InlineKeyboardButton(text="🎬 Пример", callback_data="example_split")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "menu_watermark")
async def show_watermark_preview(call: CallbackQuery):
    text = (
        "💧 **Вотермарки**\n\n"
        "Наложение текста или логотипа на видео. Поддержка динамических (движущихся) вотермарок.\n\n"
        "💰 Стоимость: 5 кр / видео\n"
        "⏳ Время: 1 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_watermark")],
        [InlineKeyboardButton(text="🎬 Пример", callback_data="example_watermark")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "menu_upscale")
async def show_upscale_preview(call: CallbackQuery):
    text = (
        "✨ **Апскейл (Улучшение качества)**\n\n"
        "Увеличение разрешения и улучшение качества фото и видео с помощью нейросетей.\n\n"
        "💰 Стоимость: 10 кр / фото, 50 кр / мин видео\n"
        "⏳ Время: 1-5 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_upscale")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "menu_download")
async def show_download_preview(call: CallbackQuery):
    text = (
        "📥 **Скачивание видео**\n\n"
        "Загрузка видео без водяных знаков из TikTok, Instagram, YouTube и других сервисов.\n\n"
        "💰 Стоимость: 2 кр / видео\n"
        "⏳ Время: 30 сек"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_download")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def generate_voiceover(text: str, voice_id: str) -> str:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as response:
            if response.status == 200:
                file_path = os.path.join(DOWNLOAD_DIR, f"voice_{uuid.uuid4()}.mp3")
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(await response.read())
                return file_path
            else:
                error_text = await response.text()
                raise Exception(f"ElevenLabs error: {error_text}")

@dp.callback_query(F.data == "menu_voice")
async def show_voice_preview(call: CallbackQuery):
    text = (
        "🎙 **AI Озвучка**\n\n"
        "Превращение текста в реалистичную речь. Мы используем лучшие нейросети для создания естественного звучания.\n\n"
        "💰 Стоимость: 10 кр / 1000 симв.\n"
        "⏳ Время: моментально"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Попробовать", callback_data="action_voice")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "action_voice")
async def voice_choose_category(call: CallbackQuery, state: FSMContext):
    await state.set_state(VoiceStates.choosing_category)
    text = "Выбери стиль голоса для своего креатива:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Соцсети", callback_data="voice_cat_social")],
        [InlineKeyboardButton(text="🗣 Разговорный", callback_data="voice_cat_conversational")],
        [InlineKeyboardButton(text="📢 Реклама", callback_data="voice_cat_advertising")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_voice")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("voice_cat_"))
async def voice_list_voices(call: CallbackQuery, state: FSMContext):
    category_id = call.data.replace("voice_cat_", "")
    category = VOICES.get(category_id)
    if not category:
        return
    
    await state.update_data(category_id=category_id)
    await state.set_state(VoiceStates.choosing_voice)
    
    text = f"Доступные голоса в категории '{category['name']}':"
    builder = InlineKeyboardBuilder()
    for voice in category["voices"]:
        builder.row(InlineKeyboardButton(text=voice["name"], callback_data=f"voice_select_{voice['id']}"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="action_voice"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("voice_select_"))
async def voice_action_menu(call: CallbackQuery, state: FSMContext):
    voice_id = call.data.replace("voice_select_", "")
    data = await state.get_data()
    category_id = data.get("category_id")
    category = VOICES.get(category_id)
    
    voice = next((v for v in category["voices"] if v["id"] == voice_id), None)
    if not voice:
        return
    
    await state.update_data(voice_id=voice_id, voice_name=voice["name"])
    await state.set_state(VoiceStates.choosing_action)
    
    text = (
        f"Голос: **{voice['name']}** ({voice['desc']})\n\n"
        "Что хочешь сделать?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Прослушать демо", callback_data=f"voice_demo_{voice_id}")],
        [InlineKeyboardButton(text="✅ Выбрать этот голос", callback_data="voice_confirm")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"voice_cat_{category_id}")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

import json

VOICES_DB_FILE = os.path.join(BASE_DIR, "voices_db.json")

def load_voices_db():
    if os.path.exists(VOICES_DB_FILE):
        try:
            with open(VOICES_DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_voices_db(db):
    with open(VOICES_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)

voices_db = load_voices_db()

@dp.message(F.audio | F.voice | F.document)
async def handle_voice_reg(message: Message):
    caption = message.caption or message.text
    if caption and caption.startswith("VOICE_REG:"):
        parts = caption.split(":")
        if len(parts) >= 3:
            category = parts[1].strip()
            name = parts[2].strip()
            
            voice_id = None
            for cat_key, cat_data in VOICES.items():
                for v in cat_data["voices"]:
                    if v["name"].lower() == name.lower():
                        voice_id = v["id"]
                        break
                if voice_id:
                    break
            
            if voice_id:
                file_id = None
                if message.audio:
                    file_id = message.audio.file_id
                elif message.voice:
                    file_id = message.voice.file_id
                elif message.document:
                    file_id = message.document.file_id
                
                if file_id:
                    voices_db[voice_id] = file_id
                    save_voices_db(voices_db)
                    await message.reply(f"✅ Демо для голоса '{name}' ({voice_id}) успешно сохранено!")
            else:
                await message.reply(f"❌ Голос с именем '{name}' не найден в словаре VOICES.")

@dp.callback_query(F.data.startswith("voice_demo_"))
async def voice_play_demo(call: CallbackQuery, state: FSMContext):
    voice_id = call.data.replace("voice_demo_", "")
    data = await state.get_data()
    category_id = data.get("category_id")
    category = VOICES.get(category_id)
    voice = next((v for v in category["voices"] if v["id"] == voice_id), None)
    
    demo_file_id = voices_db.get(voice_id) or (voice.get("demo_file_id") if voice else None)
    
    if demo_file_id:
        await call.message.answer_audio(demo_file_id)
        await call.answer()
    else:
        await call.answer("Демо этого голоса пока недоступно.", show_alert=True)

@dp.callback_query(F.data == "voice_confirm")
async def voice_ask_text(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    voice_name = data.get("voice_name")
    
    await state.set_state(VoiceStates.waiting_text)
    await call.message.edit_text(
        f"Выбран голос: **{voice_name}**\n\n"
        "Введите текст для озвучки (до 1000 символов):",
        parse_mode="Markdown"
    )

@dp.message(VoiceStates.waiting_text)
async def voice_generate(message: Message, state: FSMContext):
    text = message.text
    if not text:
        await message.answer("Пожалуйста, отправьте текст.")
        return
    
    if len(text) > 1000:
        await message.answer("Текст слишком длинный. Максимум 1000 символов.")
        return
    
    data = await state.get_data()
    voice_id = data.get("voice_id")
    
    status_msg = await message.answer("⏳ Генерирую озвучку...")
    
    try:
        file_path = await generate_voiceover(text, voice_id)
        await message.answer_audio(FSInputFile(file_path), caption="✅ Ваша озвучка готова!")
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logging.error(f"Voiceover error: {e}")
        await message.answer(f"❌ Произошла ошибка при генерации: {e}")
    finally:
        await status_msg.delete()
        await state.clear()

@dp.callback_query(F.data == "menu_subs")
async def show_subs_preview(call: CallbackQuery):
    text = (
        "📝 **AI Субтитры**\n\n"
        "Автоматическое распознавание речи и наложение стильных субтитров на видео.\n\n"
        "💰 Стоимость: 20 кр / мин\n"
        "⏳ Время: 2-3 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_subs")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "menu_translate")
async def show_translate_preview(call: CallbackQuery):
    text = (
        "🌍 **Перевод и Дубляж**\n\n"
        "Полная замена голоса в видео на 50+ языков с сохранением смысла.\n\n"
        "💰 Стоимость: 200 кр / мин\n"
        "⏳ Время: 5-10 мин"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="action_translate")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("example_"))
async def show_tool_example(call: CallbackQuery):
    await call.answer("Пример в разработке. Скоро добавим!", show_alert=True)

@dp.callback_query(F.data == "action_unique")
async def open_unique_menu(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎛 Выбери режим уникализации:", reply_markup=unique_inline_keyboard())

@dp.callback_query(F.data == "action_download")
async def open_download_menu(call: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Список поддерживаемых сайтов", callback_data="dl_supported_sites")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_download")]
    ])
    await call.message.edit_text(
        "📥 **Отправьте ссылку на видео.**\n\n"
        "Поддерживаются: YouTube, TikTok, Instagram, Twitter (X), VK, Twitch и сотни других сайтов!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(DownloadStates.waiting_url)

@dp.callback_query(F.data == "dl_supported_sites")
async def show_supported_sites(call: CallbackQuery):
    sites = """
🌐 **Популярные поддерживаемые сайты (более 1000+):**

1. YouTube (вкл. Shorts)
2. TikTok
3. Instagram (Reels, IGTV, Post)
4. Twitter / X
5. VK (ВКонтакте)
6. Twitch (Клипы, стримы)
7. Telegram
8. Reddit
9. Pinterest
10. Facebook
11. Vimeo
12. Dailymotion
13. Rumble
14. Bilibili
15. SoundCloud
16. Spotify (подкасты)
17. LinkedIn
18. Tumblr
19. Flickr
20. Imgur
21. 9GAG
22. Likee
23. Kwai
24. Snapchat
25. Discord
26. Patreon
27. OnlyFans (открытые)
28. Pornhub / Xvideos (и др. 18+)
29. Yandex Video / Music
30. RuTube

📥 *Отправьте ссылку на видео с любого из этих сайтов прямо сейчас!*
    """
    await call.message.answer(sites, parse_mode="Markdown")
    await call.answer()

@dp.message(DownloadStates.waiting_url)
async def handle_download_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Пожалуйста, отправьте корректную ссылку.")
        return
        
    progress_msg = await message.answer(
        "⚙️ Обработка видео...\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "[░░░░░░░░░░] 0%\n"
        "⏳ Пожалуйста, подождите..."
    )
    
    stop_event = asyncio.Event()
    progress_task = asyncio.create_task(animate_progress(progress_msg, stop_event))

    try:
        output_dir = os.path.join(DOWNLOAD_DIR, f"dl_{uuid.uuid4().hex[:8]}")
        video_path = await download_video_ytdlp(url, output_dir)
        
        stop_event.set()
        await progress_task
        try:
            await progress_msg.delete()
        except Exception:
            pass
        
        safe_to_delete = await send_file_safely(message, video_path, caption="✅ Видео скачано!")
        
        # Save file_id for potential unique process
        # Note: send_file_safely doesn't return the message, so we might need to handle this differently if file_id is needed.
        # But for now, let's just fix the crash.
        
        # Cleanup
        if safe_to_delete:
            if os.path.exists(video_path):
                os.remove(video_path)
            shutil.rmtree(output_dir, ignore_errors=True)
        
    except Exception as e:
        stop_event.set()
        await progress_task
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при скачивании. Попробуйте позже.\nПодробности: {e}")
        await state.clear()

@dp.callback_query(F.data.startswith("dl_"))
async def handle_download_actions(call: CallbackQuery, state: FSMContext):
    action = call.data
    
    if action == "dl_menu":
        await call.message.edit_text("🔙 Возврат в меню.")
        await state.clear()
        
    elif action == "dl_more":
        await call.message.edit_text("📥 Отправьте следующую ссылку на видео:")
        await state.set_state(DownloadStates.waiting_url)
        
    elif action in ["dl_unique_single", "dl_unique_batch"]:
        data = await state.get_data()
        file_id = data.get("downloaded_file_id")
        
        if not file_id:
            await call.message.edit_text("❌ Ошибка: файл не найден. Попробуйте загрузить заново.")
            await state.clear()
            return
            
        mode = "single" if action == "dl_unique_single" else "batch"
        await state.update_data(mode=mode, forwarded_file_id=file_id)
        
        if mode == "single":
            await call.message.edit_text("🎬 Одиночный режим выбран. Начинаю обработку...")
            # Simulate a message with the video to reuse the existing handler logic
            # But since we already have the file_id, we can just call the unique logic directly
            # To keep it simple, we can just send it to the userbot or process it
            
            # Let's just use the existing logic by mocking the state and calling the handler
            # Actually, it's easier to just trigger the UserBot or local process
            meta = {
                "user_id": call.from_user.id,
                "mode": mode,
                "count": 1
            }
            try:
                await bot.send_video(
                    chat_id=USERBOT_ID,
                    video=file_id,
                    caption=json.dumps(meta)
                )
                await call.message.answer("🧬 Генерирую уникальную версию (отправлено на обработку)...")
            except Exception as e:
                await call.message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {e}")
            await state.clear()
            
        elif mode == "batch":
            await call.message.edit_text("🔁 Массовый режим выбран.")
            await call.message.answer("Сколько уникализированных копий создать? (например: 10)")
            await state.set_state(UniqueStates.waiting_count)

@dp.callback_query(F.data == "action_upscale")
async def open_upscale_menu(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("✨ Отправьте фото или видео для улучшения качества (апскейла):")
    await state.set_state(UpscaleStates.waiting_file)

@dp.message(StateFilter(UpscaleStates.waiting_file), F.content_type.in_({ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_upscale_file(message: Message, state: FSMContext):
    file = None
    is_photo = False
    if message.photo:
        file = message.photo[-1]
        is_photo = True
    elif message.video:
        file = message.video
    elif message.document:
        file = message.document
        if file.mime_type and file.mime_type.startswith('image/'):
            is_photo = True
            
    if not file:
        await message.answer("❌ Пожалуйста, отправьте фото или видео.")
        return

    ext = ".jpg" if is_photo else ".mp4"
    if hasattr(file, 'file_name') and file.file_name:
        _, file_ext = os.path.splitext(file.file_name)
        if file_ext:
            ext = file_ext
        
    file_name = f"upscale_in_{uuid.uuid4()}{ext}"
    input_path = os.path.join(DOWNLOAD_DIR, file_name)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    try:
        await download_video(file, input_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "file_id" in str(e).lower():
            meta = {
                "user_id": message.from_user.id,
                "mode": "upscale",
                "file_unique_id": file.file_unique_id
            }
            try:
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.answer("⏳ Файл большой. Скачиваю через UserBot, это займет немного времени...")
            except Exception as send_e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {send_e}")
            await state.clear()
            return
        else:
            await message.answer(f"❌ Ошибка скачивания: {e}")
            await state.clear()
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {e}")
        await state.clear()
        return
    
    progress_msg = await message.answer("⚙️ Подготовка файла...")
    
    async def update_progress(percent, text_status):
        filled = int(10 * percent / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
        try:
            await progress_msg.edit_text(msg_text)
        except Exception:
            pass

    try:
        output_path = await process_heavy_task(
            file_path=input_path,
            task_name="upscale",
            progress_callback=update_progress,
            is_image=is_photo
        )
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        if is_photo:
            await message.answer_photo(FSInputFile(output_path), caption="✅ Апскейл завершен!")
            safe_to_delete = True
        else:
            safe_to_delete = await send_file_safely(message, output_path, caption="✅ Апскейл завершен!")
        
        if os.path.exists(input_path): os.remove(input_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при апскейле. Попробуйте позже.\nПодробности: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "action_watermark")
async def open_watermark_menu(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("💧 Отправьте видео, на которое нужно наложить вотермарку:")
    await state.set_state(WatermarkStates.waiting_video)

@dp.message(StateFilter(WatermarkStates.waiting_video), F.content_type.in_({ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_watermark_video(message: Message, state: FSMContext):
    file = message.video or message.document
    file_name = file.file_name or f"{uuid.uuid4()}.mp4"
    input_path = os.path.join(DOWNLOAD_DIR, file_name)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    try:
        await download_video(file, input_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "file_id" in str(e).lower():
            meta = {
                "user_id": message.from_user.id,
                "mode": "watermark",
                "file_unique_id": file.file_unique_id
            }
            try:
                # Send meta first
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                # Then forward the message
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.answer("⏳ Видео большое. Скачиваю через UserBot, это займет немного времени...")
            except Exception as send_e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {send_e}")
            await state.clear()
            return
        else:
            await message.answer(f"❌ Ошибка скачивания: {e}")
            await state.clear()
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {e}")
        await state.clear()
        return

    await state.update_data(video_path=input_path)
    await message.answer("📝 Отправьте текст для вотермарки:")
    await state.set_state(WatermarkStates.waiting_text)

@dp.message(WatermarkStates.waiting_text)
async def handle_watermark_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статическая", callback_data="wm_static")],
        [InlineKeyboardButton(text="Динамическая", callback_data="wm_dynamic")]
    ])
    await message.answer("Выберите тип вотермарки:", reply_markup=keyboard)
    await state.set_state(WatermarkStates.waiting_type)

@dp.callback_query(F.data.startswith("wm_"), StateFilter(WatermarkStates.waiting_type))
async def handle_watermark_type(call: CallbackQuery, state: FSMContext):
    is_dynamic = call.data == "wm_dynamic"
    await state.update_data(is_dynamic=is_dynamic)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Маленький", callback_data="wmsize_small")],
        [InlineKeyboardButton(text="Средний", callback_data="wmsize_medium")],
        [InlineKeyboardButton(text="Большой", callback_data="wmsize_large")]
    ])
    await call.message.edit_text("Выберите размер вотермарки:", reply_markup=keyboard)
    await state.set_state(WatermarkStates.waiting_size)

@dp.callback_query(F.data.startswith("wmsize_"), StateFilter(WatermarkStates.waiting_size))
async def handle_watermark_size(call: CallbackQuery, state: FSMContext):
    size = call.data.split("_")[1]
    await state.update_data(size=size)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Roboto (Классический)", callback_data="wmfont_roboto")],
        [InlineKeyboardButton(text="Montserrat (Современный)", callback_data="wmfont_montserrat")],
        [InlineKeyboardButton(text="Oswald (Узкий, строгий)", callback_data="wmfont_oswald")],
        [InlineKeyboardButton(text="Caveat (Рукописный)", callback_data="wmfont_caveat")]
    ])
    await call.message.edit_text("Выберите шрифт для вотермарки:", reply_markup=keyboard)
    await state.set_state(WatermarkStates.waiting_font)

@dp.callback_query(F.data.startswith("wmfont_"), StateFilter(WatermarkStates.waiting_font))
async def handle_watermark_font(call: CallbackQuery, state: FSMContext):
    font = call.data.replace("wmfont_", "")
    await state.update_data(font=font)
    
    data = await state.get_data()
    is_dynamic = data.get("is_dynamic", False)
    
    if is_dynamic:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Плавающая", callback_data="wmdyn_floating")],
            [InlineKeyboardButton(text="Прыгающая", callback_data="wmdyn_bouncing")],
            [InlineKeyboardButton(text="Бегущая строка", callback_data="wmdyn_scrolling")]
        ])
        await call.message.edit_text("Выберите тип анимации:", reply_markup=keyboard)
        await state.set_state(WatermarkStates.waiting_dynamic_type)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Сверху слева", callback_data="wmpos_top_left"), InlineKeyboardButton(text="Сверху справа", callback_data="wmpos_top_right")],
            [InlineKeyboardButton(text="По центру", callback_data="wmpos_center")],
            [InlineKeyboardButton(text="Снизу слева", callback_data="wmpos_bottom_left"), InlineKeyboardButton(text="Снизу справа", callback_data="wmpos_bottom_right")]
        ])
        await call.message.edit_text("Выберите позицию вотермарки:", reply_markup=keyboard)
        await state.set_state(WatermarkStates.waiting_position)

@dp.callback_query(F.data.startswith("wmpos_"), StateFilter(WatermarkStates.waiting_position))
async def handle_watermark_position(call: CallbackQuery, state: FSMContext):
    position = call.data.replace("wmpos_", "")
    await state.update_data(position=position)
    await process_watermark(call, state)

@dp.callback_query(F.data.startswith("wmdyn_"), StateFilter(WatermarkStates.waiting_dynamic_type))
async def handle_watermark_dynamic_type(call: CallbackQuery, state: FSMContext):
    dynamic_type = call.data.replace("wmdyn_", "")
    await state.update_data(dynamic_type=dynamic_type)
    await process_watermark(call, state)

async def process_watermark(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    input_path = data["video_path"]
    text = data["text"]
    is_dynamic = data["is_dynamic"]
    size = data["size"]
    font = data.get("font", "roboto")
    position = data.get("position", "bottom_right")
    dynamic_type = data.get("dynamic_type", "floating")
    
    output_path = os.path.join(DOWNLOAD_DIR, f"wm_{uuid.uuid4().hex[:8]}.mp4")
    
    await call.message.edit_text(
        "⚙️ Обработка видео...\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "[░░░░░░░░░░] 0%\n"
        "⏳ Пожалуйста, подождите..."
    )
    
    stop_event = asyncio.Event()
    progress_task = asyncio.create_task(animate_progress(call.message, stop_event))

    try:
        await add_watermark(
            input_video=input_path, 
            output_video=output_path, 
            watermark_text=text, 
            is_dynamic=is_dynamic,
            size=size,
            position=position,
            dynamic_type=dynamic_type,
            font_name=font
        )
        
        stop_event.set()
        await progress_task
        try:
            await call.message.delete()
        except Exception:
            pass
        
        safe_to_delete = await send_file_safely(call.message, output_path, caption="✅ Готово!")
        
        if os.path.exists(input_path): os.remove(input_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        stop_event.set()
        await progress_task
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(f"❌ Ошибка при наложении вотермарки. Попробуйте позже.\nПодробности: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "action_split")
async def open_split_screen_menu(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("✂️ Отправьте ВАШЕ видео:")
    await state.set_state(SplitScreenStates.waiting_user_video)

@dp.message(StateFilter(SplitScreenStates.waiting_user_video), F.content_type.in_({ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_split_user_video(message: Message, state: FSMContext):
    file = message.video or message.document
    file_name = file.file_name or f"{uuid.uuid4()}.mp4"
    user_path = os.path.join(DOWNLOAD_DIR, f"user_{file_name}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    try:
        await download_video(file, user_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "file_id" in str(e).lower():
            meta = {
                "user_id": message.from_user.id,
                "mode": "split_screen",
                "file_unique_id": file.file_unique_id
            }
            try:
                # Send meta first
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                # Then forward the message
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.answer("⏳ Видео большое. Скачиваю через UserBot, это займет немного времени...")
            except Exception as send_e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {send_e}")
            await state.clear()
            return
        else:
            await message.answer(f"❌ Ошибка скачивания: {e}")
            await state.clear()
            return
    
    # Determine orientation
    is_vertical = True
    if message.video and message.video.width and message.video.height:
        is_vertical = message.video.height >= message.video.width
    else:
        try:
            def _get_info(path):
                return ffmpeg.probe(path)
            probe = await asyncio.to_thread(_get_info, user_path)
            v_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            w = int(v_stream['width'])
            h = int(v_stream['height'])
            tags = v_stream.get('tags', {})
            rotation = str(tags.get('rotate', '0'))
            if rotation in ['90', '270', '-90', '-270']:
                w, h = h, w
            is_vertical = h >= w
        except Exception:
            pass # Default to vertical if probe fails

    target_folder = "vertical" if is_vertical else "horizontal"
    bg_folder_path = os.path.join(BACKGROUNDS_DIR, target_folder)
    
    bg_files = []
    if os.path.exists(bg_folder_path):
        bg_files = [f for f in os.listdir(bg_folder_path) if f.lower().endswith(('.mp4', '.mov', '.avi'))]
        
    # Fallback if specific folder is empty or doesn't exist
    if not bg_files:
        bg_folder_path = BACKGROUNDS_DIR
        bg_files = [f for f in os.listdir(BACKGROUNDS_DIR) if f.lower().endswith(('.mp4', '.mov', '.avi'))]
        
    bg_filename = random.choice(bg_files) if bg_files else None
    bg_path = os.path.join(bg_folder_path, bg_filename) if bg_filename else None
    
    if not bg_path:
        await message.answer("ℹ️ Фоновые видео не найдены. Видео будет растянуто на весь экран (1080x1920).")
    
    output_path = os.path.join(DOWNLOAD_DIR, f"split_{uuid.uuid4().hex[:8]}.mp4")
    
    progress_msg = await message.answer(
        "⚙️ Обработка видео...\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "[░░░░░░░░░░] 0%\n"
        "⏳ Пожалуйста, подождите..."
    )
    
    async def update_progress(percent, status_text="Обработка видео..."):
        filled = int(10 * percent / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        text = (
            f"⚙️ {status_text}\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"{bar} {percent}%\n"
            "⏳ Пожалуйста, подождите..."
        )
        try:
            await progress_msg.edit_text(text)
        except Exception:
            pass

    try:
        # Upload files to Catbox
        await update_progress(5, "Загрузка файлов...")
        user_url = await upload_to_catbox(user_path)
        bg_url = await upload_to_catbox(bg_path)
        
        # Process via RunPod
        output_path = await process_heavy_task(
            file_path=user_path,
            task_name="split_screen",
            progress_callback=lambda p, m: update_progress(p, m),
            bg_url=bg_url,
            is_vertical=is_vertical
        )
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
        
        safe_to_delete = await send_file_safely(message, output_path, caption="✅ Готово!")
        
        if os.path.exists(user_path): os.remove(user_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ {e}")
        if os.path.exists(user_path): os.remove(user_path)
        if os.path.exists(output_path): os.remove(output_path)
    finally:
        await state.clear()

@dp.callback_query(F.data == "menu_profile")
async def show_profile(call: CallbackQuery):
    user_id = call.from_user.id
    # Placeholder for actual registration date and balance
    reg_date = "14.03.2026"
    balance = 140
    
    text = (
        "👤 Личный кабинет\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🆔 Ваш ID: <code>{user_id}</code>\n"
        f"📅 Регистрация: {reg_date}\n"
        f"💰 Баланс: {balance} кредитов\n"
        "💎 Тариф: Arbitrager\n"
        "∟ Доступно AI-удалений: 12\n"
        "∟ Лимит пачек: 100 видео/день\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub"), InlineKeyboardButton(text="⭐️ Пополнить баланс", callback_data="add_balance")],
        [InlineKeyboardButton(text="🔗 Реферальная программа", callback_data="ref_program"), InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data == "buy_sub")
async def placeholder_buy_sub(call: CallbackQuery):
    await call.answer("Функция в разработке", show_alert=True)

@dp.callback_query(F.data == "add_balance")
async def placeholder_add_balance(call: CallbackQuery):
    await call.answer("Функция в разработке", show_alert=True)

@dp.callback_query(F.data == "ref_program")
async def placeholder_ref_program(call: CallbackQuery):
    await call.answer("Функция в разработке", show_alert=True)

@dp.callback_query(F.data == "ignore")
async def handle_ignore(call: CallbackQuery):
    await call.answer()

@dp.callback_query(F.data == "action_voice")
async def open_voice_menu(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎙 Отправьте текст для озвучки:")
    await state.set_state(VoiceStates.waiting_text)

@dp.message(StateFilter(VoiceStates.waiting_text), F.text)
async def handle_voice_text(message: Message, state: FSMContext):
    text = message.text
    progress_msg = await message.answer("⚙️ Отправка задачи на GPU...")
    
    async def update_progress(percent, text_status):
        filled = int(10 * percent / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
        try:
            await progress_msg.edit_text(msg_text)
        except Exception:
            pass

    try:
        # For text, we don't need to upload a file to Catbox, but process_heavy_task expects a file_path.
        # We can create a dummy file or modify process_heavy_task to handle text.
        # Let's create a temporary text file.
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w") as f:
            f.write(text)
            temp_path = f.name
            
        output_path = await process_heavy_task(
            file_path=temp_path,
            task_name="ai_voice",
            progress_callback=update_progress,
            voice_text=text
        )
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        safe_to_delete = await send_file_safely(message, output_path, caption="✅ Озвучка готова!")
        
        if os.path.exists(temp_path): os.remove(temp_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при озвучке. Попробуйте позже.\nПодробности: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "action_subs")
async def open_subs_menu(call: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Снизу (Reels/TikTok)", callback_data="subs_pos_bottom")],
        [InlineKeyboardButton(text="⏺ По центру (Сплит-видео)", callback_data="subs_pos_middle")]
    ])
    await call.message.edit_text("📍 Выберите расположение субтитров:", reply_markup=keyboard)
    await state.set_state(SubsStates.waiting_position)

@dp.callback_query(StateFilter(SubsStates.waiting_position), F.data.startswith("subs_pos_"))
async def subs_position_chosen(call: CallbackQuery, state: FSMContext):
    position = call.data.split("_")[-1]
    await state.update_data(subs_position=position)
    await call.message.edit_text("📝 Отправьте видео для наложения субтитров:")
    await state.set_state(SubsStates.waiting_video)

@dp.message(StateFilter(SubsStates.waiting_video), F.content_type.in_({ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_subs_video(message: Message, state: FSMContext):
    data = await state.get_data()
    position = data.get("subs_position", "bottom")
    
    file = message.video or message.document
    file_name = file.file_name or f"{uuid.uuid4()}.mp4"
    input_path = os.path.join(DOWNLOAD_DIR, f"subs_{file_name}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    try:
        # Use download_video instead of bot.download for consistent error handling
        await download_video(file, input_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "file_id" in str(e).lower():
            meta = {
                "user_id": message.from_user.id,
                "mode": f"ai_subs_{position}",
                "file_unique_id": file.file_unique_id
            }
            try:
                # Send meta first
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                # Then forward the message
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.answer("⏳ Видео большое. Скачиваю через UserBot, это займет немного времени...")
            except Exception as send_e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {send_e}")
            await state.clear()
            return
        else:
            await message.answer(f"❌ Ошибка скачивания: {e}")
            await state.clear()
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {e}")
        await state.clear()
        return
            
    progress_msg = await message.answer("⚙️ Подготовка видео...")
    
    async def update_progress(percent, text_status):
        filled = int(10 * percent / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
        try:
            await progress_msg.edit_text(msg_text)
        except Exception:
            pass

    try:
        output_path = await process_heavy_task(
            file_path=input_path,
            task_name="ai_subs",
            progress_callback=update_progress,
            position=position
        )
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        safe_to_delete = await send_file_safely(message, output_path, caption="✅ Субтитры добавлены!")
        
        if os.path.exists(input_path): os.remove(input_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при добавлении субтитров. Попробуйте позже.\nПодробности: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "action_translate")
async def open_translate_menu(call: CallbackQuery, state: FSMContext):
    await state.set_state(TranslateStates.waiting_language)
    text = "🌍 Выберите язык, на который нужно перевести видео:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="trans_lang_ru"), InlineKeyboardButton(text="🇬🇧 English", callback_data="trans_lang_en")],
        [InlineKeyboardButton(text="🇪🇸 Spanish", callback_data="trans_lang_es"), InlineKeyboardButton(text="🇧🇷 Portuguese", callback_data="trans_lang_pt")],
        [InlineKeyboardButton(text="🇫🇷 French", callback_data="trans_lang_fr"), InlineKeyboardButton(text="🇩🇪 German", callback_data="trans_lang_de")],
        [InlineKeyboardButton(text="🇹🇷 Turkish", callback_data="trans_lang_tr"), InlineKeyboardButton(text="🇦🇪 Arabic", callback_data="trans_lang_ar")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_translate")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("trans_lang_"))
async def translate_language_selected(call: CallbackQuery, state: FSMContext):
    lang_code = call.data.replace("trans_lang_", "")
    await state.update_data(target_lang=lang_code)
    await state.set_state(TranslateStates.waiting_video)
    await call.message.edit_text("🎥 Отправьте видео для перевода и дубляжа:")

async def dub_video_elevenlabs(file_path: str, target_lang: str, progress_callback=None) -> str:
    url = "https://api.elevenlabs.io/v1/dubbing"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    data = aiohttp.FormData()
    data.add_field('file', open(file_path, 'rb'), filename=os.path.basename(file_path))
    data.add_field('target_lang', target_lang)
    data.add_field('mode', 'automatic')
    data.add_field('num_speakers', '0')
    data.add_field('watermark', 'false')
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"ElevenLabs API error: {error_text}")
            result = await response.json()
            dubbing_id = result.get("dubbing_id")
            
        status_url = f"https://api.elevenlabs.io/v1/dubbing/{dubbing_id}"
        progress = 10
        while True:
            await asyncio.sleep(5)
            async with session.get(status_url, headers=headers) as status_response:
                if status_response.status != 200:
                    continue
                status_data = await status_response.json()
                status = status_data.get("status")
                if status == "dubbed":
                    break
                elif status == "failed":
                    raise Exception("ElevenLabs dubbing failed.")
                
                if progress_callback:
                    progress += random.randint(2, 5)
                    if progress > 95:
                        progress = 95
                    await progress_callback(progress, "Нейросеть переводит видео...")

        download_url = f"https://api.elevenlabs.io/v1/dubbing/{dubbing_id}/audio/{target_lang}"
        async with session.get(download_url, headers=headers) as download_response:
            if download_response.status != 200:
                error_text = await download_response.text()
                raise Exception(f"ElevenLabs download error: {error_text}")
            
            output_path = os.path.join(DOWNLOAD_DIR, f"dubbed_{uuid.uuid4()}.mp4")
            with open(output_path, "wb") as f:
                while True:
                    chunk = await download_response.content.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            return output_path

@dp.message(StateFilter(TranslateStates.waiting_video), F.content_type.in_({ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_translate_video(message: Message, state: FSMContext):
    data = await state.get_data()
    target_lang = data.get("target_lang", "en")
    
    file = message.video or message.document
    file_name = file.file_name or f"{uuid.uuid4()}.mp4"
    input_path = os.path.join(DOWNLOAD_DIR, f"trans_{file_name}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    try:
        await download_video(file, input_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "file_id" in str(e).lower():
            meta = {
                "user_id": message.from_user.id,
                "mode": f"ai_translate_{target_lang}",
                "file_unique_id": file.file_unique_id
            }
            try:
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.answer("⏳ Видео большое. Скачиваю через UserBot, это займет немного времени...")
            except Exception as send_e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {send_e}")
            await state.clear()
            return
        else:
            await message.answer(f"❌ Ошибка скачивания: {e}")
            await state.clear()
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {e}")
        await state.clear()
        return
    
    progress_msg = await message.answer("⚙️ Подготовка видео...")
    
    async def update_progress(percent, text_status):
        filled = int(10 * percent / 100)
        bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
        msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
        try:
            await progress_msg.edit_text(msg_text)
        except Exception:
            pass

    try:
        await update_progress(5, "Загрузка видео в ElevenLabs...")
        output_path = await dub_video_elevenlabs(
            file_path=input_path,
            target_lang=target_lang,
            progress_callback=update_progress
        )
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        safe_to_delete = await send_file_safely(message, output_path, caption="✅ Перевод и дубляж завершены!")
        
        if os.path.exists(input_path): os.remove(input_path)
        if safe_to_delete and os.path.exists(output_path): os.remove(output_path)
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при переводе видео. Попробуйте позже.\nПодробности: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "unique_single")
async def handle_unique_single(call: CallbackQuery, state: FSMContext):
    await state.update_data(mode="single")
    await state.set_state(UniqueStates.waiting_file)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_unique")]
    ])
    await call.message.edit_text("🎞 Одиночный режим выбран. Пришли видео.", reply_markup=keyboard)

@dp.callback_query(F.data == "unique_mass")
async def handle_unique_mass(call: CallbackQuery, state: FSMContext):
    await state.update_data(mode="batch")
    await state.set_state(UniqueStates.waiting_file)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_unique")]
    ])
    await call.message.edit_text("🔁 Массовый режим выбран. Пришли видео.", reply_markup=keyboard)

@dp.callback_query(F.data == "menu_help")
async def about_bot(call: CallbackQuery):
    text = (
        "<b>👋 Привет, я — ArbitraFlow, твой мощный комбайн для работы с видео и трафиком!</b>\n\n"
        "Здесь собраны лучшие инструменты для арбитражников и контент-мейкеров. Ниже описаны все мои функции:\n\n"
        "<b>🛠 Уникализатор (Одиночная / Массовая)</b>\n"
        "<i>Что делает:</i> Меняет исходный код и визуал видео, делая его уникальным для алгоритмов (TikTok, Reels, Shorts).\n"
        "<i>Как помогает:</i> Обход теневых банов и фильтров на дубликаты. Массовый режим идеален для залива сетки аккаунтов.\n"
        "<i>Памятка:</i> Отправь видео и выбери режим. Для массового режима бот выдаст ZIP-архив.\n\n"
        "<b>📥 Скачать видео</b>\n"
        "<i>Что делает:</i> Скачивает видео по ссылке (TikTok, Instagram, YouTube и др.) без водяных знаков.\n"
        "<i>Как помогает:</i> Быстрое получение чистых исходников для креативов.\n"
        "<i>Памятка:</i> Просто отправь ссылку на видео.\n\n"
        "<b>✂️ Split-Screen (Двойной экран)</b>\n"
        "<i>Что делает:</i> Объединяет два видео на одном экране (например, залипательное видео снизу, суть сверху).\n"
        "<i>Как помогает:</i> Популярный формат для удержания внимания зрителя и повышения уникальности.\n"
        "<i>Памятка:</i> Просто отправь свое видео, а бот сам подберет и подгонит фоновое видео.\n\n"
        "<b>✨ Удалить AI (Удаление объектов)</b>\n"
        "<i>Что делает:</i> Нейросеть удаляет лишние объекты, водяные знаки или текст с изображения.\n"
        "<i>Как помогает:</i> Очищает чужие креативы для повторного использования.\n"
        "<i>Памятка:</i> Отправь картинку и маску (черно-белое изображение, где белым выделено то, что нужно удалить).\n\n"
        "<b>💧 Вотермарки</b>\n"
        "<i>Что делает:</i> Накладывает твой текст или логотип на видео (статика или динамика).\n"
        "<i>Как помогает:</i> Защищает контент от кражи и переливает трафик на твой ресурс.\n"
        "<i>Памятка:</i> Выбери тип (текст/картинка) и стиль (статика/динамика), затем отправь видео.\n\n"
        "💡 <i>Все файлы обрабатываются на мощных серверах и удаляются сразу после отправки — полная конфиденциальность.</i>\n\n"
        "<i>Поддержка: @SoDot1</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


    
async def download_video(file: Union[Document, Video], destination: str):
    file_info = await bot.get_file(file.file_id)
    file_path = file_info.file_path
    url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                with open(destination, "wb") as f:
                    f.write(await resp.read())



MAX_TG_SIZE_MB = 49

def get_file_size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)

def get_video_dimensions(probe):
    v_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    width = int(v_stream['width'])
    height = int(v_stream['height'])
    
    tags = v_stream.get('tags', {})
    rotation = str(tags.get('rotate', '0'))
    if rotation in ['90', '270', '-90', '-270']:
        width, height = height, width
        
    return width, height

async def polling_sqlite():
    while True:
        try:
            tasks = await sqlite_db.get_ready_tasks()

            for task_id, user_id, mode, path in tasks:
                try:
                    if not os.path.exists(path):
                        logging.warning(f"[MainBot] polling_sqlite: File not found: {path}. Deleting task {task_id}.")
                        await sqlite_db.delete_task(task_id)
                        continue

                    logging.info(f"[MainBot] polling_sqlite: Found ready task {task_id} for user {user_id}, mode {mode}")

                    if mode == "single":
                        if get_file_size_mb(path) <= MAX_TG_SIZE_MB:
                            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                                width, height = None, None
                                try:
                                    def _get_info(p):
                                        return ffmpeg.probe(p)
                                    probe = await asyncio.to_thread(_get_info, path)
                                    width, height = get_video_dimensions(probe)
                                except Exception:
                                    pass
                                await bot.send_video(user_id, FSInputFile(path), caption="✅ Готово!", width=width, height=height)
                            else:
                                await bot.send_document(user_id, FSInputFile(path), caption="✅ Готово!")
                            if os.path.exists(path):
                                os.remove(path)
                        else:
                            await handle_large_file_upload(path, user_id, bot)
                    
                    elif mode.startswith("ai_subs_"):
                        position = mode.split("_")[2] if len(mode.split("_")) > 2 else "bottom"
                        
                        async def process_subs_bg(user_id, input_path, position):
                            progress_msg = await bot.send_message(user_id, "⚙️ Подготовка видео (субтитры)...")
                            
                            async def update_progress(percent, text_status):
                                filled = int(10 * percent / 100)
                                bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
                                msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
                                try:
                                    await progress_msg.edit_text(msg_text)
                                except Exception:
                                    pass
                                    
                            try:
                                output_path = await process_heavy_task(
                                    file_path=input_path,
                                    task_name="ai_subs",
                                    progress_callback=update_progress,
                                    position=position
                                )
                                
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                    
                                file_size = get_file_size_mb(output_path)
                                if file_size > MAX_TG_SIZE_MB:
                                    await handle_large_file_upload(output_path, user_id, bot)
                                else:
                                    width, height = None, None
                                    try:
                                        def _get_info(p):
                                            return ffmpeg.probe(p)
                                        probe = await asyncio.to_thread(_get_info, output_path)
                                        width, height = get_video_dimensions(probe)
                                    except Exception:
                                        pass
                                    await bot.send_video(user_id, FSInputFile(output_path), caption="✅ Субтитры добавлены!", width=width, height=height)
                                    if os.path.exists(output_path):
                                        os.remove(output_path)
                                        
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                            except Exception as e:
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                await bot.send_message(user_id, f"❌ Ошибка при добавлении субтитров. Попробуйте позже.\nПодробности: {e}")
                                
                        asyncio.create_task(process_subs_bg(user_id, path, position))
                        await sqlite_db.delete_task(task_id)
                        continue
                        
                    elif mode == "split_screen":
                        async def process_split_bg(user_id, user_path):
                            progress_msg = await bot.send_message(user_id, "⚙️ Подготовка видео (split screen)...")
                            
                            async def update_progress(percent, status_text="Обработка видео..."):
                                filled = int(10 * percent / 100)
                                bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
                                msg_text = f"⚙️ {status_text}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
                                try:
                                    await progress_msg.edit_text(msg_text)
                                except Exception:
                                    pass
                                    
                            try:
                                # Determine orientation
                                is_vertical = True
                                try:
                                    def _get_info(p):
                                        return ffmpeg.probe(p)
                                    probe = await asyncio.to_thread(_get_info, user_path)
                                    width, height = get_video_dimensions(probe)
                                    is_vertical = height >= width
                                except Exception:
                                    pass

                                target_folder = "vertical" if is_vertical else "horizontal"
                                bg_folder_path = os.path.join(BACKGROUNDS_DIR, target_folder)
                                bg_files = []
                                if os.path.exists(bg_folder_path):
                                    bg_files = [f for f in os.listdir(bg_folder_path) if f.lower().endswith(('.mp4', '.mov', '.avi'))]
                                if not bg_files:
                                    bg_folder_path = BACKGROUNDS_DIR
                                    bg_files = [f for f in os.listdir(BACKGROUNDS_DIR) if f.lower().endswith(('.mp4', '.mov', '.avi'))]
                                
                                bg_filename = random.choice(bg_files) if bg_files else None
                                bg_path = os.path.join(bg_folder_path, bg_filename) if bg_filename else None
                                
                                if not bg_path:
                                    await bot.send_message(user_id, "ℹ️ Фоновые видео не найдены. Видео будет растянуто на весь экран (1080x1920).")
                                
                                output_path = os.path.join(DOWNLOAD_DIR, f"split_{uuid.uuid4().hex[:8]}.mp4")

                                await update_progress(5, "Обработка видео (локально)...")
                                await generate_split_screen(
                                    user_video=user_path,
                                    background_video=bg_path,
                                    output_video=output_path,
                                    progress_callback=lambda p, m: update_progress(p, m),
                                    is_vertical=is_vertical
                                )
                                
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                    
                                file_size = get_file_size_mb(output_path)
                                if file_size > MAX_TG_SIZE_MB:
                                    await handle_large_file_upload(output_path, user_id, bot)
                                else:
                                    width, height = None, None
                                    try:
                                        def _get_info(p):
                                            return ffmpeg.probe(p)
                                        probe = await asyncio.to_thread(_get_info, output_path)
                                        width, height = get_video_dimensions(probe)
                                    except Exception:
                                        pass
                                    await bot.send_video(user_id, FSInputFile(output_path), caption="✅ Готово!", width=width, height=height)
                                    if os.path.exists(output_path):
                                        os.remove(output_path)
                                        
                                if os.path.exists(user_path):
                                    os.remove(user_path)
                            except Exception as e:
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                await bot.send_message(user_id, f"❌ Ошибка при создании split screen: {e}")
                                
                        asyncio.create_task(process_split_bg(user_id, path))
                        await sqlite_db.delete_task(task_id)
                        continue
                        
                    elif mode == "batch":
                        await bot.send_message(user_id, text="✅ Уникализированные видео готовы. Отправляю архив...")
                        if get_file_size_mb(path) <= MAX_TG_SIZE_MB:
                            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                                width, height = None, None
                                try:
                                    def _get_info(p):
                                        return ffmpeg.probe(p)
                                    probe = await asyncio.to_thread(_get_info, path)
                                    width, height = get_video_dimensions(probe)
                                except Exception:
                                    pass
                                await bot.send_video(user_id, FSInputFile(path), caption="📦 Архив с копиями", width=width, height=height)
                            else:
                                await bot.send_document(user_id, FSInputFile(path), caption="📦 Архив с копиями")
                            if os.path.exists(path):
                                os.remove(path)
                        else:
                            await handle_large_file_upload(path, user_id, bot)

                    elif mode == "upscale":
                        async def process_upscale_bg(user_id, input_path):
                            progress_msg = await bot.send_message(user_id, "⚙️ Улучшение качества (Upscale)...")
                            
                            async def update_progress(percent, text_status):
                                filled = int(10 * percent / 100)
                                bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
                                msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
                                try:
                                    await progress_msg.edit_text(msg_text)
                                except Exception:
                                    pass
                                    
                            try:
                                is_image = input_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
                                output_path = await process_heavy_task(
                                    file_path=input_path,
                                    task_name="upscale",
                                    progress_callback=update_progress,
                                    is_image=is_image
                                )
                                
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                    
                                file_size = get_file_size_mb(output_path)
                                if file_size > MAX_TG_SIZE_MB:
                                    await handle_large_file_upload(output_path, user_id, bot)
                                else:
                                    width, height = None, None
                                    try:
                                        def _get_info(p):
                                            return ffmpeg.probe(p)
                                        probe = await asyncio.to_thread(_get_info, output_path)
                                        width, height = get_video_dimensions(probe)
                                    except Exception:
                                        pass
                                    if output_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                                        await bot.send_photo(user_id, FSInputFile(output_path), caption="✨ Качество улучшено (x4)!")
                                    else:
                                        await bot.send_video(user_id, FSInputFile(output_path), caption="✨ Качество улучшено (x4)!", width=width, height=height)
                                    if os.path.exists(output_path):
                                        os.remove(output_path)
                                        
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                            except Exception as e:
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                await bot.send_message(user_id, f"❌ Ошибка при улучшении качества. Попробуйте позже.\nПодробности: {e}")
                                
                        asyncio.create_task(process_upscale_bg(user_id, path))
                        await sqlite_db.delete_task(task_id)
                        continue

                    elif mode.startswith("ai_subs_"):
                        position = mode.split("_")[-1]
                        async def process_subs_bg(user_id, input_path, pos):
                            progress_msg = await bot.send_message(user_id, "⚙️ Наложение субтитров...")
                            
                            async def update_progress(percent, text_status):
                                filled = int(10 * percent / 100)
                                bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
                                msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
                                try:
                                    await progress_msg.edit_text(msg_text)
                                except Exception:
                                    pass
                                    
                            try:
                                output_path = await process_heavy_task(
                                    file_path=input_path,
                                    task_name="ai_subs",
                                    progress_callback=update_progress,
                                    position=pos
                                )
                                
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                    
                                file_size = get_file_size_mb(output_path)
                                if file_size > MAX_TG_SIZE_MB:
                                    await handle_large_file_upload(output_path, user_id, bot)
                                else:
                                    width, height = None, None
                                    try:
                                        def _get_info(p):
                                            return ffmpeg.probe(p)
                                        probe = await asyncio.to_thread(_get_info, output_path)
                                        width, height = get_video_dimensions(probe)
                                    except Exception:
                                        pass
                                    await bot.send_video(user_id, FSInputFile(output_path), caption="✅ Субтитры добавлены!", width=width, height=height)
                                    if os.path.exists(output_path):
                                        os.remove(output_path)
                                        
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                            except Exception as e:
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                await bot.send_message(user_id, f"❌ Ошибка при наложении субтитров. Попробуйте позже.\nПодробности: {e}")
                                
                        asyncio.create_task(process_subs_bg(user_id, path, position))
                        await sqlite_db.delete_task(task_id)
                        continue

                    elif mode.startswith("ai_translate_"):
                        target_lang = mode.replace("ai_translate_", "")
                        async def process_translate_bg(user_id, input_path, target_lang):
                            progress_msg = await bot.send_message(user_id, "⚙️ Подготовка видео (перевод)...")
                            
                            async def update_progress(percent, text_status):
                                filled = int(10 * percent / 100)
                                bar = f"[{'▒' * filled}{'░' * (10 - filled)}]"
                                msg_text = f"⚙️ {text_status}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n{bar} {percent}%\n⏳ Пожалуйста, подождите..."
                                try:
                                    await progress_msg.edit_text(msg_text)
                                except Exception:
                                    pass
                                    
                            try:
                                await update_progress(5, "Загрузка видео в ElevenLabs...")
                                output_path = await dub_video_elevenlabs(
                                    file_path=input_path,
                                    target_lang=target_lang,
                                    progress_callback=update_progress
                                )
                                
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                    
                                file_size = get_file_size_mb(output_path)
                                if file_size > MAX_TG_SIZE_MB:
                                    await handle_large_file_upload(output_path, user_id, bot)
                                else:
                                    width, height = None, None
                                    try:
                                        def _get_info(p):
                                            return ffmpeg.probe(p)
                                        probe = await asyncio.to_thread(_get_info, output_path)
                                        width, height = get_video_dimensions(probe)
                                    except Exception:
                                        pass
                                    await bot.send_video(user_id, FSInputFile(output_path), caption="✅ Перевод и дубляж завершены!", width=width, height=height)
                                    if os.path.exists(output_path):
                                        os.remove(output_path)
                                        
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                            except Exception as e:
                                try:
                                    await progress_msg.delete()
                                except Exception:
                                    pass
                                await bot.send_message(user_id, f"❌ Ошибка при переводе видео. Попробуйте позже.\nПодробности: {e}")
                                
                        asyncio.create_task(process_translate_bg(user_id, path, target_lang))
                        await sqlite_db.delete_task(task_id)
                        continue

                    elif mode == "watermark":
                        from aiogram.fsm.storage.base import StorageKey
                        try:
                            # We need bot.id. If it's not available yet, we use the token prefix.
                            bot_id = bot.id if bot.id else int(bot.token.split(":")[0])
                            key = StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)
                            user_state = dp.fsm.resolve_context(key)
                            await user_state.update_data(video_path=path)
                            await user_state.set_state(WatermarkStates.waiting_text)
                            await bot.send_message(user_id, "✅ Видео скачано через UserBot. Теперь отправьте текст для вотермарки:")
                        except Exception as e:
                            logging.error(f"Error restoring watermark state: {e}")
                            await bot.send_message(user_id, "⚠️ Ошибка при восстановлении состояния вотермарки. Пожалуйста, попробуйте видео поменьше.")
                            if os.path.exists(path):
                                os.remove(path)

                    await sqlite_db.delete_task(task_id)

                except Exception as e:
                    print(f"❌ Ошибка при отправке результата обработчика: {e}")
        except Exception as e:
            print(f"❌ Ошибка в polling_sqlite: {e}")

        await asyncio.sleep(5)



# Вместо send_video используем send_document
@dp.message(StateFilter(None), F.from_user.id != USERBOT_ID, F.content_type.in_({ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_video_file(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("mode")
    file = message.video or message.document

    if not mode:
        await message.answer("Пожалуйста, выбери режим: одиночная или массовая.", reply_markup=main_inline_keyboard())
        return

    file_name = file.file_name or f"{uuid.uuid4()}.mp4"
    input_path = os.path.join(DOWNLOAD_DIR, file_name)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    try:
        await download_video(file, input_path)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower():
            await message.answer("🧬 Генерирую уникальную версию...")

            count = data.get("count")
            if mode == "batch" and count is None:
                await state.update_data(forwarded_file_id=file.file_id)
                await message.answer("Сколько уникализированных копий создать? (например: 10)")
                await state.set_state(UniqueStates.waiting_count)
                return

            meta = {
                "user_id": message.from_user.id,
                "mode": mode,
                "count": count or 1
            }
            
            try:
                # Если файл слишком большой, отправляем его в UserBot
                meta["file_unique_id"] = file.file_unique_id
                # Send meta first
                await bot.send_message(USERBOT_ID, json.dumps(meta))
                # Then forward the message
                await bot.forward_message(chat_id=USERBOT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
                
                msg = await message.answer(
                    "⚙️ Видео большое. Обработка через UserBot...\n"
                    "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    "[▒▒▒▒▒░░░░░] 50%\n"
                    "⏳ Пожалуйста, подождите..."
                )
                
                async def delete_later(m, delay):
                    await asyncio.sleep(delay)
                    try:
                        await m.delete()
                    except:
                        pass
                
                asyncio.create_task(delete_later(msg, 5))
            except Exception as e:
                await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {e}")

            # 👉 Оставляем пользователя в режиме single
            if mode == "batch":
                await state.clear()
            else:
                await state.update_data(mode="single")

            return


    # — Одиночная обработка —
    if mode == "single":
        output_path = os.path.join(DOWNLOAD_DIR, f"unique_{file_name}")
        progress_msg = await message.answer(
            "⚙️ Обработка видео...\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "[░░░░░░░░░░] 0%\n"
            "⏳ Пожалуйста, подождите..."
        )

        stop_event = asyncio.Event()
        progress_task = asyncio.create_task(animate_progress(progress_msg, stop_event))

        try:
            filters = await unique_video_single(input_path, output_path, True)

            stop_event.set()
            await progress_task

            await progress_msg.delete()
            
            # Отправляем результат
            safe_to_delete = await send_file_safely(message, output_path, caption="✅ Готово")

            if filters:
                await message.answer("📊 Применены фильтры:\n" + "\n".join(f"• {f}" for f in filters))

            if os.path.exists(input_path): os.remove(input_path)
            if safe_to_delete and os.path.exists(output_path): os.remove(output_path)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu_main")]
            ])
            await message.answer("📭 Можешь прислать следующее видео или вернуться в меню", reply_markup=keyboard)

        except Exception as e:
            stop_event.set()
            await progress_task
            await progress_msg.edit_text(f"⚠️ Ошибка во время обработки. Попробуй ещё раз.\nПодробности: {e}")


    # — Массовая обработка —
    elif mode == "batch":
        await state.update_data(input_path=input_path)
        await message.answer("Сколько уникализированных копий создать? (например: 10)")
        await state.set_state(UniqueStates.waiting_count)



@dp.message(F.text, UniqueStates.waiting_count)
async def handle_count(message: Message, state: FSMContext):
    text = message.text.strip()

    if not text.isdigit():
        await message.answer("❗ Введите число от 1 до 100, например: 10")
        return

    count = int(text)
    if count < 1 or count > 100:
        await message.answer("❗ Введите число от 1 до 100, например: 10")
        return

    await state.update_data(count=count)
    data = await state.get_data()
    input_path = data.get("input_path")
    forwarded_file_id = data.get("forwarded_file_id")

    # 👉 forwarded видео — отправляем сразу в UserBot
    if not input_path and forwarded_file_id:
        meta = {
            "user_id": message.from_user.id,
            "mode": "batch",
            "count": count
        }

        try:
            await bot.send_video(
                chat_id=USERBOT_ID,
                video=forwarded_file_id,
                caption=json.dumps(meta)
            )
            msg = await message.answer(
                "⚙️ Обработка видео...\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                "[▒▒▒▒▒░░░░░] 50%\n"
                "⏳ Пожалуйста, подождите..."
            )
            
            async def delete_later(m, delay):
                await asyncio.sleep(delay)
                try:
                    await m.delete()
                except:
                    pass
            
            asyncio.create_task(delete_later(msg, 5))
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {e}")
            
        await state.clear()
        return

    # ✅ Локальная генерация (маленькие видео)
    session_id = str(uuid.uuid4())
    output_dir = os.path.join(DOWNLOAD_DIR, f"batch_{session_id}")
    os.makedirs(output_dir, exist_ok=True)

    progress_msg = await message.answer(
        "⚙️ Обработка видео...\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "[░░░░░░░░░░] 0%\n"
        "⏳ Готово: 0"
    )

    semaphore = asyncio.Semaphore(3)  # 🧠 Можешь менять это значение
    generated_files = await generate_unique_copies_async(
        input_path=input_path,
        output_dir=output_dir,
        count=count,
        semaphore=semaphore,
        progress_callback=lambda status: progress_msg.edit_text(status)
    )

    try:
        await progress_msg.delete()
    except Exception:
        pass

    all_safe_to_delete = True
    if not generated_files:
        await message.answer("⚠️ Не удалось сгенерировать уникальные видео.")
    elif len(generated_files) <= 3:
        for path in generated_files:
            safe = await send_file_safely(message, path, caption="✅ Готово!")
            if not safe:
                all_safe_to_delete = False
            if safe and os.path.exists(path): os.remove(path)
        await message.answer("✅ Все уникализированные видео отправлены.")
    else:
        zip_path = os.path.join(DOWNLOAD_DIR, f"archive_{session_id}.zip")
        await create_zip(generated_files, zip_path)

        safe_to_delete = await send_file_safely(message, zip_path, caption="✅ Архив с видео")
        
        if safe_to_delete and os.path.exists(zip_path): os.remove(zip_path)

    if all_safe_to_delete:
        shutil.rmtree(output_dir, ignore_errors=True)
    if input_path and os.path.exists(input_path):
        os.remove(input_path)

    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu_main")]
    ])
    await message.answer("📭 Можешь прислать следующее видео или вернуться в меню", reply_markup=keyboard)
    
async def generate_unique_copies_async(input_path, output_dir, count, semaphore, progress_callback):
    import uuid
    import shutil
    import time
    from pathlib import Path

    def render_progress(i, total, width=10):
        percent = int(100 * i / total)
        filled = int(width * i / total)
        bar = f"[{'▒' * filled}{'░' * (width - filled)}]"
        return (
            "⚙️ Обработка видео...\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"{bar} {percent}%\n"
            f"⏳ Готово: {i} из {total}"
        )

    seen_hashes = set()
    used_vf_signatures = set()
    generated_files = []

    progress = {'done': 0}
    progress_lock = asyncio.Lock()
    progress_last_updated = time.monotonic()

    async def generate_one():
        nonlocal progress_last_updated

        async with semaphore:
            tries = 0
            max_tries = 10

            while tries < max_tries:
                vf, af, extra_flags, flags = get_random_filters(input_path)
                vf_signature = vf.strip().replace(" ", "")
                if vf_signature in used_vf_signatures:
                    tries += 1
                    continue
                used_vf_signatures.add(vf_signature)

                suffix = f"_{uuid.uuid4().hex[:8]}.mp4"
                output_path = os.path.join(output_dir, f"unique{suffix}")

                try:
                    await unique_video_single(input_path, output_path, vf=vf, af=af, extra_flags=flags)
                except Exception as e:
                    logging.error(f"Error generating unique video: {e}")
                    tries += 1
                    continue

                is_valid = await is_valid_mp4(output_path)
                if not is_valid:
                    logging.error(f"Generated video is invalid: {output_path}")
                    tries += 1
                    continue

                file_hash = await hash_file(output_path)
                if file_hash in seen_hashes:
                    os.remove(output_path)
                    tries += 1
                    continue

                seen_hashes.add(file_hash)
                generated_files.append(output_path)

                async with progress_lock:
                    progress['done'] += 1
                    now = time.monotonic()
                    should_update = (
                        count <= 10 or
                        (count <= 50 and progress['done'] % 5 == 0) or
                        (now - progress_last_updated >= 6)
                    )
                    if should_update:
                        try:
                            await progress_callback(render_progress(progress['done'], count))
                            progress_last_updated = now
                        except:
                            pass
                return

    tasks = [generate_one() for _ in range(count)]
    await asyncio.gather(*tasks)

    return generated_files

@dp.message(Command("userbot_status"))
async def cmd_userbot_status(message: Message):
    await message.answer(f"⏳ Проверяю статус UserBot (ID: {USERBOT_ID})...")
    try:
        # Send ping to UserBot
        await bot.send_message(USERBOT_ID, "ping")
        await message.answer("✅ Запрос 'ping' отправлен. Если UserBot работает, вы получите 'pong' через несколько секунд.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке запроса UserBot: {e}")

@dp.message(Command("userbot_id"))
async def cmd_userbot_id(message: Message):
    await message.answer(
        f"🤖 Информация об ID:\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🔹 MainBot ID: `{bot.id}`\n"
        f"🔹 Текущий USERBOT_ID в конфиге: `{USERBOT_ID}`\n\n"
        f"💡 Если вы хотите изменить UserBot, установите `USERBOT_ID` в файле `.env` и перезапустите бота."
    )

@dp.message(F.from_user.id == USERBOT_ID, F.text)
async def handle_userbot_text(message: Message):
    logging.info(f"[MainBot] Received text message from UserBot: {message.text}")
    # If the userbot sends an error message, we might want to forward it to the user
    # But we need to know WHICH user.
    # The userbot usually replies to the message that triggered the action.
    # If it's a reply, we can try to extract the user_id from the original message's caption if it was a JSON.
    
    if message.reply_to_message and message.reply_to_message.caption:
        try:
            meta = json.loads(message.reply_to_message.caption)
            user_id = meta.get("user_id")
            if user_id:
                await bot.send_message(user_id, f"🤖 Сообщение от UserBot:\n{message.text}")
        except:
            pass
    elif "для" in message.text: # Simple heuristic for "Загружаю большой файл для {user_id}..."
        import re
        match = re.search(r"для (\d+)", message.text)
        if match:
            user_id = int(match.group(1))
            await bot.send_message(user_id, f"🤖 Статус от UserBot:\n{message.text}")

@dp.message(F.from_user.id == USERBOT_ID, F.content_type.in_({ContentType.DOCUMENT, ContentType.VIDEO}))
async def handle_userbot_forward(message: Message):
    logging.info(f"[MainBot] Received message from UserBot (ID: {message.from_user.id})")
    try:
        if not message.caption:
            logging.warning("[MainBot] Received message from UserBot without caption.")
            return
        
        logging.info(f"[MainBot] UserBot caption: {message.caption}")
        try:
            meta = json.loads(message.caption)
        except json.JSONDecodeError:
            logging.warning(f"[MainBot] Failed to parse JSON from UserBot caption: {message.caption}")
            return

        if meta.get("action") == "forward":
            user_id = int(meta.get("user_id"))
            logging.info(f"[MainBot] Forwarding file to user_id={user_id}")
            
            try:
                if message.video:
                    await bot.send_video(
                        chat_id=user_id,
                        video=message.video.file_id,
                        caption="✅ Готово!"
                    )
                else:
                    await bot.send_document(
                        chat_id=user_id,
                        document=message.document.file_id,
                        caption="✅ Готово!"
                    )
                # Optionally notify userbot that it was successful
                await message.reply("✅ Файл успешно переслан пользователю.")
                logging.info(f"[MainBot] Successfully forwarded file to user_id={user_id}")
            except Exception as send_error:
                logging.error(f"[MainBot] ❌ Ошибка при отправке файла пользователю {user_id}: {send_error}")
                await message.reply(f"❌ Не удалось отправить файл пользователю {user_id}: {send_error}")
        else:
            logging.info(f"[MainBot] Ignored UserBot action: {meta.get('action')}")
    except Exception as e:
        logging.error(f"[MainBot] ❌ Ошибка при пересылке файла от обработчика: {e}", exc_info=True)

if __name__ == "__main__":
    # Safety check: do not run the bot on RunPod
    if os.getenv("RUNPOD_POD_ID"):
        logging.error("[MainBot] ⚠️ Detected RunPod environment. Bot will not start here.")
        sys.exit(0)

    logging.basicConfig(level=logging.INFO)

    async def main():
        logging.info(f"[MainBot] Starting with USERBOT_ID: {USERBOT_ID}")
        
        # Start UserBot as a separate process
        try:
            import subprocess
            import sys
            import signal
            
            # Check if pyrogram is installed for this interpreter
            try:
                import pyrogram
                logging.info("[MainBot] Pyrogram is available.")
            except ImportError:
                logging.error("[MainBot] ❌ Pyrogram is NOT installed for this Python interpreter!")
                logging.error(f"[MainBot] Please run: {sys.executable} -m pip install pyrogram tgcrypto")
            
            userbot_path = os.path.join(os.path.dirname(__file__), "userbot.py")
            
            # Try to kill existing userbot processes to avoid session locks
            if sys.platform != "win32":
                try:
                    subprocess.run(["pkill", "-f", "userbot.py"], capture_output=True)
                    logging.info("[MainBot] Killed existing userbot processes.")
                except:
                    pass

            # Pass environment variables explicitly
            env = os.environ.copy()
            env["MAIN_BOT_ID"] = str(bot.id)
            env["USERBOT_ID"] = str(USERBOT_ID)
            
            subprocess.Popen([sys.executable, userbot_path], env=env)
            logging.info(f"[MainBot] UserBot process started with MAIN_BOT_ID={bot.id}")
        except Exception as e:
            logging.error(f"[MainBot] Failed to start UserBot: {e}")

        await sqlite_db.init_db()  # <--- ВАЖНО: вызываем до запуска anything
        asyncio.create_task(polling_sqlite())
        await dp.start_polling(bot)

    asyncio.run(main())
