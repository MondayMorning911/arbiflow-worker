import os
import uuid
import requests
import runpod
import torch
import ffmpeg
import traceback
import shutil
import sys
from dotenv import load_dotenv

def send_debug(msg):
    try:
        token = os.getenv("TELEGRAM_API_TOKEN")
        chat_id = os.getenv("TELEGRAM_DEBUG_CHAT_ID")
        if not token or not chat_id: return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = str(msg)[:4000]
        requests.post(url, json={"chat_id": chat_id, "text": f"🤖 [ArbiFlow Debug]:\n{text}"}, timeout=5)
    except:
        pass

# --- GLOBAL WRAPPER ---
try:
    load_dotenv()
    send_debug("🚀 Скрипт запущен! Начинаю инициализацию...")

    # --- CONFIGURATION ---
    VOLUME_PATH = "/runpod-volume"
    MODEL_PATH = os.path.join(VOLUME_PATH, "models")
    # Пытаемся найти шрифт
    BASE_DIR = os.getcwd()
    FONT_PATH = os.path.join(BASE_DIR, "SoyuzGroteskBold.ttf")
    if not os.path.exists(FONT_PATH):
        FONT_PATH = os.path.join(BASE_DIR, "runpod", "SoyuzGroteskBold.ttf")
        if not os.path.exists(FONT_PATH):
            FONT_PATH = os.path.join(BASE_DIR, "fonts", "font.ttf")
            if not os.path.exists(FONT_PATH):
                FONT_PATH = "/app/fonts/font.ttf"

    FONT_DIR = os.path.dirname(FONT_PATH)
    TEMP_PATH = "/tmp/arbiflow"

    os.makedirs(TEMP_PATH, exist_ok=True)
    os.makedirs(MODEL_PATH, exist_ok=True)
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    COMPUTE_TYPE = "float16" if torch.cuda.is_available() else "int8"
    send_debug(f"⚙️ Конфиг готов. Device: {DEVICE}, Compute: {COMPUTE_TYPE}")

except Exception as e:
    err_msg = f"❌ ФАТАЛЬНАЯ ОШИБКА ПРИ СТАРТЕ:\n{traceback.format_exc()}"
    print(err_msg, file=sys.stderr)
    send_debug(err_msg)
    # Мы не выходим, чтобы runpod мог попытаться запустить handler и показать ошибку там

whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            send_debug(f"📦 Загружаю модель large-v3 в {MODEL_PATH}...")
            whisper_model = WhisperModel(
                "large-v3", 
                device=DEVICE, 
                compute_type=COMPUTE_TYPE, 
                download_root=MODEL_PATH
            )
            send_debug("✅ Модель успешно загружена в память!")
        except Exception as e:
            send_debug(f"❌ Ошибка загрузки модели:\n{traceback.format_exc()}")
            raise e
    return whisper_model

def download_file(url, dest):
    send_debug(f"📥 Качаю файл. Ссылка: {url[:30]}...") 
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, stream=True, timeout=300, headers=headers)
        response.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        send_debug("✅ Файл скачан.")
    except Exception as e:
        raise Exception(f"Ошибка скачивания: {str(e)}")

def upload_to_catbox(file_path):
    import time
    send_debug("📤 Загрузка в облако...")
    last_error = ""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for attempt in range(3):
        try:
            url = "https://catbox.moe/user/api.php"
            with open(file_path, 'rb') as f:
                data = {'reqtype': 'fileupload'}
                files = {'fileToUpload': (os.path.basename(file_path), f)}
                response = requests.post(url, data=data, files=files, headers=headers, timeout=120)
                if response.status_code == 200:
                    return response.text.strip()
                else:
                    last_error = f"HTTP {response.status_code}"
        except Exception as e:
            last_error = str(e)
        time.sleep(2)
        
    send_debug("⚠️ Catbox недоступен, пробую 0x0.st...")
    try:
        url = "https://0x0.st"
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            response = requests.post(url, files=files, headers=headers, timeout=120)
            if response.status_code == 200:
                return response.text.strip()
    except Exception as e:
        pass
        
    raise Exception(f"Upload failed. Last error: {last_error}")

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{millis:02d}"

def generate_ass_subtitles(segments, output_path, position="bottom", width=1080, height=1920):
    font_name = "Soyuz Grotesk Bold"
    
    if position == "middle":
        alignment = 5
        margin_v = 0
    else:
        alignment = 2
        margin_v = int(height * 0.13)
        
    font_size = int(height * 0.04)
    outline = 3
    shadow = 1.5
        
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},10,10,{margin_v},1
"""
    lines = []
    for segment in segments:
        if not hasattr(segment, 'words') or not segment.words:
            start = format_timestamp(segment.start)
            end = format_timestamp(segment.end)
            text = segment.text.strip().replace("\n", "\\N").upper()
            text = text.replace('Ё', 'Е')
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
            continue

        words = list(segment.words)
        chunk_size = 1
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            start = format_timestamp(chunk[0].start)
            end = format_timestamp(chunk[-1].end)
            text = " ".join([w.word.strip() for w in chunk]).upper()
            text = text.replace('Ё', 'Е')
            if chunk_size == 1:
                for p in ['-', ',', '.', '!', '?', ':', ';']:
                    text = text.replace(p, '')
                text = text.strip()
            if text:
                lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write("\n".join(lines))

def handler(job):
    job_id = job.get("id")
    send_debug(f"⚡️ Задание ID: {job_id}")
    job_input = job.get("input", {})
    task = job_input.get("task")
    video_url = job_input.get("video_url")
    
    if not video_url:
        return {"error": "No video_url provided"}
    if not task:
        return {"error": "No task provided"}

    input_video = os.path.join(TEMP_PATH, f"in_{job_id}.mp4")
    output_video = os.path.join(TEMP_PATH, f"out_{job_id}.mp4")
    
    try:
        download_file(video_url, input_video)
        
        is_image = job_input.get("is_image", False)
        if is_image:
            new_input = input_video.replace('.mp4', '.jpg')
            os.rename(input_video, new_input)
            input_video = new_input
            output_video = output_video.replace('.mp4', '.jpg')
            
        if task == "ai_subs":
            position = job_input.get("position", "bottom")
            ass_file = os.path.join(TEMP_PATH, f"subs_{job_id}.ass")
            
            model_instance = get_whisper_model()
            send_debug("🎙 Транскрибация...")
            segments, _ = model_instance.transcribe(
                input_video, 
                beam_size=5, 
                word_timestamps=True,
                vad_filter=True
            )
            
            segments_list = list(segments)
            send_debug(f"📝 Сегментов: {len(segments_list)}")

            width, height = 1080, 1920
            try:
                probe = ffmpeg.probe(input_video)
                v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                if v_stream:
                    width = int(v_stream['width'])
                    height = int(v_stream['height'])
                    send_debug(f"📐 Разрешение видео: {width}x{height}")
            except Exception as e:
                send_debug(f"⚠️ Не удалось определить разрешение: {e}")

            generate_ass_subtitles(segments_list, ass_file, position=position, width=width, height=height)

            send_debug("🔥 Рендер FFmpeg (субтитры)...")
            try:
                (
                    ffmpeg
                    .input(input_video)
                    .output(output_video, 
                            vf=f"subtitles='{ass_file}':fontsdir={FONT_DIR}", 
                            vcodec='libx264', 
                            acodec='copy',
                            preset='ultrafast',
                            crf=23)
                    .overwrite_output()
                    .run(capture_stdout=True, capture_stderr=True)
                )
            except ffmpeg.Error as e:
                stderr = e.stderr.decode()
                send_debug(f"❌ Ошибка FFmpeg:\n{stderr[-500:]}")
                raise Exception("FFmpeg failed")
                
        elif task == "upscale":
            send_debug("✨ Апскейл видео/фото (4x-UltraSharp)...")
            try:
                import cv2
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer
                
                device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
                # 4x-UltraSharp is an RRDBNet model
                rrdb_model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
                model_path = '/runpod-volume/ComfyUI/models/upscale_models/4x-UltraSharp.pth'
                if not os.path.exists(model_path):
                    model_path = '/runpod-volume/ComfyUI/models/upscale_models/4x-UltraSharp.safetensors'
                
                upscaler = RealESRGANer(
                    scale=4,
                    model_path=model_path,
                    dni_weight=None,
                    model=rrdb_model,
                    tile=0,
                    tile_pad=10,
                    pre_pad=0,
                    half=True,
                    device=device
                )
                
                scale_factor = 4
                
                if is_image:
                    send_debug("🖼 Обработка изображения...")
                    img = cv2.imread(input_video, cv2.IMREAD_COLOR)
                    if img is None:
                        raise Exception(f"cv2.imread failed to read {input_video}")
                    output, _ = upscaler.enhance(img, outscale=scale_factor)
                    success = cv2.imwrite(output_video, output)
                    if not success:
                        raise Exception(f"Failed to write image to {output_video}")
                else:
                    send_debug("🎥 Обработка видео...")
                    cap = cv2.VideoCapture(input_video)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    
                    out_width = int(width * scale_factor)
                    out_height = int(height * scale_factor)
                    
                    temp_video_path = os.path.join(TEMP_PATH, f"temp_{job_id}.mp4")
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    out = cv2.VideoWriter(temp_video_path, fourcc, fps, (out_width, out_height))
                    
                    while cap.isOpened():
                        ret, frame = cap.read()
                        if not ret: break
                        enhanced_frame, _ = upscaler.enhance(frame, outscale=scale_factor)
                        out.write(enhanced_frame)
                            
                    cap.release()
                    out.release()
                    
                    # Merge audio from original video
                    try:
                        input_vid = ffmpeg.input(temp_video_path)
                        input_aud = ffmpeg.input(input_video)
                        ffmpeg.output(input_vid.video, input_aud.audio, output_video, vcodec='libx264', acodec='aac').run(overwrite_output=True, quiet=True)
                    except:
                        shutil.copy(temp_video_path, output_video)
                    finally:
                        if os.path.exists(temp_video_path): os.remove(temp_video_path)
                
            except Exception as e:
                send_debug(f"⚠️ Ошибка апскейла: {e}. Использую Lanczos...")
                try:
                    if is_image:
                        (
                            ffmpeg
                            .input(input_video)
                            .output(output_video, vf="scale=iw*4:ih*4:flags=lanczos")
                            .run(overwrite_output=True, quiet=True, capture_stderr=True)
                        )
                    else:
                        (
                            ffmpeg
                            .input(input_video)
                            .output(output_video, vf="scale=iw*4:ih*4:flags=lanczos", vcodec='libx264', acodec='copy')
                            .run(overwrite_output=True, quiet=True, capture_stderr=True)
                        )
                except ffmpeg.Error as fallback_e:
                    stderr = fallback_e.stderr.decode() if fallback_e.stderr else "No stderr"
                    raise Exception(f"Fallback failed: {fallback_e}, stderr: {stderr}")
                except Exception as fallback_e:
                    raise Exception(f"Fallback failed: {fallback_e}")
        elif task == "ai_voice":
            send_debug("🎙 Озвучка текста...")
            voice_text = job_input.get("voice_text")
            if not voice_text:
                raise Exception("No voice_text provided for ai_voice")
                
            try:
                import edge_tts
                import asyncio
                
                output_audio = output_video.replace('.mp4', '.mp3')
                
                async def generate_tts():
                    communicate = edge_tts.Communicate(voice_text, "ru-RU-SvetlanaNeural")
                    await communicate.save(output_audio)
                    
                asyncio.run(generate_tts())
                output_video = output_audio
                
            except ImportError:
                send_debug("⚠️ edge-tts не установлен, использую заглушку")
                output_audio = output_video.replace('.mp4', '.mp3')
                os.system(f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 3 -q:a 9 -acodec libmp3lame {output_audio}")
                output_video = output_audio
                
        elif task == "ai_translate":
            send_debug("🌍 Перевод видео...")
            send_debug("⚠️ Полный пайплайн перевода еще не реализован в воркере. Возвращаю исходное видео.")
            shutil.copy(input_video, output_video)
            
        else:
            raise Exception(f"Unknown task: {task}")

        result_url = upload_to_catbox(output_video)
        send_debug(f"✨ ГОТОВО! {result_url}")
        return {"status": "success", "result_url": result_url}

    except Exception as e:
        err = traceback.format_exc()
        send_debug(f"🚨 ОШИБКА:\n{err}")
        return {"status": "error", "message": str(e)}
    finally:
        # Безопасная очистка
        files_to_clean = [
            input_video, 
            output_video,
            os.path.join(TEMP_PATH, f"subs_{job_id}.ass"),
            os.path.join(TEMP_PATH, f"temp_{job_id}.mp4"),
            os.path.join(TEMP_PATH, f"in_{job_id}.jpg"),
            os.path.join(TEMP_PATH, f"out_{job_id}.jpg"),
            os.path.join(TEMP_PATH, f"out_{job_id}.mp3")
        ]
        for f in files_to_clean:
            if f and os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == "__main__":
    try:
        send_debug("📡 Воркер готов к приему задач!")
        runpod.serverless.start({"handler": handler})
    except Exception as e:
        err_msg = f"❌ ОШИБКА ПРИ ЗАПУСКЕ СЕРВЕРЛЕСС:\n{traceback.format_exc()}"
        print(err_msg, file=sys.stderr)
        send_debug(err_msg)
