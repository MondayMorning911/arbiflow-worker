import aiohttp
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY")
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")

async def upload_to_catbox(file_path: str) -> str:
    last_error = ""
    for attempt in range(3):
        try:
            url = "https://catbox.moe/user/api.php"
            async with aiohttp.ClientSession() as session:
                with open(file_path, 'rb') as f:
                    data = aiohttp.FormData()
                    data.add_field('reqtype', 'fileupload')
                    data.add_field('fileToUpload', f, filename=os.path.basename(file_path))
                    async with session.post(url, data=data, timeout=120) as resp:
                        if resp.status == 200:
                            return (await resp.text()).strip()
                        else:
                            last_error = f"Catbox HTTP {resp.status}"
        except Exception as e:
            last_error = str(e)
        await asyncio.sleep(2)
        
    # Fallback to 0x0.st
    try:
        url = "https://0x0.st"
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(url, data=data, timeout=120) as resp:
                    if resp.status == 200:
                        return (await resp.text()).strip()
    except Exception as e:
        pass
        
    raise Exception(f"Upload failed after retries. Last error: {last_error}")

async def runpod_submit_job(task_name: str, input_data: dict) -> str:
    url = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}/run"
    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "input": {
            "task": task_name,
            **input_data
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("id")
            else:
                text = await resp.text()
                raise Exception(f"RunPod submit failed: {resp.status} - {text}")

async def runpod_check_status(job_id: str) -> dict:
    url = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}/status/{job_id}"
    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json"
    }
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        raise Exception(f"RunPod status failed: {resp.status} - {text}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == 2:
                raise Exception(f"RunPod status connection error: {str(e)}")
            await asyncio.sleep(2)

async def download_from_url(url: str, dest_path: str):
    import time
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    last_error = ""
    
    # Give Catbox a moment to propagate the file across their CDN
    if "catbox.moe" in url:
        await asyncio.sleep(3)
        
    for attempt in range(5):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=600, connect=60)) as resp:
                    if resp.status == 200:
                        with open(dest_path, 'wb') as f:
                            while True:
                                chunk = await resp.content.read(65536) # Larger chunk size
                                if not chunk:
                                    break
                                f.write(chunk)
                        return
                    else:
                        last_error = f"HTTP {resp.status}"
        except Exception as e:
            last_error = str(e)
            
        # Exponential backoff
        await asyncio.sleep(2 * (attempt + 1))
        
    raise Exception(f"Server disconnected during download: {last_error}")

async def process_heavy_task(file_path: str, task_name: str, progress_callback=None, **kwargs) -> str:
    """
    1. Upload to Catbox
    2. Submit to RunPod
    3. Poll status
    4. Download result
    """
    if progress_callback:
        await progress_callback(10, "Загрузка файла в облако...")
    
    file_url = await upload_to_catbox(file_path)
    
    if progress_callback:
        await progress_callback(20, "Отправка задачи на GPU...")
        
    input_data = {"video_url": file_url, "task": task_name, **kwargs}
    job_id = await runpod_submit_job(task_name, input_data)
    
    attempts = 0
    while True:
        await asyncio.sleep(5)
        status_data = await runpod_check_status(job_id)
        status = status_data.get("status")
        
        if status in ["IN_QUEUE", "IN_PROGRESS"]:
            attempts += 1
            fake_prog = min(90, 20 + attempts * 5)
            if progress_callback:
                if task_name == "ai_subs":
                    msg = "⏳ Расшифровка текста..." if attempts % 2 == 0 else "⚙️ Рендер видео..."
                else:
                    msg = f"Обработка на GPU ({status})..."
                await progress_callback(fake_prog, msg)
        elif status == "COMPLETED":
            if progress_callback:
                await progress_callback(95, "Скачивание результата...")
            output = status_data.get("output", {})
            result_url = output.get("result_url") or output.get("video_url") or output.get("url")
            if not result_url:
                raise Exception("RunPod completed but no result_url provided.")
            
            ext = result_url.split('.')[-1]
            if len(ext) > 4: ext = "mp4"
            dest_path = os.path.join(os.path.dirname(file_path), f"rp_result_{job_id}.{ext}")
            await download_from_url(result_url, dest_path)
            return dest_path
        elif status in ["FAILED", "CANCELLED"]:
            raise Exception(f"RunPod task failed: {status_data}")
