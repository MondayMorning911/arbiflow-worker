#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 Запуск UniUniBot..."

# Запуск UserBot в фоне
python3 userbot.py &

# Запуск основного бота
python3 bot.py

echo "✅ Бот остановлен."
