import asyncio
import os
import random
import ffmpeg
import re
import time

async def generate_split_screen(user_video: str, background_video: str, output_video: str, progress_callback=None, is_vertical=True):
    """
    Generates a split-screen video from two input videos.
    Strict Requirements:
    1. Final resolution: 1080x1920 (9:16).
    2. Layout: Top (1080x960) User, Bottom (1080x960) Background.
    3. User Video: Scale to FIT 1080x960 (no cropping), then CENTER.
    4. Background Video: Scale to FILL 1080x960 (center crop).
    5. Implementation: Use vstack of two 1080x960 processed streams.
    6. Audio: From user video.
    7. Duration: Match user video.
    """
    try:
        def _get_info(path):
            return ffmpeg.probe(path)
            
        user_probe = await asyncio.to_thread(_get_info, user_video)
        user_v_stream = next(s for s in user_probe['streams'] if s['codec_type'] == 'video')
        user_duration = float(user_v_stream.get('duration', user_probe['format'].get('duration', 0)))
        has_audio = any(s['codec_type'] == 'audio' for s in user_probe['streams'])
        
        user_input = ffmpeg.input(user_video)
        user_vid = user_input.video.filter('setsar', 1)
        
        if background_video and os.path.exists(background_video):
            # Process top half (User Video)
            user_vid_split = user_vid.split()
            # Background layer: scale to fill 1080x960 and blur
            top_bg = (
                user_vid_split[0]
                .filter('scale', 1080, 960, force_original_aspect_ratio='increase')
                .filter('crop', 1080, 960)
                .filter('boxblur', 20, 5)
            )
            # Foreground layer: scale proportionally to fit in 1080x960 (ensure 100% content)
            top_fg = (
                user_vid_split[1]
                .filter('scale', 1080, 960, force_original_aspect_ratio='decrease')
            )
            # Overlay foreground on background
            user_processed = ffmpeg.overlay(top_bg, top_fg, x='(W-w)/2', y='(H-h)/2')

            bg_probe = await asyncio.to_thread(_get_info, background_video)
            bg_v_stream = next(s for s in bg_probe['streams'] if s['codec_type'] == 'video')
            bg_duration = float(bg_v_stream.get('duration', bg_probe['format'].get('duration', 0)))
            
            if bg_duration > user_duration:
                start_time = random.uniform(0, bg_duration - user_duration)
                bg_input = ffmpeg.input(background_video, ss=start_time, t=user_duration)
            else:
                bg_input = ffmpeg.input(background_video, stream_loop=-1, t=user_duration)
            
            bg_vid = bg_input.video.filter('setsar', 1)
            
            # Process background to 1080x960 (FILL and CENTER CROP)
            # Use 'increase' to ensure it covers 1080x960 regardless of aspect ratio
            bg_processed = (
                bg_vid
                .filter('scale', 1080, 960, force_original_aspect_ratio='increase')
                .filter('crop', 1080, 960)
            )
            
            # Vertical stack
            stacked = ffmpeg.filter([user_processed, bg_processed], 'vstack')
        else:
            # No background - user video centered in full 1080x1920 with blurred background
            user_vid_split = user_vid.split()
            full_bg = (
                user_vid_split[0]
                .filter('scale', 1080, 1920, force_original_aspect_ratio='increase')
                .filter('crop', 1080, 1920)
                .filter('boxblur', 20, 5)
            )
            full_fg = (
                user_vid_split[1]
                .filter('scale', 1080, 1920, force_original_aspect_ratio='decrease')
            )
            stacked = ffmpeg.overlay(full_bg, full_fg, x='(W-w)/2', y='(H-h)/2')
            
        # Final formatting
        stacked = (
            stacked
            .filter('fps', fps=30, round='up')
            .filter('format', 'yuv420p')
        )
        
        # Output with user audio
        if has_audio:
            output = ffmpeg.output(stacked, user_input.audio, output_video, vcodec='libx264', acodec='aac', crf=21, preset='fast')
        else:
            output = ffmpeg.output(stacked, output_video, vcodec='libx264', crf=21, preset='fast')
            
        args = ffmpeg.compile(output, overwrite_output=True)
        # Global flags must be at the beginning
        for arg in reversed(['-progress', 'pipe:2', '-nostats']):
            if arg not in args:
                args.insert(1, arg)
            
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        
        last_update = 0
        stderr_log = []
        buffer = ""
        
        while True:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            
            decoded_chunk = chunk.decode('utf-8', errors='ignore')
            buffer += decoded_chunk
            
            while True:
                r_idx = buffer.find('\r')
                n_idx = buffer.find('\n')
                if r_idx == -1 and n_idx == -1: break
                idx = min(r_idx, n_idx) if r_idx != -1 and n_idx != -1 else (r_idx if r_idx != -1 else n_idx)
                line = buffer[:idx].strip()
                buffer = buffer[idx+1:]
                
                if line:
                    stderr_log.append(line)
                    if len(stderr_log) > 20: stderr_log.pop(0)
                    if progress_callback and user_duration > 0:
                        match = re.search(r"(?:out_)?time=(\d{2}):(\d{2}):(\d{2}\.\d+)", line)
                        if match:
                            h, m, s = match.groups()
                            current_time = int(h) * 3600 + int(m) * 60 + float(s)
                            percent = int((current_time / user_duration) * 100)
                            percent = min(99, max(0, percent))
                            now = time.time()
                            if now - last_update > 2:
                                last_update = now
                                await progress_callback(percent)
                        
        await process.wait()
        if process.returncode != 0:
            stderr_output = '\n'.join(stderr_log[-20:])
            print(f"ffmpeg failed with code {process.returncode}. {stderr_output}")
            raise Exception(f"Ошибка при создании сплит-экрана.")
            
        return output_video
    except Exception as e:
        print(f"Split-screen error: {e}")
        raise Exception(f"Не удалось создать сплит-экран. {e}")
