import os
import cv2
import torch
import ffmpeg
import numpy as np
from basicsr.archs.rrdbnet_arch import RRDBNet
from realesrgan import RealESRGANer
from gfpgan import GFPGANer

def get_upscaler(scale_factor=4):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    half = True if torch.cuda.is_available() else False
    
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    model_path = '/app/weights/RealESRGAN_x4plus.pth'
    if not os.path.exists(model_path):
        model_path = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth'
        
    upscaler = RealESRGANer(
        scale=4,
        model_path=model_path,
        dni_weight=None,
        model=model,
        tile=0,
        tile_pad=10,
        pre_pad=0,
        half=half,
        device=device
    )
    return upscaler

def upscale_media_sync(input_path, output_path, scale_factor=2):
    ext = os.path.splitext(input_path)[1].lower()
    is_video = ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']
    
    upscaler = get_upscaler(scale_factor)
    
    if not is_video:
        # Process image
        img = cv2.imread(input_path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Failed to read image {input_path}")
            
        output, _ = upscaler.enhance(img, outscale=scale_factor)
        cv2.imwrite(output_path, output)
        return output_path
        
    # Process video
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise ValueError(f"Failed to open video {input_path}")
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    out_width = int(width * scale_factor)
    out_height = int(height * scale_factor)
    
    temp_video_path = output_path + "_temp.mp4"
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(temp_video_path, fourcc, fps, (out_width, out_height))
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        enhanced_frame, _ = upscaler.enhance(frame, outscale=scale_factor)
        out.write(enhanced_frame)
        
    cap.release()
    out.release()
    
    # Merge audio
    try:
        input_video = ffmpeg.input(temp_video_path)
        input_audio = ffmpeg.input(input_path)
        
        probe = ffmpeg.probe(input_path)
        has_audio = any(stream['codec_type'] == 'audio' for stream in probe['streams'])
        
        if has_audio:
            ffmpeg.output(input_video.video, input_audio.audio, output_path, vcodec='libx264', acodec='aac').run(overwrite_output=True, quiet=True)
        else:
            ffmpeg.output(input_video.video, output_path, vcodec='libx264').run(overwrite_output=True, quiet=True)
            
        os.remove(temp_video_path)
    except Exception as e:
        print(f"Error merging audio: {e}")
        if os.path.exists(temp_video_path):
            os.rename(temp_video_path, output_path)
            
    return output_path
