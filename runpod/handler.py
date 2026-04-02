import os
import sys

# --- EARLY LOGGING ---
print("🚀 [ArbiFlow Worker]: Starting initialization...", flush=True)

try:
    import uuid
    import requests
    import runpod
    import torch
    import ffmpeg
    import traceback
    import shutil
    from dotenv import load_dotenv
    print("✅ [ArbiFlow Worker]: Core modules imported.", flush=True)
except Exception as e:
    print(f"❌ [ArbiFlow Worker]: FAILED TO IMPORT CORE MODULES: {e}", file=sys.stderr, flush=True)
    sys.exit(1)

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

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        from faster_whisper import WhisperModel
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if torch.cuda.is_available() else "int8"
        whisper_model = WhisperModel(
            "large-v3", 
            device=device, 
            compute_type=compute_type, 
            download_root=MODEL_PATH
        )
    return whisper_model

def download_file(url, dest):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(url, stream=True, timeout=300, headers=headers)
    response.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def upload_to_catbox(file_path):
    import time
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
        except:
            pass
        time.sleep(2)
        
    # Fallback to 0x0.st
    try:
        url = "https://0x0.st"
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            response = requests.post(url, files=files, headers=headers, timeout=120)
            if response.status_code == 200:
                return response.text.strip()
    except:
        pass
        
    raise Exception("Upload to cloud failed after multiple attempts")

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
        download_file(video_url, input_video)
        
        is_image = job_input.get("is_image", False)
        if is_image:
            new_input = input_video.replace('.mp4', '.jpg')
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
                
        elif task == "upscale":
            try:
                import cv2
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer
                
                device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
                rrdb_model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
                model_path = '/runpod-volume/ComfyUI/models/upscale_models/4x-UltraSharp.pth'
                if not os.path.exists(model_path):
                    model_path = '/runpod-volume/ComfyUI/models/upscale_models/4x-UltraSharp.safetensors'
                
                upscaler = RealESRGANer(
                    scale=4, model_path=model_path, model=rrdb_model, 
                    tile=0, tile_pad=10, pre_pad=0, half=True, device=device
                )
                
                if is_image:
                    img = cv2.imread(input_video, cv2.IMREAD_COLOR)
                    output, _ = upscaler.enhance(img, outscale=4)
                    cv2.imwrite(output_video, output)
                else:
                    cap = cv2.VideoCapture(input_video)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    
                    temp_video_path = os.path.join(TEMP_PATH, f"temp_{job_id}.mp4")
                    cleanup_list.append(temp_video_path)
                    out = cv2.VideoWriter(temp_video_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width*4, height*4))
                    
                    while cap.isOpened():
                        ret, frame = cap.read()
                        if not ret: break
                        enhanced_frame, _ = upscaler.enhance(frame, outscale=4)
                        out.write(enhanced_frame)
                    cap.release()
                    out.release()
                    
                    try:
                        ffmpeg.output(ffmpeg.input(temp_video_path).video, ffmpeg.input(input_video).audio, output_video, vcodec='libx264', acodec='aac').run(overwrite_output=True, quiet=True)
                    except:
                        shutil.copy(temp_video_path, output_video)
                
            except Exception as e:
                # Fallback to Lanczos
                vf = "scale=iw*4:ih*4:flags=lanczos"
                if is_image:
                    ffmpeg.input(input_video).output(output_video, vf=vf).run(overwrite_output=True, quiet=True)
                else:
                    ffmpeg.input(input_video).output(output_video, vf=vf, vcodec='libx264', acodec='copy').run(overwrite_output=True, quiet=True)

        elif task == "ai_voice":
            voice_text = job_input.get("voice_text")
            import edge_tts
            import asyncio
            output_audio = output_video.replace('.mp4', '.mp3')
            cleanup_list.append(output_audio)
            asyncio.run(edge_tts.Communicate(voice_text, "ru-RU-SvetlanaNeural").save(output_audio))
            output_video = output_audio
                
        elif task == "ai_translate":
            shutil.copy(input_video, output_video)
            
        result_url = upload_to_catbox(output_video)
        return {"status": "success", "result_url": result_url}

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
