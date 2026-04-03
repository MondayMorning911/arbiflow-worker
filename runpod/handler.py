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
            import yt_dlp
            import zipfile
            from google import genai
            from google.genai import types
            import json
            import cv2
            
            is_url = job_input.get("is_url", False)
            
            # 1. Download if URL
            if is_url:
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': input_video,
                    'quiet': True,
                    'no_warnings': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            
            # 2. Transcribe
            model_instance = get_whisper_model()
            segments, info = model_instance.transcribe(input_video, beam_size=5, word_timestamps=True, vad_filter=True)
            
            transcript = ""
            for segment in segments:
                transcript += f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {segment.text}\n"
                
            # 3. Analyze with Gemini
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            if not gemini_api_key:
                raise Exception("GEMINI_API_KEY is not set")
                
            client = genai.Client(api_key=gemini_api_key)
            prompt = f"""
            Analyze the following video transcript and identify the 3 most engaging, viral-worthy segments.
            Each segment should be between 15 and 60 seconds long.
            Return the result as a JSON array of objects, where each object has:
            - start_time: start time in seconds (float)
            - end_time: end time in seconds (float)
            - description: a short, catchy title/description for the clip
            
            Transcript:
            {transcript}
            """
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            
            clips_data = json.loads(response.text)
            
            # 4. Cut and Crop
            output_zip = os.path.join(TEMP_PATH, f"shorts_{job_id}.zip")
            cleanup_list.append(output_zip)
            
            descriptions = []
            
            with zipfile.ZipFile(output_zip, 'w') as zipf:
                for i, clip in enumerate(clips_data):
                    start = clip['start_time']
                    end = clip['end_time']
                    desc = clip['description']
                    
                    clip_filename = f"clip_{i+1}.mp4"
                    clip_path = os.path.join(TEMP_PATH, clip_filename)
                    cleanup_list.append(clip_path)
                    
                    # Simple center crop for now (9:16)
                    try:
                        probe = ffmpeg.probe(input_video)
                        v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                        width = int(v_stream['width'])
                        height = int(v_stream['height'])
                        
                        target_h = min(height, 1080)
                        target_w = int(target_h * 9 / 16)
                        x_offset = (width - int(height * 9 / 16)) // 2
                        
                        ffmpeg.input(input_video, ss=start, t=end-start).output(
                            clip_path,
                            vf=f"crop={int(height * 9 / 16)}:{height}:{x_offset}:0,scale={target_w}:{target_h}",
                            vcodec='libx264', acodec='aac', preset='fast', crf=26
                        ).overwrite_output().run(capture_stdout=True, capture_stderr=True)
                        
                        zipf.write(clip_path, arcname=clip_filename)
                        descriptions.append(f"{i+1}. {desc} ({start}s - {end}s)")
                    except Exception as e:
                        print(f"Error processing clip {i}: {e}")
                        
                # Add descriptions.txt
                desc_path = os.path.join(TEMP_PATH, "descriptions.txt")
                cleanup_list.append(desc_path)
                with open(desc_path, 'w', encoding='utf-8') as f:
                    f.write("✅ Нарезка завершена! Ваши клипы:\n\n" + "\n".join(descriptions))
                zipf.write(desc_path, arcname="descriptions.txt")
                
            output_video = output_zip
            
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
