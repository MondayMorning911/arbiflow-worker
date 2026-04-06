import os
import sys

# Add parent directory to sys.path so we can import telegram_downloader
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
import requests
import runpod
import torch
import ffmpeg
import traceback
import shutil
import json
import zipfile
import asyncio
import time
from dotenv import load_dotenv

# --- EARLY LOGGING ---
print("🚀 [ArbiFlow Worker]: Starting initialization...", flush=True)

# Load environment variables from .env if it exists
load_dotenv()

try:
    from google import genai
    from google.genai import types
    import edge_tts
    from faster_whisper import WhisperModel
    import cv2
    import mediapipe as mp
    print("✅ [ArbiFlow Worker]: All modules imported successfully.", flush=True)
except ImportError as e:
    print(f"❌ [ArbiFlow Worker]: MISSING MODULE: {e}", file=sys.stderr, flush=True)
    # Don't exit yet, let's see if we can provide more info
except Exception as e:
    print(f"❌ [ArbiFlow Worker]: IMPORT ERROR: {e}", file=sys.stderr, flush=True)
    traceback.print_exc()

# --- CONFIGURATION ---
VOLUME_PATH = "/runpod-volume"
MODEL_PATH = os.path.join(VOLUME_PATH, "models")
TEMP_PATH = "/tmp/arbiflow"

# Пытаемся найти шрифт
BASE_DIR = os.getcwd()
FONT_PATH = os.path.join(BASE_DIR, "runpod", "SoyuzGroteskBold.ttf")
if not os.path.exists(FONT_PATH):
    FONT_PATH = os.path.join(BASE_DIR, "SoyuzGroteskBold.ttf")
    if not os.path.exists(FONT_PATH):
        FONT_PATH = "/app/runpod/SoyuzGroteskBold.ttf"

FONT_DIR = os.path.dirname(FONT_PATH)
print(f"📍 [ArbiFlow Worker]: Font path: {FONT_PATH}", flush=True)

# Глобальные переменные для моделей (ленивая загрузка)
whisper_model = None
mp_face_detection = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        print("📥 [ArbiFlow Worker]: Loading Whisper model (large-v3)...", flush=True)
        try:
            cuda_available = torch.cuda.is_available()
            if cuda_available:
                device = "cuda"
                compute_type = "float16"
                gpu_name = torch.cuda.get_device_name(0)
                print(f"🖥️ [ArbiFlow Worker]: GPU DETECTED: {gpu_name}", flush=True)
            else:
                device = "cpu"
                compute_type = "int8"
                print("🖥️ [ArbiFlow Worker]: NO GPU DETECTED. Falling back to CPU mode.", flush=True)
                # Проверим, видит ли система драйвер вообще
                try:
                    import subprocess
                    n_smi = subprocess.check_output(["nvidia-smi"], stderr=subprocess.STDOUT).decode()
                    print(f"🔍 [ArbiFlow Worker]: nvidia-smi output:\n{n_smi}", flush=True)
                except Exception:
                    print("🔍 [ArbiFlow Worker]: nvidia-smi command failed. Drivers might be missing.", flush=True)

            print(f"⚙️ [ArbiFlow Worker]: Using device: {device} ({compute_type})", flush=True)
            
            whisper_model = WhisperModel(
                "large-v3", 
                device=device, 
                compute_type=compute_type, 
                download_root=MODEL_PATH
            )
            print("✅ [ArbiFlow Worker]: Whisper model loaded.", flush=True)
        except Exception as e:
            print(f"❌ [ArbiFlow Worker]: Failed to load Whisper: {e}", file=sys.stderr, flush=True)
            raise e
    return whisper_model

mp_face_detection_instance = None
cv2_face_cascade_instance = None
face_detection_method = None

def init_face_detector():
    global mp_face_detection_instance, cv2_face_cascade_instance, face_detection_method
    if face_detection_method is not None:
        return
        
    # Сначала пробуем OpenCV (более стабилен по структурам данных)
    try:
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        if os.path.exists(cascade_path):
            cv2_face_cascade_instance = cv2.CascadeClassifier(cascade_path)
            if not cv2_face_cascade_instance.empty():
                face_detection_method = "opencv"
                print("✅ [ArbiFlow Worker]: OpenCV face detection initialized (Primary).", flush=True)
                return
    except Exception as e:
        print(f"⚠️ [ArbiFlow Worker]: OpenCV initialization failed ({e}).", flush=True)

    # MediaPipe отключен по просьбе пользователя (вызывал tuple index out of range)
    """
    try:
        import mediapipe as mp
        try:
            import mediapipe.python.solutions.face_detection as mp_fd
        except ImportError:
            mp_fd = mp.solutions.face_detection
        mp_face_detection_instance = mp_fd.FaceDetection(model_selection=1, min_detection_confidence=0.5)
        face_detection_method = "mediapipe"
        print("✅ [ArbiFlow Worker]: MediaPipe face detection initialized (Fallback).", flush=True)
        return
    except Exception as e:
        print(f"⚠️ [ArbiFlow Worker]: MediaPipe initialization failed ({e}).", flush=True)
    """
        
    face_detection_method = "disabled"
    print("👤 [ArbiFlow Worker]: Face detection disabled (no engines available).", flush=True)

def get_face_center_x(video_path, start_time, duration, original_width):
    try:
        init_face_detector()
        # Если MediaPipe отключен или не сработал — используем только OpenCV или центр
        if face_detection_method != "opencv" or cv2_face_cascade_instance is None:
            return original_width // 2
            
        cap = cv2.VideoCapture(video_path)
        sample_interval = 2.0
        num_samples = max(3, int(duration / sample_interval))
        sample_times = [start_time + i * (duration / num_samples) for i in range(num_samples)]
        
        x_coords = []
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t * 1000))
            ret, frame = cap.read()
            if not ret or frame is None: continue
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cv2_face_cascade_instance.detectMultiScale(gray, 1.1, 6, minSize=(60, 60))
            
            # Безопасная проверка: faces может быть кортежем или массивом
            if faces is not None and len(faces) > 0:
                faces_list = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
                x, y, w, h = faces_list[0]
                x_coords.append(x + w / 2)
        
        cap.release()
        if x_coords:
            x_coords.sort()
            return int(x_coords[len(x_coords) // 2])
        return original_width // 2
    except Exception as e:
        print(f"⚠️ [ArbiFlow]: Face detection failed safe: {e}")
        return original_width // 2

def download_file(url, dest):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(url, stream=True, timeout=300, headers=headers)
    response.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def upload_to_catbox(file_path):
    import time
    import sys
    file_size = os.path.getsize(file_path) / (1024 * 1024)
    print(f"Uploading file: {os.path.basename(file_path)} ({file_size:.2f} MB)", file=sys.stderr)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 1. Catbox (200MB limit)
    if file_size <= 200:
        for attempt in range(3):
            try:
                url = "https://catbox.moe/user/api.php"
                with open(file_path, 'rb') as f:
                    data = {'reqtype': 'fileupload'}
                    files = {'fileToUpload': (os.path.basename(file_path), f)}
                    response = requests.post(url, data=data, files=files, headers=headers, timeout=180)
                    if response.status_code == 200:
                        return response.text.strip()
                    print(f"Catbox attempt {attempt+1} failed: {response.status_code}", file=sys.stderr)
            except Exception as e:
                print(f"Catbox attempt {attempt+1} error: {e}", file=sys.stderr)
            time.sleep(2)
    else:
        print("File too large for Catbox (>200MB), skipping...", file=sys.stderr)
        
    # 2. Fallback to 0x0.st (512MB limit)
    if file_size <= 512:
        try:
            url = "https://0x0.st"
            with open(file_path, 'rb') as f:
                files = {'file': (os.path.basename(file_path), f)}
                response = requests.post(url, files=files, headers=headers, timeout=180)
                if response.status_code == 200:
                    return response.text.strip()
                print(f"0x0.st failed: {response.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"0x0.st error: {e}", file=sys.stderr)
            
    # 3. Fallback to file.io (2GB limit, but expires after 1 download)
    try:
        print("Trying file.io fallback...", file=sys.stderr)
        url = "https://file.io"
        with open(file_path, 'rb') as f:
            files = {'file': f}
            response = requests.post(url, files=files, headers=headers, timeout=180)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("link")
            print(f"file.io failed: {response.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"file.io error: {e}", file=sys.stderr)
        
    raise Exception(f"Upload to cloud failed after multiple attempts (File size: {file_size:.2f} MB)")

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{millis:02d}"

def generate_ass_subtitles(segments, output_path, position="bottom", width=1080, height=1920):
    font_name = "Soyuz Grotesk Bold"
    alignment = 5 if position == "middle" else 2
    margin_v = 0 if position == "middle" else int(height * 0.13)
    font_size = int(height * 0.04)
        
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,1.5,{alignment},10,10,{margin_v},1
"""
    lines = []
    for segment in segments:
        if not hasattr(segment, 'words') or not segment.words:
            start = format_timestamp(segment.start)
            end = format_timestamp(segment.end)
            text = segment.text.strip().replace("\n", "\\N").upper().replace('Ё', 'Е')
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
            continue

        for word in segment.words:
            start = format_timestamp(word.start)
            end = format_timestamp(word.end)
            text = word.word.strip().upper().replace('Ё', 'Е')
            for p in ['-', ',', '.', '!', '?', ':', ';']:
                text = text.replace(p, '')
            if text:
                lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write("\n".join(lines))

def extract_video_id(url):
    import re
    # Регулярка для извлечения ID из разных типов ссылок YouTube
    regex = r"(?:v=|\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})"
    match = re.search(regex, url)
    if match:
        return match.group(1)
    return url

def download_file_multithreaded_python(url, dest, threads=8):
    """
    Многопоточное скачивание через Range headers на чистом Python.
    Максимально ускоряет загрузку, если сервер поддерживает Range.
    """
    import requests
    import concurrent.futures
    import os

    try:
        # 1. Получаем размер файла
        head = requests.head(url, timeout=10, allow_redirects=True)
        file_size = int(head.headers.get('content-length', 0))
        
        # Если сервер не поддерживает Range или размер неизвестен - качаем обычно
        if file_size <= 0 or head.headers.get('accept-ranges') != 'bytes' and 'content-range' not in head.headers:
            print(f"⚠️ [ArbiFlow]: Сервер не поддерживает Range, качаем в один поток...", flush=True)
            return False

        print(f"🚀 [ArbiFlow]: Запуск многопоточного Python-загрузчика ({threads} потоков, {file_size/1024/1024:.1f} MB)...", flush=True)
        
        chunk_size = file_size // threads
        futures = []
        
        # Создаем пустой файл нужного размера
        with open(dest, 'wb') as f:
            f.seek(file_size - 1)
            f.write(b'\0')

        def download_chunk(start, end, chunk_idx):
            headers = {
                'Range': f'bytes={start}-{end}',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            proxies = {"http": PROXY_URL, "https": PROXY_URL}
            with requests.get(url, headers=headers, stream=True, timeout=60, proxies=proxies) as r:
                r.raise_for_status()
                with open(dest, 'r+b') as f:
                    f.seek(start)
                    # Стримим кусок для экономии памяти
                    for chunk in r.iter_content(chunk_size=1024*1024): # 1MB
                        if chunk:
                            f.write(chunk)
                            f.flush()
                            os.fsync(f.fileno()) # Гарантируем запись на диск

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            for i in range(threads):
                start = i * chunk_size
                end = (i + 1) * chunk_size - 1 if i < threads - 1 else file_size - 1
                futures.append(executor.submit(download_chunk, start, end, i))
            
            # Ждем завершения всех потоков
            concurrent.futures.wait(futures)
            
        # Проверяем финальный размер
        if os.path.exists(dest) and os.path.getsize(dest) >= file_size:
            print(f"✅ [ArbiFlow]: Многопоточный Python-загрузчик завершил работу!", flush=True)
            return True
    except Exception as e:
        print(f"⚠️ [ArbiFlow]: Ошибка многопоточного Python-загрузчика: {e}", flush=True)
    
    return False

def download_file_fast(direct_link, dest_path, method="aria2c"):
    """
    Самый надежный метод скачивания сверхдлинных ссылок.
    """
    import subprocess
    import os
    
    # Гарантируем наличие папки
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    if method == "aria2c":
        # Резерв: aria2c (axel убрали по просьбе пользователя)
        url_file = dest_path + ".url.txt"
        with open(url_file, "w") as f:
            f.write(direct_link)
            
        try:
            print(f"🚀 [ArbiFlow]: Запуск aria2c через прокси...", flush=True)
            cmd = [
                "aria2c", 
                "-i", url_file,
                "-x", "16", "-s", "16", "-j", "16", "-k", "1M",
                "--all-proxy", PROXY_URL,
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "--check-certificate=false",
                "--file-allocation=none",
                "--max-connection-per-server=16",
                "--split=16",
                "--min-split-size=1M",
                "-o", os.path.basename(dest_path), 
                "-d", os.path.dirname(dest_path)
            ]
            process = subprocess.run(cmd, capture_output=True)
            if process.returncode == 0 and os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                print(f"✅ [ArbiFlow]: Файл успешно сохранен через aria2c ({round(os.path.getsize(dest_path)/1024/1024, 2)} MB)", flush=True)
                return True
            else:
                print(f"⚠️ [ArbiFlow]: aria2c failed or file empty (code {process.returncode})", flush=True)
        except Exception as e:
            print(f"⚠️ [ArbiFlow]: aria2c error: {e}", flush=True)
        finally:
            if os.path.exists(url_file): os.remove(url_file)
            
        # Если системные утилиты не сработали, пробуем наш многопоточный Python-загрузчик
        if not os.path.exists(dest_path) or os.path.getsize(dest_path) < 100:
            print(f"🔄 [ArbiFlow]: aria2c не справился, пробуем Multi-threaded Python...", flush=True)
            if download_file_multithreaded_python(direct_link, dest_path):
                return True
            
    elif method == "requests":
        print(f"🚀 [ArbiFlow]: Запуск ускоренного requests (1MB buffer) через прокси...", flush=True)
        try:
            import requests
            proxies = {"http": PROXY_URL, "https": PROXY_URL}
            # Увеличиваем таймаут и используем сессию для скорости
            with requests.Session() as s:
                s.proxies.update(proxies)
                response = s.get(direct_link, stream=True, timeout=300)
                response.raise_for_status()
                with open(dest_path, 'wb') as f:
                    # Используем большой буфер для записи
                    for chunk in response.iter_content(chunk_size=1024*1024): # 1MB chunk
                        if chunk:
                            f.write(chunk)
            
            if os.path.exists(dest_path):
                print(f"✅ [ArbiFlow]: Файл успешно сохранен через requests ({round(os.path.getsize(dest_path)/1024/1024, 2)} MB)", flush=True)
                return True
        except Exception as e:
            print(f"⚠️ [ArbiFlow]: Ошибка при скачивании через requests: {e}", flush=True)
            return False
            
    return False

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
PROXY_URL = "socks5://arbiproxy:arbiproxy@83.147.18.62:1080"
COOKIES_PATH = "/cookies.txt"
if not os.path.exists(COOKIES_PATH):
    COOKIES_PATH = "cookies.txt"
    if not os.path.exists(COOKIES_PATH):
        COOKIES_PATH = None

def download_via_vps_cobalt(video_url, dest_path):
    print(f"📡 [ArbiFlow]: Запрос ссылки через твой VPS Cobalt (Port 9005)...", flush=True)
    # Твой проверенный IP и порт
    cobalt_api = "http://83.147.18.62:9005/" 
    
    payload = {
        "url": video_url,
        "videoQuality": "1080",
        "filenameStyle": "basic"
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    try:
        # Шлем POST на корень, как в твоем успешном curl-тесте
        response = requests.post(cobalt_api, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        direct_link = data.get("url")
        if direct_link:
            print(f"✅ [ArbiFlow]: Ссылка получена! Начинаю скачивание...", flush=True)
            # Используем твой метод с aria2c и SOCKS5 прокси
            return download_file_fast(direct_link, dest_path, method="aria2c")
            
    except Exception as e:
        print(f"⚠️ Cobalt VPS failed: {e}", flush=True)
    return False

def download_via_ytdlp(video_url, dest_path):
    """
    Скачивание через yt-dlp с использованием прокси и куки.
    """
    import subprocess
    import os
    
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    proxy_url = "socks5://arbiproxy:arbiproxy@83.147.18.62:1080"

    # Убрали --audio-langs, добавили приоритет RU в параметр -f
    cmd = [
        "python3", "-m", "yt_dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[language~='(?i)ru|orig']/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--proxy", proxy_url,
        "--no-check-certificate",
        "-o", dest_path,
        video_url
    ]
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.extend(["--cookies", COOKIES_PATH])
        print(f"🍪 [ArbiFlow]: Куки подключены ({COOKIES_PATH}).", flush=True)

    try:
        print(f"📡 [ArbiFlow]: Запуск yt-dlp (Module) без лишних флагов...", flush=True)
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode == 0 and os.path.exists(dest_path):
            print(f"✅ [ArbiFlow]: yt-dlp успешно скачал видео!", flush=True)
            return True
        else:
            stderr = process.stderr if process.stderr else "No error output"
            print(f"❌ yt-dlp error: {stderr[:300]}", flush=True)
            return False
    except Exception as e:
        print(f"🔥 Critical yt-dlp error: {e}", flush=True)
        return False

    print(f"❌ [ArbiFlow]: Все способы запуска yt-dlp провалены.", flush=True)
    return False

def download_via_rapidapi(video_url, dest_path, method="aria2c"):
    """
    Стабильное скачивание видео в 1080p через RapidAPI для продакшена ArbiFlow.
    """
    print(f"📡 [ArbiFlow]: Получение прямой ссылки через RapidAPI (Target: 1080p)...", flush=True)
    
    # 1. Извлекаем ID видео из ссылки (поддерживает разные форматы YouTube)
    video_id = extract_video_id(video_url)
    if not video_id or len(video_id) != 11:
        print(f"❌ [ArbiFlow]: Не удалось распознать ID видео в ссылке: {video_url}", flush=True)
        return False

    # 2. Конфигурация API
    rapid_url = "https://youtube-media-downloader.p.rapidapi.com/v2/video/details"
    headers = {
        "x-rapidapi-key": "a46b139ademsh2c7c294d619b0a2p1015bajsnb930db830785",
        "x-rapidapi-host": "youtube-media-downloader.p.rapidapi.com"
    }
    params = {"videoId": video_id}

    try:
        # Запрос метаданных видео с ретраями при 429
        import time
        proxies = {"http": PROXY_URL, "https": PROXY_URL}
        for attempt in range(3):
            response = requests.get(rapid_url, headers=headers, params=params, timeout=20, proxies=proxies)
            if response.status_code == 429:
                print(f"⚠️ [ArbiFlow]: RapidAPI 429 (Too Many Requests). Ждем 5 сек (попытка {attempt+1}/3)...", flush=True)
                time.sleep(5)
                continue
            response.raise_for_status()
            break
        else:
            print(f"❌ [ArbiFlow]: RapidAPI не ответил после 3 попыток (429).", flush=True)
            return False
            
        data = response.json()

        # 1. Ищем видео 1080p
        video_items = data.get("videos", {}).get("items", [])
        if not video_items:
            print(f"❌ [ArbiFlow]: API не вернул доступных видео ссылок для ID: {video_id}", flush=True)
            return False
            
        best_video_link = None
        for item in video_items:
            if item.get("quality") == "1080p":
                best_video_link = item.get("url")
                print(f"💎 [ArbiFlow]: Найдено видео 1080p.", flush=True)
                break
                
        if not best_video_link:
            best_video_link = video_items[0].get("url")
            print(f"⚠️ [ArbiFlow]: 1080p недоступно, выбрано качество: {video_items[0].get('quality')}", flush=True)

        # 2. Ищем лучшее аудио
        audio_items = data.get("audios", {}).get("items", [])
        best_audio_link = None
        if audio_items:
            print(f"🎵 [ArbiFlow]: Доступно аудио-дорожек: {len(audio_items)}", flush=True)
            # Пытаемся найти русскую дорожку или ту, что помечена как оригинал/дефолт
            for item in audio_items:
                lang = str(item.get("language", "")).lower()
                print(f"   - Дорожка: {lang} (original: {item.get('is_original')}, default: {item.get('default')})", flush=True)
                # Проверяем на 'ru', 'original', 'default' или отсутствие языка (часто оригинал)
                if "ru" in lang or item.get("is_original") or item.get("default") or not lang:
                    best_audio_link = item.get("url")
                    print(f"✅ [ArbiFlow]: Выбрана аудиодорожка: {lang if lang else 'original/default'}", flush=True)
                    break
            
            if not best_audio_link:
                best_audio_link = audio_items[0].get("url")
                print(f"🎵 [ArbiFlow]: Найдена отдельная аудиодорожка (первая в списке).", flush=True)

        # 3. Скачиваем и склеиваем
        if best_audio_link:
            print(f"📥 [ArbiFlow]: Скачиваем видео и аудио параллельно...", flush=True)
            
            import os
            import subprocess
            import concurrent.futures
            
            video_raw_path = dest_path.replace(".mp4", "_video_raw.mp4")
            audio_raw_path = dest_path.replace(".mp4", "_audio_raw.m4a")
            
            # Запускаем скачивание параллельно
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_v = executor.submit(download_file_fast, best_video_link, video_raw_path, method)
                future_a = executor.submit(download_file_fast, best_audio_link, audio_raw_path, method)
                
                v_success = future_v.result()
                a_success = future_a.result()
            
            if v_success and a_success:
                print(f"🎬 [ArbiFlow]: Склеиваем видео и аудио через FFmpeg...", flush=True)
                merge_cmd = [
                    "ffmpeg", "-y",
                    "-i", video_raw_path,
                    "-i", audio_raw_path,
                    "-c", "copy",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    dest_path
                ]
                try:
                    process = subprocess.run(merge_cmd, capture_output=True)
                    if process.returncode == 0:
                        print(f"✅ [ArbiFlow]: Склейка успешно завершена!", flush=True)
                        if os.path.exists(video_raw_path): os.remove(video_raw_path)
                        if os.path.exists(audio_raw_path): os.remove(audio_raw_path)
                        return True
                    else:
                        print(f"❌ [ArbiFlow]: Ошибка склейки FFmpeg: {process.stderr.decode('utf-8', errors='ignore')}", flush=True)
                except Exception as e:
                    print(f"❌ [ArbiFlow]: Ошибка запуска FFmpeg: {e}", flush=True)
            
            # Если склейка не удалась, пробуем просто вернуть видео (вдруг там есть звук)
            print(f"⚠️ [ArbiFlow]: Склейка не удалась, используем только видеофайл.", flush=True)
            if v_success and os.path.exists(video_raw_path):
                import shutil
                shutil.move(video_raw_path, dest_path)
                if os.path.exists(audio_raw_path) and os.path.exists(audio_raw_path): 
                    try: os.remove(audio_raw_path)
                    except: pass
                return True
            
            return False # Если ничего не скачалось
                
        else:
            print(f"⚠️ [ArbiFlow]: Отдельного аудио нет, качаем только видео (надеемся, что звук встроен).", flush=True)
            return download_file_fast(best_video_link, dest_path, method=method)

    except Exception as e:
        print(f"🔥 [ArbiFlow]: Ошибка продакшен-загрузки: {e}", flush=True)
        return False

async def handler(job):
    job_id = job.get("id")
    job_input = job.get("input", {})
    task = job_input.get("task")
    video_url = job_input.get("video_url")
    
    if not video_url or not task:
        return {"status": "error", "message": "Missing video_url or task"}

    # Setup paths
    os.makedirs(TEMP_PATH, exist_ok=True)
    os.makedirs(MODEL_PATH, exist_ok=True)
    
    input_video = os.path.join(TEMP_PATH, f"in_{job_id}.mp4")
    output_video = os.path.join(TEMP_PATH, f"out_{job_id}.mp4")
    
    # Files for cleanup
    cleanup_list = [input_video, output_video]
    
    try:
        is_url = job_input.get("is_url", False)
        
        # Skip direct download for tasks that handle it themselves (like ai_shorts with URL)
        if not (task == "ai_shorts" and is_url):
            download_file(video_url, input_video)
        
        is_image = job_input.get("is_image", False)
        if is_image:
            new_input = input_video.replace('.mp4', '.jpg')
            if os.path.exists(input_video):
                os.rename(input_video, new_input)
            input_video = new_input
            output_video = output_video.replace('.mp4', '.jpg')
            cleanup_list.extend([input_video, output_video])
            
        if task == "ai_subs":
            position = job_input.get("position", "bottom")
            ass_file = os.path.join(TEMP_PATH, f"subs_{job_id}.ass")
            cleanup_list.append(ass_file)
            
            model_instance = get_whisper_model()
            segments, _ = model_instance.transcribe(input_video, beam_size=5, word_timestamps=True, vad_filter=True)
            
            width, height = 1080, 1920
            try:
                probe = ffmpeg.probe(input_video)
                v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                if v_stream:
                    width, height = int(v_stream['width']), int(v_stream['height'])
            except: pass

            generate_ass_subtitles(list(segments), ass_file, position=position, width=width, height=height)

            ffmpeg.input(input_video).output(
                output_video, 
                vf=f"subtitles='{ass_file}':fontsdir='{FONT_DIR}'", 
                vcodec='libx264', acodec='copy', preset='ultrafast', crf=23
            ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                
        elif task == "ai_shorts":
            print(f"🎬 [ArbiFlow Worker]: Starting AI-Shorts task for {video_url}", flush=True)
            
            # 1. API Key DeepSeek (Хардкод по просьбе пользователя)
            deepseek_api_key = "sk-e6f8353356a149bdb6e10bb54c9e5609"
            print(f"🔑 [ArbiFlow Worker]: Using DeepSeek API Key (starts with: {deepseek_api_key[:4]}...)")
            
            is_url = job_input.get("is_url", False)
            
            # 1. Download if URL
            if is_url:
                print(f"📥 [ArbiFlow Worker]: Starting download process...", flush=True)
                
                # Попытка 1: Твой новый Cobalt VPS (Бесплатно, без лимитов)
                success = download_via_vps_cobalt(video_url, input_video)
                
                if not success:
                    print(f"🔄 [ArbiFlow]: Cobalt VPS не сработал. Пробую yt-dlp (Module)...", flush=True)
                    # Попытка 2: yt-dlp через прокси (Best for YouTube language selection)
                    success = download_via_ytdlp(video_url, input_video)
                
                if not success:
                    print(f"🔄 [ArbiFlow]: yt-dlp не сработал. Пробую RapidAPI + aria2c...", flush=True)
                    # Попытка 3: RapidAPI + aria2c (Fallback)
                    success = download_via_rapidapi(video_url, input_video, method="aria2c")

                # 4. Telegram Bots
                if not success:
                    print(f"🔄 [ArbiFlow]: RapidAPI failed. Falling back to Telegram Bots...", flush=True)
                    try:
                        from telegram_downloader.worker import download_via_telegram
                        success = await download_via_telegram(video_url, input_video)
                        if success:
                            print(f"✅ [ArbiFlow Worker]: Video downloaded successfully via Telegram Bots.", flush=True)
                    except Exception as e:
                        print(f"❌ [ArbiFlow Worker]: Telegram Bots failed: {e}", flush=True)
                        import traceback
                        traceback.print_exc()
                        success = False

                # 5. RapidAPI + requests
                if not success:
                    print(f"🔄 [ArbiFlow]: Telegram Bots failed. Falling back to RapidAPI + requests...", flush=True)
                    success = download_via_rapidapi(video_url, input_video, method="requests")
                
                if not success:
                    raise Exception("Все методы скачивания (Cobalt, yt-dlp, RapidAPI, Telegram, requests) завершились с ошибкой.")
                else:
                    if success and not os.path.exists(input_video):
                        # Just in case
                        pass
            
            # 2. Transcribing (с подробным дебагом)
            print(f"📝 [ArbiFlow Worker]: Transcribing video...", flush=True)
            model_instance = get_whisper_model()

            # === НОВОЕ: проверка файла перед транскрипцией ===
            print(f"🔍 [ArbiFlow]: Проверяем файл {os.path.basename(input_video)} ({os.path.getsize(input_video)/1024/1024:.1f} MB)", flush=True)
            try:
                probe = ffmpeg.probe(input_video)
                audio_streams = [s for s in probe['streams'] if s['codec_type'] == 'audio']
                video_streams = [s for s in probe['streams'] if s['codec_type'] == 'video']
                print(f"✅ FFmpeg probe OK: {len(video_streams)} видео, {len(audio_streams)} аудио потоков", flush=True)
                if not audio_streams:
                    raise Exception("В видео отсутствует аудио-трек!")
            except Exception as probe_err:
                print(f"❌ [ArbiFlow]: FFmpeg probe failed: {probe_err}", flush=True)
                raise Exception(f"Файл повреждён или не содержит аудио: {probe_err}")

            # === Сама транскрипция с защитой ===
            try:
                print(f"🚀 [ArbiFlow]: Запускаем Whisper transcribe (beam=5, word_timestamps=False, vad_filter=True)...", flush=True)
                segments, info = model_instance.transcribe(
                    input_video,
                    beam_size=5,
                    word_timestamps=False,      # оставляем False — он спасал от tuple index
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500)
                )
                print(f"✅ [ArbiFlow]: Whisper transcribe setup OK. Язык: {info.language} (prob {info.language_probability:.2f})", flush=True)
            except Exception as transcribe_err:
                print(f"❌ [ArbiFlow]: ОШИБКА В model.transcribe(): {transcribe_err}", flush=True)
                import traceback
                traceback.print_exc()
                raise Exception(f"Whisper transcribe failed: {transcribe_err}")

            # === Итерация сегментов (была главной точкой краша раньше) ===
            transcript = ""
            try:
                print(f"⏳ [ArbiFlow]: Starting generation from segments...", flush=True)
                segments_list = list(segments)   # <- здесь раньше был tuple index out of range
                print(f"✅ [ArbiFlow]: Получено {len(segments_list)} сегментов", flush=True)
            except Exception as e:
                print(f"❌ [ArbiFlow]: Whisper failed during iteration: {e}", flush=True)
                import traceback
                traceback.print_exc()
                raise Exception(f"Whisper transcription error: {e}")
            
            if not segments_list:
                raise Exception("No speech segments detected in the video.")

            for segment in segments_list:
                transcript += f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {segment.text}\n"
            
            # 3. Multi-Pass Pipeline Analysis (Smart Chunking by Pauses)
            print(f"🧩 [ArbiFlow Worker]: Structuring transcript into semantic blocks by pauses...", flush=True)
            import concurrent.futures
            
            blocks = []
            current_block_segs = []
            
            # Безопасное получение времени начала
            block_start_time = segments_list[0].start if segments_list else 0

            MIN_BLOCK_DURATION = 120  # Минимальный блок 2 минуты
            MAX_BLOCK_DURATION = 420  # Максимальный блок 7 минут
            PAUSE_THRESHOLD = 3.0     # Пауза 3+ секунды считается сменой сцены

            for i, seg in enumerate(segments_list):
                current_block_segs.append(seg)
                current_duration = seg.end - block_start_time

                should_split = False
                if i < len(segments_list) - 1:
                    next_seg = segments_list[i+1]
                    gap = next_seg.start - seg.end

                    # 1. Если блок достиг минимума (2 мин) И есть длинная пауза (>3 сек) -> режем
                    if gap >= PAUSE_THRESHOLD and current_duration >= MIN_BLOCK_DURATION:
                        should_split = True
                    # 2. Если блок слишком большой (почти 7 мин) -> режем на любой микро-паузе (>1 сек)
                    elif current_duration >= MAX_BLOCK_DURATION and gap >= 1.0:
                        should_split = True
                    # 3. Жесткий лимит (8 мин) -> режем в любом случае, чтобы не перегрузить ИИ
                    elif current_duration >= MAX_BLOCK_DURATION + 60:
                        should_split = True
                else:
                    should_split = True # Последний сегмент

                if should_split and current_block_segs:
                    text = ""
                    for s in current_block_segs:
                        text += f"[{format_timestamp(s.start)} - {format_timestamp(s.end)}] {s.text}\n"
                    blocks.append({
                        "id": len(blocks) + 1,
                        "text": text
                    })
                    current_block_segs = []
                    if i < len(segments_list) - 1:
                        block_start_time = segments_list[i+1].start
                
            print(f"📦 [ArbiFlow Worker]: Created {len(blocks)} semantic blocks for local analysis.", flush=True)
            
            all_scenes = []
            
            def analyze_block(block):
                prompt = f"""
Ты — профессиональный режиссер монтажа вирусных Shorts / Reels.
Твоя задача — найти максимум сильных кандидатов для клипов.

Это этап генерации кандидатов (MAX RECALL).
Нужно найти 15–25 потенциальных сцен уровня >=7.

==================================================
АЛГОРИТМ:

1. Прочитай транскрипт полностью.
2. Игнорируй:
   - трейлерные нарезки
   - анонсы “в этом выпуске”
   - рекламные вставки
   - музыкальные вступления
3. Найди фразы-хуки:
   - конфликт
   - провокация
   - неожиданный факт
   - сильный инсайт
   - эмоциональный всплеск
   - резкое утверждение
4. Для каждого кандидата проверь:
   ✓ Начинается с речи (не музыка)
   ✓ Нет пауз >3 секунд
   ✓ Длительность 20–90 секунд
   ✓ Мысль завершена
   ✓ Нет резких перескоков тем
   ✓ Можно понять без контекста

Если пункт не выполнен — не включай.

==================================================
VIRAL_SCORE (1–10):

10 — сильный шок / конфликт
8–9 — мощный инсайт / эмоция
7 — хороший удерживающий момент
<7 — не возвращать

==================================================
Верни от 15 до 25 сцен (если есть).

==================================================
ТРАНСКРИПТ:
{block['text']}

==================================================
ВЫВОД СТРОГО В JSON:

{{
  "candidates": [
    {{
      "start_time": 12.5,
      "end_time": 55.0,
      "viral_score": 8,
      "emotion_type": "конфликт",
      "hook_text": "Первая фраза сцены",
      "scene_topic": "Коротко о чем сцена"
    }}
  ]
}}
"""
                headers = {
                    "Authorization": f"Bearer {deepseek_api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You output ONLY valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"}
                }
                try:
                    resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload, timeout=60)
                    resp.raise_for_status()
                    
                    data = resp.json()
                    choices = data.get('choices', [])
                    if not choices:
                        print(f"⚠️ [ArbiFlow]: AI вернул пустой ответ (no choices) для блока {block['id']}")
                        return []
                        
                    content = choices[0].get('message', {}).get('content', '')
                    
                    # БЕЗОПАСНЫЙ ПАРСИНГ MARKDOWN
                    if "```json" in content:
                        parts = content.split("```json")
                        if len(parts) > 1:
                            content = parts[1].split("```")[0].strip()
                    elif "```" in content:
                        parts = content.split("```")
                        if len(parts) > 1:
                            content = parts[1].split("```")[0].strip()
                    
                    # Пытаемся распарсить как чистый JSON
                    try:
                        return json.loads(content).get("candidates", [])
                    except json.JSONDecodeError:
                        print(f"⚠️ [ArbiFlow]: Не удалось распарсить JSON из текста блока {block['id']}: {content[:100]}...")
                        return []
                except Exception as e:
                    print(f"⚠️ Block {block['id']} analysis failed: {e}")
                    return []

            print(f"🔍 [ArbiFlow Worker]: Running local analysis (multi-threading)...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                results = executor.map(analyze_block, blocks)
                for res in results:
                    if isinstance(res, list):
                        all_scenes.extend(res)
                        
            # Filter valid scenes and remove overlaps
            all_candidates = []
            for s in all_scenes:
                if 'start_time' in s and 'end_time' in s:
                    try:
                        s['start_time'] = float(s['start_time'])
                        s['end_time'] = float(s['end_time'])
                        if s['end_time'] - s['start_time'] >= 20:
                            all_candidates.append(s)
                    except: pass
            
            # 1. Сортируем по viral_score убыванию
            all_candidates.sort(key=lambda x: x.get('viral_score', 0), reverse=True)
            
            # 2. Убираем пересечения (оставляем более высокий viral_score)
            valid_scenes = []
            for cand in all_candidates:
                overlap = False
                for selected in valid_scenes:
                    # Проверка на пересечение интервалов
                    if not (cand['end_time'] <= selected['start_time'] or cand['start_time'] >= selected['end_time']):
                        overlap = True
                        break
                if not overlap:
                    valid_scenes.append(cand)
                    
            print(f"🎯 [ArbiFlow Worker]: Found {len(valid_scenes)} non-overlapping candidates. Running global selection...", flush=True)
            
            if not valid_scenes:
                raise Exception("No valid scenes found in the video.")
                
            # PASS B: FINAL SELECTION
            top_scenes = valid_scenes[:40] # Берем топ-40 для финального отбора
            
            global_prompt = f"""
Ты — главный редактор коротких видео.
Твоя задача — из списка кандидатов выбрать ровно 10
максимально сильных и разнообразных клипов.

Это финальный этап (QUALITY MODE).

==================================================
ТРЕБОВАНИЯ:

1. Ровно 10 сцен.
2. Без пересечений по времени.
3. Разные emotion_type (по возможности).
4. Только сцены с полной драматургией:
   хук → развитие → вывод.
5. Сцены должны быть самостоятельными.
6. Не выбирать несколько сцен на одну и ту же мысль.

==================================================
Приоритет при выборе:

1. Более высокий viral_score
2. Завершенность мысли
3. Сильный хук в первые 2 секунды
4. Разнообразие тем и эмоций

==================================================
КАНДИДАТЫ:
{json.dumps(top_scenes, ensure_ascii=False, indent=2)}

==================================================
ВЫВОД СТРОГО В JSON:

{{
  "final_scenes": [
    {{
      "start_time": 12.5,
      "end_time": 55.0,
      "viral_score": 9,
      "emotion_type": "шок",
      "hook_text": "Первая фраза сцены",
      "cover_title": "Кликабельный заголовок до 5 слов",
      "viral_reason": "Почему это зайдет (до 10 слов)"
    }}
  ]
}}
"""
            
            headers = {
                "Authorization": f"Bearer {deepseek_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You output ONLY valid JSON."},
                    {"role": "user", "content": global_prompt}
                ],
                "response_format": {"type": "json_object"}
            }
            try:
                resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload, timeout=60)
                resp.raise_for_status()
                
                data = resp.json()
                choices = data.get('choices', [])
                if not choices:
                    print(f"⚠️ [ArbiFlow]: AI вернул пустой ответ (no choices) для глобального отбора")
                    clips_data = valid_scenes[:10]
                else:
                    content = choices[0].get('message', {}).get('content', '')
                    
                    # БЕЗОПАСНЫЙ ПАРСИНГ MARKDOWN
                    if "```json" in content:
                        parts = content.split("```json")
                        if len(parts) > 1:
                            content = parts[1].split("```")[0].strip()
                    elif "```" in content:
                        parts = content.split("```")
                        if len(parts) > 1:
                            content = parts[1].split("```")[0].strip()
                    
                    try:
                        final_data = json.loads(content)
                        clips_data = final_data.get("final_scenes", [])
                        if not clips_data:
                            clips_data = valid_scenes[:10]
                    except json.JSONDecodeError:
                        print(f"⚠️ [ArbiFlow]: Не удалось распарсить JSON из глобального отбора: {content[:100]}...")
                        clips_data = valid_scenes[:10]
            except Exception as e:
                print(f"❌ [ArbiFlow Worker]: Global selection failed: {e}. Falling back to top 10 scenes.", flush=True)
                clips_data = valid_scenes[:10]
                
            # 4. Validation (Remove overlaps locally)
            clips_data.sort(key=lambda x: x['start_time'])
            validated_clips = []
            last_end = -1
            for clip in clips_data:
                if clip['start_time'] >= last_end:
                    validated_clips.append(clip)
                    last_end = clip['end_time']
                    
            clips_data = validated_clips
            print(f"✅ [ArbiFlow Worker]: Selected {len(clips_data)} final clips.", flush=True)
            
            # 4. Cut and Crop
            output_zip = os.path.join(TEMP_PATH, f"shorts_{job_id}.zip")
            cleanup_list.append(output_zip)
            
            descriptions = []
            
            print(f"✂️ [ArbiFlow Worker]: Creating ZIP and processing clips...", flush=True)
            
            def process_clip(i, clip):
                start = clip['start_time']
                end = clip['end_time']
                cover_title = clip.get('cover_title', 'Viral Clip')
                scene_topic = clip.get('scene_topic', '')
                viral_reason = clip.get('viral_reason', '')
                viral_score = clip.get('viral_score', 10)
                emotion_type = clip.get('emotion_type', 'Shorts')
                
                clip_filename = f"clip_{i+1}.mp4"
                clip_path = os.path.join(TEMP_PATH, clip_filename)
                temp_extract_path = os.path.join(TEMP_PATH, f"temp_extract_{i+1}.mp4")
                
                print(f"🎬 [ArbiFlow Worker]: Processing clip {i+1}: {start}s - {end}s", flush=True)
                try:
                    try:
                        probe = ffmpeg.probe(input_video)
                        v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                        if not v_stream:
                            raise Exception("No video stream found in the file.")
                        width = int(v_stream['width'])
                        height = int(v_stream['height'])
                    except Exception as e:
                        print(f"⚠️ [ArbiFlow Worker]: FFmpeg probe failed for clip {i+1}: {e}", file=sys.stderr)
                        # Fallback to standard 1080p if probe fails
                        width, height = 1920, 1080
                        
                    # ШАГ 1: Быстро вырезаем кусок для OpenCV (используем x264, чтобы избежать проблем с AV1 и stream copy)
                    ffmpeg.input(input_video, ss=start, t=end-start).output(
                        temp_extract_path, vcodec='libx264', preset='ultrafast', crf=28
                    ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                    
                    # ШАГ 2: Находим центр лица на уже вырезанном маленьком кусочке (start_time = 0)
                    face_x = get_face_center_x(temp_extract_path, 0, end-start, width)
                    
                    if width > height:
                        # Горизонтальное видео -> Делаем "Подкаст формат" (Размытый фон + широкий кроп 4:3)
                        # Это решает проблему "слишком близкого лица" и "обрезанных краев"
                        crop_w = int(height * 4 / 3)
                        if crop_w > width: crop_w = width
                        if crop_w % 2 != 0: crop_w -= 1
                        
                        # Рассчитываем x_offset так, чтобы лицо было в центре широкого кропа
                        x_offset = face_x - (crop_w // 2)
                        x_offset = max(0, min(x_offset, width - crop_w))
                        
                        complex_filter = (
                            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:20[bg];"
                            f"[0:v]crop={crop_w}:{height}:{x_offset}:0,scale=1080:-1[fg];"
                            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                        )
                        
                        # ШАГ 3: Финальный рендер с фильтром напрямую из оригинального видео
                        ffmpeg.input(input_video, ss=start, t=end-start).output(
                            clip_path,
                            filter_complex=complex_filter,
                            vcodec='libx264', acodec='aac', preset='fast', crf=18
                        ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                    else:
                        # Уже вертикальное видео -> Просто подгоняем под 1080x1920
                        complex_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                        ffmpeg.input(input_video, ss=start, t=end-start).output(
                            clip_path,
                            vf=complex_filter,
                            vcodec='libx264', acodec='aac', preset='fast', crf=18
                        ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                    
                    # Удаляем временный файл
                    if os.path.exists(temp_extract_path):
                        os.remove(temp_extract_path)
                        
                    desc = f"🎬 {i+1}.mp4 [{emotion_type}] - {cover_title}\n📝 Тема: {scene_topic}\n🔥 Оценка: {viral_score}/10\n💡 Почему залетит: {viral_reason}\n"
                    return clip_path, clip_filename, desc, i
                except Exception as e:
                    print(f"❌ [ArbiFlow Worker]: Error processing clip {i+1}: {e}", file=sys.stderr, flush=True)
                    if os.path.exists(temp_extract_path):
                        try: os.remove(temp_extract_path)
                        except: pass
                    return None, None, None, i

            with zipfile.ZipFile(output_zip, 'w') as zipf:
                # Запускаем обработку клипов параллельно (до 3 потоков, чтобы не перегрузить CPU/RAM)
                results_data = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    futures = [executor.submit(process_clip, i, clip) for i, clip in enumerate(clips_data)]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            result = future.result()
                            if result and len(result) == 4:
                                clip_path, clip_filename, desc, idx = result
                                if clip_path and os.path.exists(clip_path):
                                    zipf.write(clip_path, arcname=clip_filename)
                                    results_data.append((idx, desc))
                                    cleanup_list.append(clip_path)
                            else:
                                print(f"⚠️ [ArbiFlow Worker]: process_clip returned unexpected result: {result}", file=sys.stderr)
                        except Exception as e:
                            print(f"❌ [ArbiFlow Worker]: Exception during future.result(): {e}", file=sys.stderr)
                            
                # Сортируем описания по порядку клипов (1, 2, 3...)
                results_data.sort(key=lambda x: x[0])
                for _, desc in results_data:
                    descriptions.append(desc)
                        
                # Add descriptions.txt
                desc_path = os.path.join(TEMP_PATH, "descriptions.txt")
                cleanup_list.append(desc_path)
                with open(desc_path, 'w', encoding='utf-8') as f:
                    f.write("✅ Нарезка завершена! Ваши клипы:\n\n" + "\n".join(descriptions))
                zipf.write(desc_path, arcname="descriptions.txt")
                
            output_video = output_zip
            result_url = upload_to_catbox(output_video)
            return {
                "status": "success", 
                "result_url": result_url, 
                "message": "✅ Нарезка завершена! Ваши клипы:\n\n" + "\n".join(descriptions)
            }
            
        elif task == "ai_voice":
            print(f"🗣️ [ArbiFlow Worker]: Starting AI-Voice task...", flush=True)
            voice_text = job_input.get("voice_text")
            output_audio = output_video.replace('.mp4', '.mp3')
            cleanup_list.append(output_audio)
            await edge_tts.Communicate(voice_text, "ru-RU-SvetlanaNeural").save(output_audio)
            output_video = output_audio
                
        elif task == "ai_translate":
            shutil.copy(input_video, output_video)
            
        result_url = upload_to_catbox(output_video)
        return {"status": "success", "result_url": result_url}

    except ffmpeg.Error as fe:
        err_msg = fe.stderr.decode() if fe.stderr else str(fe)
        return {"status": "error", "message": f"FFmpeg Error: {err_msg}", "traceback": traceback.format_exc()}
    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"❌ [ArbiFlow Worker]: НЕОЖИДАННАЯ ОШИБКА В handler: {err_msg}", flush=True)
        traceback.print_exc()
        return {"status": "error", "message": err_msg, "traceback": traceback.format_exc()}
    finally:
        for f in cleanup_list:
            if f and os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == "__main__":
    print("🚀 [ArbiFlow Worker]: Initialization complete. Starting RunPod serverless...", flush=True)
    runpod.serverless.start({"handler": handler})
