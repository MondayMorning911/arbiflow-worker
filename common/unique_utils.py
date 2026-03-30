import os
import random
import time
import uuid
import subprocess
import zipfile
from pathlib import Path
from multiprocessing import Pool, cpu_count
from datetime import datetime
import aiofiles
import hashlib
import asyncio
import aiohttp
from aiogram.types import Message  # Добавь этот импорт
from aiogram.fsm.context import FSMContext  # Добавь этот импорт
import ffmpeg
import requests
from typing import Optional, Callable, Awaitable


semaphore = asyncio.Semaphore(3)

async def is_valid_mp4(path):
    if not path.endswith('.mp4') or not os.path.exists(path) or os.path.getsize(path) < 100 * 1024:
        return False

    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return False
        
        output = stdout.decode().strip().split("\n")
        if not output or "video" not in output[0].lower():
            return False
        duration = float(output[0].split(",")[1])
        return duration > 0.5
    except Exception:
        return False

async def get_video_duration(path: str) -> float:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        return float(stdout.decode().strip())
    except Exception:
        return 0.0

MAX_TG_SIZE_MB = 49

def get_file_size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)

def upload_to_gofile(path):
    try:
        with open(path, "rb") as f:
            response = requests.post(
                "https://upload.gofile.io/uploadfile",
                files={"file": (os.path.basename(path), f)}
            )
        data = response.json()
        if data.get("status") == "ok":
            return data["data"]["downloadPage"]
    except Exception as e:
        print(f"❌ Ошибка при загрузке на Gofile: {e}")
    return None



async def handle_large_file_upload(file_path: str, chat_id: int, bot):
    import logging
    logging.info(f"[MainBot] Handling large file upload: {file_path} for chat_id={chat_id}")
    try:
        userbot_id = int(os.getenv("USERBOT_ID", "0"))
        import json
        meta = {
            "action": "upload",
            "path": file_path,
            "user_id": chat_id
        }
        logging.info(f"[MainBot] Sending upload request to UserBot (ID: {userbot_id}): {meta}")
        await bot.send_message(userbot_id, json.dumps(meta))
        await bot.send_message(chat_id, "⏳ Файл большой. Загружаю через UserBot, это займет немного времени...")
    except Exception as e:
        logging.error(f"[MainBot] ❌ Ошибка при делегировании загрузки UserBot'у: {e}", exc_info=True)
        await bot.send_message(chat_id, f"❌ Ошибка отправки на обработку. Попробуйте позже.\nПодробности: {e}")


async def handle_large_files(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = str(uuid.uuid4())  # Генерация уникального ID для сессии
    output_dir = os.path.join(DOWNLOAD_DIR, f"batch_{session_id}")
    os.makedirs(output_dir, exist_ok=True)

    # Генерация уникализированных видео
    generated_files = await generate_unique_copies_async(input_path=input_path, output_dir=output_dir, count=count, semaphore=semaphore, progress_callback=callback)

    # Создание архива из уникализированных файлов
    zip_path = os.path.join(DOWNLOAD_DIR, f"archive_{session_id}")
    await create_zip(generated_files, zip_path)

    # Загружаем архив на Gofile и отправляем ссылку пользователю
    await handle_large_file_upload(zip_path, message.chat.id, bot)
    
    # Очищаем временные файлы
    shutil.rmtree(output_dir, ignore_errors=True)
    os.remove(zip_path)


async def hash_file(path):
    h = hashlib.sha256()
    async with aiofiles.open(path, 'rb') as f:
        while True:
            chunk = await f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def random_color():
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))

def get_random_filters(input_path):

    probe = ffmpeg.probe(input_path)
    video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    orig_w = int(video_stream['width'])
    orig_h = int(video_stream['height'])

    # --- drawtext с эмодзи 70% шанс ---
    emojis = ['❤️', '💛', '💚', '💙', '💜', '🖤', '🤍', '💖', '💫', '✨', '🎀', '🌸', '🌼', '🌷', '🌿', '🍀', '🍃', '🌟', '💎', '💕', '💓', '💗', '💞', '💘', '💝', '🍭', '🍬', '🧁', '🍓', '🧸', '🕊️', '🐣', '🐾', '🪄']
    emoji_vf = ""

    # Убрали эмодзи, так как на Linux нет Apple Color Emoji и ffmpeg падает


    visual_filters = [
        lambda: f"eq=contrast=1.07:saturation=1.04:brightness=0.03",
        lambda: f"scale=iw*{round(random.uniform(1.01, 1.02), 3)}:ih*{round(random.uniform(1.01, 1.02), 3)}",
        lambda: f"crop=in_w-{random.randint(2, 4)}:in_h-{random.randint(2, 4)}",
        lambda: f"pad=iw+2:ih+2:(ow-iw)/2:(oh-ih)/2:color=0x{random.randint(0x111111, 0x999999):06x}@0.04",
        lambda: f"rotate={round(random.uniform(-0.01, 0.01), 5)}:fillcolor=black",
        # Тёплый (теплый тон, без кислотных оттенков)
        lambda: "colorchannelmixer=rr=1.1:gg=0.95:bb=0.9",

        # Холодный (холодный тон, синие оттенки)
        lambda: "colorchannelmixer=rr=0.95:gg=0.95:bb=1.1",

        # Слегка тёплый с усилением красного
        lambda: "colorchannelmixer=rr=1.05:gg=1.0:bb=0.95",

        # Слегка холодный с усилением синего
        lambda: "colorchannelmixer=rr=0.95:gg=1.0:bb=1.05",

        # Слабый контраст и насыщенность
        lambda: "eq=contrast=1.05:saturation=1.05",

        # Немного пониженная яркость (смягчённый эффект)
        lambda: "eq=brightness=-0.02:saturation=1.03",

        # Немного повышенная яркость
        lambda: "eq=brightness=0.02:saturation=1.02",

        # Комбинация тёплого + яркости
        lambda: "colorchannelmixer=rr=1.05:gg=0.97:bb=0.93,eq=brightness=0.015",

        lambda: "lut='r=val+2:g=val-1:b=val'",
        lambda: f"drawbox=x=0:y=0:w=iw:h=5:color=black@0.15:t=fill",
        lambda: (
    f"scale=iw*{round(random.uniform(1.01, 1.03), 3)}:"
    f"ih*{round(random.uniform(1.01, 1.03), 3)},"
    "crop=iw-2:ih-2"
),
        lambda: "vignette=PI/4",
        lambda: "unsharp=5:5:0.5",  # слегка повышает резкость
        # Дребезжащий масштаб
        lambda: (
            f"scale=iw*{round(random.uniform(1.005, 1.02), 3)}:"
            f"ih*{round(random.uniform(1.005, 1.02), 3)},"
            "crop=iw-4:ih-4"
        ),

        # Лёгкая псевдо-дергающаяся рамка
        lambda: f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.01:t=2",

        # "Блики" сверху
        lambda: f"drawbox=x=0:y=0:w=iw:h=20:color=white@0.03:t=fill",

        lambda: random.choice(["hflip"]),
        lambda: "chromakey=black:0.1:0.2",
        lambda: (
        "drawbox=x=0:y=0:w=iw:h=3:color=black@0.08:t=fill,"
        "drawbox=x=0:y=ih-3:w=iw:h=3:color=black@0.08:t=fill,"
        "drawbox=x=0:y=0:w=3:h=ih:color=black@0.08:t=fill,"
        "drawbox=x=iw-3:y=0:w=3:h=ih:color=black@0.08:t=fill"
    ),
        lambda: "colorchannelmixer=rr=1.1:gg=0.9:bb=1.0",
        lambda: f"setpts=PTS*{round(random.uniform(0.985, 1.015), 5)}",  # микроскопическое замедление/ускорение
        lambda: f"tpad=start_duration={round(random.uniform(0.1, 0.3), 2)}",  # лёгкое структурное смещение
        lambda: f"trim=start={round(random.uniform(0.05, 0.2), 2)}",
        lambda: f"drawgrid=width=iw/72:height=ih/60:thickness=1:color=white@{round(random.uniform(0.03, 0.07), 2)}",
        lambda: random.choice([
            "colorbalance=rs=0.2",  # теплый
            "colorbalance=bs=0.2"   # холодный
        ]),
        lambda: f"fade=in:0:{random.randint(3, 6)}"
    ]

    selected_vf = random.sample(visual_filters, k=random.randint(4, 6))
    vf_core = ",".join(f() for f in selected_vf)
    vf = (
        "scale=1080:-2,"                 # сначала даунскейлим для ускорения
        + emoji_vf
        + vf_core                        # применяем все фильтры (работают быстрее на 1080p)
        + (f",scale={orig_w}:{orig_h}" if orig_w != 1920 or orig_h != 1080 else "")  # восстанавливаем оригинал
        + ",scale=trunc(iw/2)*2:trunc(ih/2)*2"      # приводим к чётным значениям
        + ",crop='floor(in_w/2)*2:floor(in_h/2)*2'" # фиксируем размер (чётный crop)
        + ",setsar=1"                              # нормализуем SAR (до финального вывода!)
        + ",format=yuv420p"                        # завершаем правильной цветовой моделью
    )




    # --- аудио ---
    audio_filters = [
        lambda: "volume=1.01",  # еле заметное усиление
        lambda: f"atempo={round(random.uniform(0.97, 1.03), 2)}",  # лёгкое изменение темпа
        lambda: "asetrate=44100*1.01,aresample=44100",  # лёгкое повышение частоты
        lambda: "asetrate=44100*0.99,aresample=44100",  # лёгкое понижение частоты
        lambda: "adelay=10|10",  # сдвиг аудио по каналам
        lambda: "apad=pad_dur=0.1",  # тишина в конце
        lambda: "highpass=f=20",  # фильтрация низких <20 Гц (не слышно)
        lambda: "lowpass=f=18000",  # фильтрация высоких >18 кГц (не слышно)
        lambda: "pan=stereo|c0=c0|c1=c1", # как бы "клонирует" стерео
        lambda: "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo"  # безопасное форматирование
    ]

    af = ",".join(f() for f in random.sample(audio_filters, k=2))

    structure_flags = [
        ["-map_metadata", "-1"],  # Удаляет старые метаданные
        ["-movflags", "+faststart"],
        ["-crf", str(random.randint(23, 25))],
        ["-c:v", "libx264"],
        ["-c:a", "aac"],
        ["-preset", "ultrafast"],  
        ["-b:v", f"{random.randint(800, 1500)}k"],


        # --- Поддельные метаданные ---
        ["-metadata", f"title=Export_{random.randint(1000, 9999)}"],
        ["-metadata", f"comment=Session_{random.randint(100000, 999999)}"],

        ["-metadata", f"encoder={random.choice(['Adobe Premiere', 'CapCut Pro', 'DaVinci Resolve', 'Shotcut'])}"],
        ["-metadata", f"creation_time={datetime.utcnow().isoformat()}Z"]
    ]
    flags = [f for pair in structure_flags for f in pair]

    # DEBUG: лог финальных фильтров
    print("▶️ Visual Filter:", vf)
    print("🎵 Audio Filter:", af)
    extra_flags = []  # если не используется, пусть будет пустым списком

    return vf, af, extra_flags, flags



from new_modules.runpod_client import process_heavy_task
import shutil

async def unique_video_single(input_path: str, output_path: str, vf=None, af=None, extra_flags=None, return_log=False):
    duration = await get_video_duration(input_path)
    if duration > 60:
        # Heavy task -> RunPod
        print(f"Video duration {duration}s > 60s. Sending to RunPod for heavy unique...")
        try:
            # We pass the filters to RunPod if needed, or let RunPod generate them.
            # Assuming RunPod expects "heavy_unique" task.
            result_path = await process_heavy_task(
                file_path=input_path,
                task_name="heavy_unique",
                vf=vf,
                af=af
            )
            shutil.move(result_path, output_path)
            if return_log:
                return ["RunPod Heavy Unique Applied"]
            return
        except Exception as e:
            print(f"RunPod failed: {e}. Falling back to local FFmpeg...")

    if vf is None or af is None:
       vf, af, extra_flags, flags = get_random_filters(input_path)
       extra_flags = flags # Use flags as extra_flags if generated

    if extra_flags is None:
        extra_flags = []

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-af", af,
    ] + extra_flags + [output_path]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300) # 5 minutes timeout
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        print(f"❌ FFmpeg timed out for {output_path}")
        raise RuntimeError("ffmpeg timed out")

    if process.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        print("❌ FFmpeg stderr:\n", stderr.decode())
        raise RuntimeError("ffmpeg failed or output empty")

    now = time.time()
    os.utime(output_path, (now, now))

    if return_log:
        return vf.split(",") + af.split(",")



def _batch_worker(args):
    input_path, output_path, vf, af, flags = args
    try:
        unique_video_single(input_path, output_path, vf=vf, af=af, extra_flags=flags)
        return output_path if is_valid_mp4(output_path) else None
    except Exception:
        return None

async def unique_video_batch(input_path: str, count: int, output_dir: str, on_progress: Optional[Callable[[int, int], Awaitable]] = None):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    generated_files = []
    seen_hashes = set()
    used_vf_signatures = set()
    tries = 0
    max_tries = count * 6

    async def process_task(output_file, vf, af, flags):
        try:
            async with semaphore:
                await unique_video_single(input_path, output_file, vf=vf, af=af, extra_flags=flags)
                if await is_valid_mp4(output_file):
                    file_hash = await hash_file(output_file)
                    if file_hash not in seen_hashes:
                        seen_hashes.add(file_hash)
                        return output_file
                    else:
                        os.remove(output_file)
                else:
                    os.remove(output_file)
        except Exception:
            pass
        return None

    while len(generated_files) < count and tries < max_tries:
        tasks = []
        for _ in range(count - len(generated_files)):
            vf, af, extra_flags, flags = get_random_filters(input_path)
            vf_signature = vf.strip().replace(" ", "")
            if vf_signature in used_vf_signatures:
                continue
            used_vf_signatures.add(vf_signature)

            suffix = f"_{uuid.uuid4().hex[:8]}.mp4"
            output_file = os.path.join(output_dir, f"unique{suffix}")
            tasks.append((output_file, vf, af, flags))

        results = await asyncio.gather(
            *[process_task(out, vf, af, fl) for out, vf, af, fl in tasks]
        )

        for result in results:
            if result:
                generated_files.append(result)
                if on_progress:
                    await on_progress(len(generated_files), count)

        tries += 1

    for f in Path(output_dir).glob("*.mp4"):
        if str(f) not in generated_files:
            os.remove(f)

    return generated_files[:count]

async def create_zip(files: list, zip_path: str):
    valid_files = []

    for f in files:
        if await is_valid_mp4(f):
            valid_files.append(f)

    def zip_worker():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in valid_files:
                arcname = os.path.basename(file)
                zipf.write(file, arcname=arcname)
        return zip_path

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, zip_worker)
