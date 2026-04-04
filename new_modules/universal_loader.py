import asyncio
import os
import yt_dlp
import uuid
import aiohttp

async def download_tiktok_fallback(url: str, output_dir: str, file_id: str) -> str:
    """
    Fallback downloader for TikTok using tikwm API.
    """
    api_url = "https://www.tikwm.com/api/"
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, data={"url": url}) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("code") == 0 and "data" in data and "play" in data["data"]:
                    video_url = data["data"]["play"]
                    
                    # Download the video file
                    output_path = os.path.join(output_dir, f"{file_id}.mp4")
                    async with session.get(video_url) as video_resp:
                        if video_resp.status == 200:
                            with open(output_path, "wb") as f:
                                f.write(await video_resp.read())
                            return output_path
            raise Exception("TikTok fallback API failed to extract video.")

async def download_via_cobalt(video_url: str, output_dir: str, file_id: str) -> str:
    """
    Downloads a video using Cobalt API instances.
    """
    instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.v0lume.me/api/json",
        "https://cobalt.meowing.de/api/json"
    ]
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    payload = {
        "url": video_url,
        "vQuality": "1080",
        "filenameStyle": "basic"
    }

    async with aiohttp.ClientSession() as session:
        for api_url in instances:
            try:
                async with session.post(api_url, json=payload, headers=headers, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "stream":
                            direct_url = data.get("url")
                            output_path = os.path.join(output_dir, f"{file_id}.mp4")
                            async with session.get(direct_url) as video_resp:
                                if video_resp.status == 200:
                                    with open(output_path, "wb") as f:
                                        f.write(await video_resp.read())
                                    return output_path
            except Exception:
                continue
    return None

async def download_video_ytdlp(url: str, output_dir: str) -> str:
    """
    Downloads a video from a given URL using Cobalt (primary) or yt-dlp (fallback).
    Returns the path to the downloaded video file.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    
    # Try Cobalt first
    try:
        cobalt_path = await download_via_cobalt(url, output_dir, file_id)
        if cobalt_path:
            return cobalt_path
    except Exception:
        pass

    output_template = os.path.join(output_dir, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'nocheckcertificate': True,
        'no_color': True,
        'youtube_skip_dash_manifest': True,
        'cachedir': False,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    # Check for cookies in root or cookies/ folder
    cookies_content = None
    cookies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies")
    cookies_file_default = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
    
    if os.path.exists(cookies_dir) and os.path.isdir(cookies_dir):
        import random
        cookie_files = [f for f in os.listdir(cookies_dir) if f.endswith(".txt")]
        if cookie_files:
            selected_cookie = random.choice(cookie_files)
            ydl_opts['cookiefile'] = os.path.join(cookies_dir, selected_cookie)
            ydl_opts['extractor_args'] = {'youtube': ['player_client=web']}
    
    if 'cookiefile' not in ydl_opts and os.path.exists(cookies_file_default):
        ydl_opts['cookiefile'] = cookies_file_default
        ydl_opts['extractor_args'] = {'youtube': ['player_client=web']}
        
    if 'cookiefile' not in ydl_opts:
        ydl_opts['extractor_args'] = {'youtube': ['player_client=android,web,tv']}

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        downloaded_file = await asyncio.to_thread(_download)
        # yt-dlp might change the extension during merge, so let's find the actual file
        # prepare_filename might return the template with the original extension, but merge changes it to mp4
        base_path = os.path.splitext(downloaded_file)[0]
        mp4_path = base_path + ".mp4"
        if os.path.exists(mp4_path):
            return mp4_path
        elif os.path.exists(downloaded_file):
            return downloaded_file
        else:
            # Fallback: search for the file in the directory
            for file in os.listdir(output_dir):
                if file.startswith(file_id):
                    return os.path.join(output_dir, file)
            raise Exception("Downloaded file not found.")
    except Exception as e:
        if "tiktok.com" in url.lower():
            try:
                return await download_tiktok_fallback(url, output_dir, file_id)
            except Exception as fallback_e:
                raise Exception(f"Failed to download video: {e}\nFallback also failed: {fallback_e}")
        raise Exception(f"Failed to download video: {e}")
