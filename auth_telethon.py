import os
from telethon.sync import TelegramClient
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

if API_ID == 0 or not API_HASH:
    print("❌ Ошибка: TELEGRAM_API_ID или TELEGRAM_API_HASH не найдены в .env файле.")
    exit(1)

# Путь к файлу сессии
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
os.makedirs(os.path.join(BASE_DIR, "sessions"), exist_ok=True)
SESSION_NAME = os.path.join(BASE_DIR, "sessions", "telethon_downloader")

print(f"🚀 Запуск авторизации Telethon...")
print(f"📁 Файл сессии будет сохранен как: {SESSION_NAME}.session\n")

# Запускаем интерактивную авторизацию
with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
    print("\n✅ Успешно! Сессия авторизована и сохранена.")
    print("Теперь вы можете запушить код и файл сессии на сервер/RunPod.")
