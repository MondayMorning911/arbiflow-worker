# Используем официальный образ NVIDIA CUDA как более стабильную основу
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Установка Python 3.10 и системных зависимостей
RUN apt-get update && \
    apt-get install -y python3.10 python3-pip ffmpeg aria2 axel fonts-liberation libgl1-mesa-glx libglib2.0-0 wget git && \
    rm -rf /var/lib/apt/lists/*

# Делаем python3.10 основным python
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 && \
    update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Установка Torch и остальных библиотек через pip (избегаем конфликтов conda)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch==2.1.0+cu118 --extra-index-url https://download.pytorch.org/whl/cu118 && \
    pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем необходимые папки
RUN mkdir -p downloads sessions /tmp/arbiflow

# На RunPod должен запускаться только обработчик (worker)
# Основной бот (bot.py) будет запущен пользователем локально на его машине (Mac)
CMD ["python", "runpod/handler.py"]