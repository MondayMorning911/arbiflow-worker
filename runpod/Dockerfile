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
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Скачивание весов нейросетей (Real-ESRGAN и GFPGAN)
RUN mkdir -p /app/weights && \
    wget -O /app/weights/RealESRGAN_x4plus.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth && \
    wget -O /app/weights/GFPGANv1.4.pth https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth

# Копируем все файлы проекта
COPY . .

# Создаем необходимые папки
RUN mkdir -p downloads sessions /tmp/arbiflow

# На RunPod должен запускаться только обработчик (worker)
# Основной бот (bot.py) будет запущен пользователем локально на его машине (Mac)
CMD ["python", "runpod/handler.py"]
