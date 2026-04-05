import os
import sys
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
    import yt_dlp
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

def download_via_rapidapi(video_url, dest_path):
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
        # Запрос метаданных видео
        response = requests.get(rapid_url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()

        # Список доступных комбинированных ссылок (видео + аудио)
        items = data.get("videos", {}).get("items", [])
        if not items:
            print(f"❌ [ArbiFlow]: API не вернул доступных ссылок для ID: {video_id}", flush=True)
            return False
            
        # 🎯 Поиск 1080p
        final_link = None
        for item in items:
            if item.get("quality") == "1080p":
                final_link = item.get("url")
                print(f"💎 [ArbiFlow]: Качество 1080p подтверждено.", flush=True)
                break
        
        # Фолбэк: если 1080p нет, берем самую первую (лучшую) ссылку
        if not final_link:
            final_link = items[0].get("url")
            print(f"⚠️ [ArbiFlow]: 1080p недоступно, выбрано качество: {items[0].get('quality')}", flush=True)

        print(f"🔗 [ArbiFlow]: Direct link (first 100 chars): {final_link[:100]}...", flush=True)

        # 3. Загрузка файла на RunPod
        print(f"📥 [ArbiFlow]: Начинаю загрузку в {dest_path}...", flush=True)
        
        # Пытаемся использовать aria2c для многопоточного скачивания (максимальная скорость)
        download_success = False
        try:
            import subprocess
            # Проверяем наличие aria2c
            subprocess.run(['aria2c', '--version'], capture_output=True, check=True)
            
            print(f"🚀 [ArbiFlow]: Использование aria2c для многопоточного скачивания (16 потоков)...", flush=True)
            # -x16 (соединений на сервер), -s16 (частей на файл), -k1M (минимальный размер части)
            cmd = [
                'aria2c', 
                '--header', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--header', 'Referer: https://www.youtube.com/',
                '-x', '16', 
                '-s', '16', 
                '-j', '16', 
                '-k', '1M',
                '--file-allocation=none', 
                '--summary-interval=0',
                '--check-certificate=false',
                '--retry-wait=2',
                '--max-tries=5',
                '-o', os.path.basename(dest_path),
                '-d', os.path.dirname(dest_path),
                final_link
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            download_success = True
        except Exception as e:
            print(f"⚠️ [ArbiFlow]: aria2c недоступен или произошла ошибка, использую стандартный метод. ({e})", flush=True)
            
        if not download_success:
            # Стандартный метод (fallback)
            headers_dl = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.youtube.com/'
            }
            with requests.get(final_link, stream=True, timeout=60, headers=headers_dl, verify=False) as r:
                r.raise_for_status()
                with open(dest_path, 'wb') as f:
                    # Качаем кусками по 1МБ
                    for chunk in r.iter_content(chunk_size=1024*1024): 
                        if chunk:
                            f.write(chunk)
        
        # Проверка, что файл реально создался и не пустой
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            print(f"✅ [ArbiFlow]: Файл успешно сохранен ({round(os.path.getsize(dest_path)/1024/1024, 2)} MB)", flush=True)
            return True
        else:
            print("❌ [ArbiFlow]: Файл не был сохранен или он пустой", flush=True)
            return False

    except Exception as e:
        print(f"🔥 [ArbiFlow]: Критическая ошибка при скачивании через RapidAPI: {e}", flush=True)
        return False

def handler(job):
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
                
                # 1. Сначала пробуем RapidAPI (самый надежный платный метод)
                success = download_via_rapidapi(video_url, input_video)
                
                if not success:
                    print(f"⚠️ [ArbiFlow Worker]: RapidAPI failed. Falling back to verified Android-client...", flush=True)
                    
                    # --- УМНАЯ ПРОВЕРКА КУКОВ ---
                    cookies_content = job_input.get("cookies")
                    cookies_path = None
                    
                    if cookies_content:
                        # Если куки пришли в запросе от бота
                        cookies_path = os.path.join(TEMP_PATH, f"cookies_{job_id}.txt")
                        with open(cookies_path, "w") as f:
                            f.write(cookies_content)
                        print(f"🍪 [ArbiFlow Worker]: Using cookies from job input. (Size: {len(cookies_content)} bytes)", flush=True)
                        if not cookies_content.strip().startswith("# Netscape"):
                            print("⚠️ [ArbiFlow Worker]: Cookies might not be in Netscape format! (Should start with # Netscape)", flush=True)
                    else:
                        # Если в запросе нет, ищем файл cookies.txt в папке с кодом (внутри Docker)
                        local_cookies = os.path.join(os.path.dirname(__file__), "cookies.txt")
                        # Также проверим в корне /app
                        root_cookies = "/app/cookies.txt"
                        
                        if os.path.exists(local_cookies):
                            cookies_path = local_cookies
                            print(f"🍪 [ArbiFlow Worker]: Using local cookies.txt from handler folder.", flush=True)
                        elif os.path.exists(root_cookies):
                            cookies_path = root_cookies
                            print(f"🍪 [ArbiFlow Worker]: Using local cookies.txt from /app root.", flush=True)

                    ydl_opts = {
                        'format': 'bestvideo[height<=1440]+bestaudio/best[height<=1440]',
                        'format_sort': ['res:1440', 'ext:mp4:m4a'],
                        'outtmpl': input_video,
                        'quiet': True,
                        'no_warnings': True,
                        'merge_output_format': 'mp4',
                        'nocheckcertificate': True,
                        'no_color': True,
                        'youtube_skip_dash_manifest': True,
                        'cachedir': False,
                        'geo_bypass': True,
                        'concurrent_fragment_downloads': 10,
                        # Настройки из успешного теста:
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['ios', 'android', 'web'],
                                'player_skip': ['web_embedded-player_mechanism']
                            }
                        },
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                    
                    # Пытаемся добавить aria2c в yt-dlp для ускорения фрагментов
                    try:
                        import subprocess
                        subprocess.run(['aria2c', '--version'], capture_output=True, check=True)
                        ydl_opts['external_downloader'] = 'aria2c'
                        ydl_opts['external_downloader_args'] = ['-x', '16', '-s', '16', '-k', '1M']
                        print(f"🚀 [ArbiFlow Worker]: Aria2c enabled for yt-dlp fallback.", flush=True)
                    except:
                        pass
                    
                    if cookies_path:
                        ydl_opts['cookiefile'] = cookies_path
                        print(f"✅ [ArbiFlow Worker]: Cookies applied for extra auth.", flush=True)

                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([video_url])
                        print(f"✅ [ArbiFlow Worker]: Video downloaded successfully via fallback.", flush=True)
                    finally:
                        # Удаляем только временный файл, созданный из запроса
                        if cookies_content and cookies_path and os.path.exists(cookies_path) and "cookies_" in cookies_path:
                            try:
                                os.remove(cookies_path)
                            except: pass
                else:
                    print(f"✅ [ArbiFlow Worker]: Video downloaded successfully via RapidAPI.", flush=True)
            
            # 2. Transcribe
            print(f"📝 [ArbiFlow Worker]: Transcribing video...", flush=True)
            model_instance = get_whisper_model()
            segments, info = model_instance.transcribe(input_video, beam_size=5, word_timestamps=True, vad_filter=True)
            
            transcript = ""
            segments_list = list(segments)
            
            if not segments_list:
                raise Exception("No speech segments detected in the video.")

            for segment in segments_list:
                transcript += f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {segment.text}\n"
            
            # 3. Multi-Pass Pipeline Analysis (Smart Chunking by Pauses)
            print(f"🧩 [ArbiFlow Worker]: Structuring transcript into semantic blocks by pauses...", flush=True)
            import concurrent.futures
            
            blocks = []
            current_block_segs = []
            
            block_start_time = segments_list[0].start

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
                Ты — строгий режиссер монтажа вирусных Shorts/Reels. Твоя задача — хирургически точно вырезать самые "сочные" моменты из транскрипта.

                КРИТЕРИИ ИДЕАЛЬНОЙ СЦЕНЫ (ЖЕСТКИЕ ПРАВИЛА):
                1. ХИРУРГИЧЕСКАЯ ТОЧНОСТЬ ТАЙМКОДОВ: Сцена должна начинаться РОВНО с того слова, где начинается интересная тема (Хук). Никакого "смоллтока", приветствий или мычания до этого! Заканчиваться сцена должна РОВНО на логичном выводе или панчлайне.
                2. ЦЕЛЬНАЯ ИСТОРИЯ: Внутри отрезка должна быть полностью раскрыта одна мысль. Если тема только началась и блок закончился — НЕ БЕРИ эту сцену.
                3. АНТИ-РЕКЛАМА И АНТИ-ПУСТОТА: Если между репликами (таймкодами) есть разрыв более 5-7 секунд — это реклама или музыкальная пауза. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО включать такие разрывы внутрь сцены!
                4. КРАТКОСТЬ: Описание (scene_topic) и причина (viral_reason) должны быть ОЧЕНЬ короткими — максимум 1 предложение (до 10 слов).
                5. Длительность: строго от 20 до 90 секунд.

                Найди от 0 до 3 лучших самостоятельных сцен. Если в блоке только скучный треп, реклама или обрывки — смело возвращай пустой массив. Лучше 0 сцен, чем плохая сцена.
                
                ТРАНСКРИПТ:
                {block['text']}
                
                ВЫВОД СТРОГО В JSON:
                {{
                  "scenes": [
                    {{
                      "start_time": 12.5,
                      "end_time": 55.0,
                      "viral_score": 9,
                      "emotion_type": "юмор/дилемма/шок/инсайт",
                      "viral_reason": "Коротко: почему это зацепит (до 10 слов)",
                      "hook_text": "Точная первая фраза, с которой начинается клип",
                      "cover_title": "Кликабельный заголовок (до 5 слов)",
                      "scene_topic": "Коротко: о чем клип (до 10 слов)"
                    }}
                  ]
                }}
                Если подходящих сцен нет, верни {{"scenes": []}}.
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
                        return json.loads(content).get("scenes", [])
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
                        
            # Filter valid scenes
            valid_scenes = []
            for s in all_scenes:
                if 'start_time' in s and 'end_time' in s:
                    try:
                        s['start_time'] = float(s['start_time'])
                        s['end_time'] = float(s['end_time'])
                        if s['end_time'] - s['start_time'] >= 20:
                            valid_scenes.append(s)
                    except: pass
                    
            print(f"🎯 [ArbiFlow Worker]: Found {len(valid_scenes)} potential scenes. Running global selection...", flush=True)
            
            if not valid_scenes:
                raise Exception("No valid scenes found in the video.")
                
            valid_scenes.sort(key=lambda x: x.get('viral_score', 0), reverse=True)
            top_scenes = valid_scenes[:20]
            
            global_prompt = f"""
            Вот список лучших потенциальных вирусных сцен из видео:
            {json.dumps(top_scenes, ensure_ascii=False, indent=2)}
            
            Твоя задача: выбрать от 3 до 8 САМЫХ ЛУЧШИХ сцен для публикации.
            
            ПРАВИЛА:
            1. Сцены НЕ ДОЛЖНЫ пересекаться по времени (start_time и end_time).
            2. Выбирай самые разнообразные по темам.
            3. Отдавай приоритет высокому viral_score.
            
            ВЫВОД СТРОГО В JSON:
            {{
              "clips": [
                {{
                  "start_time": 12.5,
                  "end_time": 55.0,
                  "cover_title": "...",
                  "scene_topic": "...",
                  "viral_reason": "...",
                  "emotion_type": "...",
                  "viral_score": 8
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
                    clips_data = top_scenes[:5]
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
                        clips_data = final_data.get("clips", [])
                    except json.JSONDecodeError:
                        print(f"⚠️ [ArbiFlow]: Не удалось распарсить JSON из глобального отбора: {content[:100]}...")
                        clips_data = top_scenes[:5]
            except Exception as e:
                print(f"❌ [ArbiFlow Worker]: Global selection failed: {e}. Falling back to top 5 scenes.", flush=True)
                clips_data = top_scenes[:5]
                
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
            asyncio.run(edge_tts.Communicate(voice_text, "ru-RU-SvetlanaNeural").save(output_audio))
            output_video = output_audio
                
        elif task == "ai_translate":
            shutil.copy(input_video, output_video)
            
        result_url = upload_to_catbox(output_video)
        return {"status": "success", "result_url": result_url}

    except ffmpeg.Error as fe:
        err_msg = fe.stderr.decode() if fe.stderr else str(fe)
        return {"status": "error", "message": f"FFmpeg Error: {err_msg}", "traceback": traceback.format_exc()}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}
    finally:
        for f in cleanup_list:
            if f and os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == "__main__":
    print("🚀 [ArbiFlow Worker]: Initialization complete. Starting RunPod serverless...", flush=True)
    runpod.serverless.start({"handler": handler})
