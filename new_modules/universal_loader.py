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

async def download_video_ytdlp(url: str, output_dir: str) -> str:
    """
    Downloads a video from a given URL using yt-dlp.
    Returns the path to the downloaded video file.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    output_template = os.path.join(output_dir, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'extractor_args': {'youtube': ['player_client=android,web,tv']},
        'nocheckcertificate': True,
        'no_color': True,
        'youtube_skip_dash_manifest': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

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
