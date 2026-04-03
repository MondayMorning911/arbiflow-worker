# Используем образ с CUDA для GPU-задач на RunPod
FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Установка системных зависимостей (FFmpeg, шрифты, библиотеки для OpenCV)
RUN apt-get update && \
    apt-get install -y ffmpeg fonts-liberation libgl1-mesa-glx libglib2.0-0 wget git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Установка Python-зависимостей
# 1. Обновляем pip
# 2. Устанавливаем mkl для исправления ошибки iJIT_NotifyEvent
# 3. Устанавливаем остальные зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir mkl mkl-service && \
    pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем необходимые папки
RUN mkdir -p downloads sessions /tmp/arbiflow

# На RunPod должен запускаться только обработчик (worker)
CMD ["python", "runpod/handler.py"]