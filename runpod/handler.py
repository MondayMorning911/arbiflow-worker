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
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if torch.cuda.is_available() else "int8"
            print(f"🖥️ [ArbiFlow Worker]: Using device: {device} ({compute_type})", flush=True)
            
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

def get_face_detector():
    global mp_face_detection
    if mp_face_detection is None:
        import mediapipe as mp
        mp_face_detection = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    return mp_face_detection

def get_face_center_x(video_path, start_time, duration, original_width):
    try:
        detector = get_face_detector()
        cap = cv2.VideoCapture(video_path)
        
        # Берем кадры каждые 2 секунды для более точного отслеживания
        sample_interval = 2.0
        num_samples = max(3, int(duration / sample_interval))
        sample_times = [start_time + i * (duration / num_samples) for i in range(num_samples)]
        
        x_coords = []
        
        for t in sample_times:
            if t < 0: t = 0
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret: continue
            
            results = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if results.detections:
                # Берем самое уверенное лицо
                best_detection = max(results.detections, key=lambda d: d.score[0])
                bbox = best_detection.location_data.relative_bounding_box
                center_x = (bbox.xmin + bbox.width / 2) * original_width
                x_coords.append(center_x)
        
        cap.release()
        
        if x_coords:
            # Используем медиану, чтобы исключить резкие движения или ошибки детекции
            x_coords.sort()
            median_x = x_coords[len(x_coords) // 2]
            print(f"🎯 [ArbiFlow Worker]: Face detected in {len(x_coords)}/{len(sample_times)} frames. Median X: {median_x:.1f}")
            return int(median_x)
            
        print("👤 [ArbiFlow Worker]: No faces detected in any frames. Falling back to center.")
        return original_width // 2
    except Exception as e:
        print(f"⚠️ [ArbiFlow Worker]: Face detection failed: {e}")
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
                print(f"📥 [ArbiFlow Worker]: Downloading video via yt-dlp...", flush=True)
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': input_video,
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {'youtube': ['player_client=android']},
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            
            # 2. Transcribe
            print(f"📝 [ArbiFlow Worker]: Transcribing video...", flush=True)
            model_instance = get_whisper_model()
            segments, info = model_instance.transcribe(input_video, beam_size=5, word_timestamps=True, vad_filter=True)
            
            transcript = ""
            segments_list = list(segments)
            for segment in segments_list:
                transcript += f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {segment.text}\n"
            
            if not transcript:
                raise Exception("Transcript is empty. Could not detect speech.")
                
            # 3. Analyze with DeepSeek
            print(f"🧠 [ArbiFlow Worker]: Analyzing transcript with DeepSeek...", flush=True)
            
            prompt = f"""
            Ты — профессиональный редактор вирусных коротких видео (TikTok, Reels, Shorts).
            Ты превращаешь длинные YouTube-видео в самостоятельные клипы,
            которые можно сразу публиковать.

            Твоя задача — найти 3 САМЫХ сильных фрагмента.

            --------------------------------------------------
            ШАГ 1. Определи тип контента:
            - подкаст
            - интервью
            - образовательный
            - развлекательный
            - реакция
            - личная история
            - другое

            Адаптируй стратегию поиска под тип.

            --------------------------------------------------
            ШАГ 2. Найди 3 вирусных фрагмента.

            ⚠️ КРИТИЧЕСКОЕ ПРАВИЛО:
            Фрагмент должен быть ЦЕЛЬНОЙ МИНИ-ИСТОРИЕЙ.

            Каждый фрагмент обязан содержать:
            1. Хук (сильное начало в первые 3–5 секунд)
            2. Развитие мысли / конфликт / нарастающее напряжение
            3. Кульминацию или главный инсайт
            4. Логическое завершение

            Если нет всех 4 элементов — фрагмент НЕ подходит.

            Запрещено:
            - начинать с середины предложения
            - заканчивать на незавершённой мысли
            - выбирать куски без контекста
            - брать просто «интересная фразу»

            --------------------------------------------------
            КРИТЕРИИ ВИРУСНОСТИ (по приоритету):

            1. Сильная эмоция (шок, страх, злость, восторг)
            2. Конфликт или провокация
            3. Неожиданный поворот
            4. Личный опыт с выводом
            5. Практический инсайт
            6. Потенциал вызвать комментарии

            --------------------------------------------------
            ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ:

            - Длительность: 25–55 секунд
            - Понятен без просмотра всего видео
            - Самодостаточный смысл
            - Законченная мысль

            --------------------------------------------------
            SELF-CHECK (обязательно перед финальным выбором):

            Спроси себя:
            - Захочу ли я досмотреть это до конца?
            - Есть ли здесь напряжение?
            - Есть ли завершённость?
            Если нет — исключи фрагмент.

            Если в видео нет сильных фрагментов — верни [].

            --------------------------------------------------

            ТРАНСКРИПТ:
            {transcript}

            --------------------------------------------------

            ВЫВОД СТРОГО В ФОРМАТЕ JSON-ОБЪЕКТА (обязательно используй ключ "clips"):

            {{
              "clips": [
                {{
                  "content_type": "определённый тип",
                  "start_time": 15.5,
                  "end_time": 45.0,
                  "hook_text": "первые 5–12 слов фрагмента",
                  "cover_title": "короткий мощный заголовок для обложки (до 8 слов)",
                  "description_for_post": "2–3 предложения для описания под видео",
                  "viral_reason": "почему это удержит зрителя",
                  "emotion_trigger": "шок | конфликт | инсайт | драма | юмор | провокация",
                  "format_type": "сторителлинг | инсайт | обучение | конфликт | признание"
                }}
              ]
            }}

            Никакого текста вне JSON.
            """
            
            try:
                headers = {
                    "Authorization": f"Bearer {deepseek_api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a professional viral video editor. Respond ONLY with valid JSON in Russian."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"}
                }
                
                ds_response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload, timeout=60)
                ds_response.raise_for_status()
                
                ds_data = ds_response.json()
                content = ds_data['choices'][0]['message']['content']
                
                # DeepSeek might return JSON inside markdown blocks or just raw JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                
                clips_data = json.loads(content)
                
                # Улучшенный парсинг: ищем массив внутри объекта
                if isinstance(clips_data, dict):
                    extracted = False
                    # Проверяем известные ключи
                    for key in ["clips", "segments", "viral_segments", "fragments", "data"]:
                        if key in clips_data and isinstance(clips_data[key], list):
                            clips_data = clips_data[key]
                            extracted = True
                            break
                    
                    # Если не нашли по ключам, берем первый попавшийся массив
                    if not extracted:
                        for key, value in clips_data.items():
                            if isinstance(value, list):
                                clips_data = value
                                extracted = True
                                break
                                
                    # Если массивов нет вообще, оборачиваем сам объект в список
                    if not extracted:
                        clips_data = [clips_data]
                
                if not isinstance(clips_data, list):
                    clips_data = []
                    
                # Фильтруем только валидные клипы (где есть start_time)
                valid_clips = []
                for clip in clips_data:
                    if 'start_time' in clip and 'end_time' in clip:
                        valid_clips.append(clip)
                    elif 'start' in clip and 'end' in clip:
                        clip['start_time'] = clip['start']
                        clip['end_time'] = clip['end']
                        valid_clips.append(clip)
                clips_data = valid_clips
                
            except Exception as e:
                print(f"❌ [ArbiFlow Worker]: DeepSeek Error: {str(e)}", flush=True)
                raise Exception(f"AI Analysis failed: {str(e)}")
            
            print(f"✅ [ArbiFlow Worker]: DeepSeek found {len(clips_data)} clips.", flush=True)
            
            # 4. Cut and Crop
            output_zip = os.path.join(TEMP_PATH, f"shorts_{job_id}.zip")
            cleanup_list.append(output_zip)
            
            descriptions = []
            
            print(f"✂️ [ArbiFlow Worker]: Creating ZIP and processing clips...", flush=True)
            with zipfile.ZipFile(output_zip, 'w') as zipf:
                for i, clip in enumerate(clips_data):
                    start = clip['start_time']
                    end = clip['end_time']
                    cover_title = clip.get('cover_title', 'Viral Clip')
                    post_desc = clip.get('description_for_post', '')
                    format_type = clip.get('format_type', 'Shorts')
                    
                    clip_filename = f"clip_{i+1}.mp4"
                    clip_path = os.path.join(TEMP_PATH, clip_filename)
                    cleanup_list.append(clip_path)
                    
                    print(f"🎬 [ArbiFlow Worker]: Processing clip {i+1}: {start}s - {end}s", flush=True)
                    # Умный кроп (поиск лица)
                    try:
                        probe = ffmpeg.probe(input_video)
                        v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                        width = int(v_stream['width'])
                        height = int(v_stream['height'])
                        
                        # Находим центр лица
                        face_x = get_face_center_x(input_video, start, end-start, width)
                        
                        # libx264 требует четные размеры (делимые на 2)
                        crop_w = int(height * 9 / 16)
                        if crop_w % 2 != 0: crop_w -= 1
                        
                        target_h = min(height, 1080)
                        if target_h % 2 != 0: target_h -= 1
                        
                        target_w = int(target_h * 9 / 16)
                        if target_w % 2 != 0: target_w -= 1
                        
                        # Рассчитываем x_offset так, чтобы лицо было в центре
                        x_offset = face_x - (crop_w // 2)
                        # Ограничиваем, чтобы не выйти за края видео
                        x_offset = max(0, min(x_offset, width - crop_w))
                        
                        ffmpeg.input(input_video, ss=start, t=end-start).output(
                            clip_path,
                            vf=f"crop={crop_w}:{height}:{x_offset}:0,scale={target_w}:{target_h}",
                            vcodec='libx264', acodec='aac', preset='fast', crf=26
                        ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                        
                        zipf.write(clip_path, arcname=clip_filename)
                        descriptions.append(f"🎬 {i+1}.mp4 [{format_type}] - {cover_title}\n📝 Описание: {post_desc}\n")
                    except Exception as e:
                        print(f"❌ [ArbiFlow Worker]: Error processing clip {i+1}: {e}", file=sys.stderr, flush=True)
                        
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
