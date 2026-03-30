import asyncio
import os
import uuid
import ffmpeg
import urllib.request

FONTS = {
    "roboto": "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Regular.ttf",
    "montserrat": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf",
    "oswald": "https://raw.githubusercontent.com/googlefonts/OswaldFont/master/fonts/ttf/Oswald-Bold.ttf",
    "caveat": "https://raw.githubusercontent.com/googlefonts/caveat/main/fonts/ttf/Caveat-Bold.ttf"
}

def _ensure_font(font_name="roboto"):
    font_name = font_name.lower()
    if font_name not in FONTS:
        font_name = "roboto"
        
    font_filename = f"{font_name}.ttf"
    font_path = os.path.join(os.path.dirname(__file__), font_filename)
    
    if not os.path.exists(font_path):
        print(f"Downloading {font_name} font for watermarks...")
        url = FONTS[font_name]
        urllib.request.urlretrieve(url, font_path)
        
    return font_path

async def add_watermark(input_video: str, output_video: str, watermark_text: str, 
                        is_dynamic: bool = False, size: str = "medium", 
                        position: str = "bottom_right", dynamic_type: str = "floating",
                        font_name: str = "roboto"):
    """
    Adds a static or dynamic watermark to a video using FFmpeg drawtext.
    size: 'small', 'medium', 'large'
    position: 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center'
    dynamic_type: 'floating', 'bouncing', 'scrolling'
    """
    font_path = _ensure_font(font_name)
    
    try:
        # Determine font size based on video height (using expressions in drawtext)
        # small: h/25, medium: h/15, large: h/8
        if size == "small":
            fontsize_expr = "(h/25)"
        elif size == "large":
            fontsize_expr = "(h/8)"
        else:
            fontsize_expr = "(h/15)"
            
        # Escape text for drawtext
        escaped_text = watermark_text.replace("'", "\\'").replace(":", "\\:")
        
        if is_dynamic:
            if dynamic_type == "scrolling":
                # Scrolls from right to left
                x_expr = "w-mod(t*100,w+tw)"
                y_expr = "(h-th)/2"
            elif dynamic_type == "bouncing":
                # Bounces around
                x_expr = "abs(w-tw-abs(mod(t*150,2*(w-tw))))"
                y_expr = "abs(h-th-abs(mod(t*100,2*(h-th))))"
            else: # floating
                # Smoothly floats around using sine and cosine
                x_expr = "(w-tw)/2+(w-tw)/2*sin(t/2)"
                y_expr = "(h-th)/2+(h-th)/2*cos(t/3)"
        else:
            # Add padding to avoid clipping
            padding = "20"
            if position == "top_left":
                x_expr = padding
                y_expr = padding
            elif position == "top_right":
                x_expr = f"w-tw-{padding}"
                y_expr = padding
            elif position == "bottom_left":
                x_expr = padding
                y_expr = f"h-th-{padding}"
            elif position == "center":
                x_expr = "(w-tw)/2"
                y_expr = "(h-th)/2"
            else: # bottom_right
                x_expr = f"w-tw-{padding}"
                y_expr = f"h-th-{padding}"

        input_stream = ffmpeg.input(input_video)
        
        # Apply drawtext filter
        video_stream = input_stream.video.filter_('drawtext', 
            fontfile=font_path,
            text=watermark_text,
            fontsize=fontsize_expr,
            fontcolor='white@0.7',
            x=x_expr,
            y=y_expr,
            shadowcolor='black@0.5',
            shadowx=2,
            shadowy=2
        )
        
        # Output
        output = ffmpeg.output(video_stream, input_stream.audio, output_video, vcodec='libx264', acodec='copy')
        
        def _run():
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            
        await asyncio.to_thread(_run)
        return output_video
        
    except ffmpeg.Error as e:
        err_msg = e.stderr.decode('utf8') if e.stderr else str(e)
        raise Exception(f"FFmpeg error: {err_msg}")
    except Exception as e:
        raise Exception(f"Failed to add watermark: {e}")
