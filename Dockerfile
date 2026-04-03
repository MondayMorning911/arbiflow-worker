# Используем образ с CUDA для GPU-задач на RunPod
FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Установка системных зависимостей (FFmpeg, шрифты, библиотеки для OpenCV)
RUN apt-get update && \
    apt-get install -y ffmpeg fonts-liberation libgl1-mesa-glx libglib2.0-0 wget git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка Python-зависимостей
WORKDIR /app
COPY requirements.txt .

# ❗ ПРИНУДИТЕЛЬНО УДАЛЯЕМ conda numpy и ставим версию 1.x
RUN conda remove -y numpy && \
    pip install --no-cache-dir "numpy==1.26.4" && \
    pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем необходимые папки
RUN mkdir -p downloads sessions /tmp/arbiflow

# На RunPod должен запускаться только обработчик (worker)
# Основной бот (bot.py) будет запущен пользователем локально на его машине (Mac)
CMD ["python", "runpod/handler.py"]