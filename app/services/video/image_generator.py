import os
import logging
import subprocess
import urllib.parse
import httpx

logger = logging.getLogger(__name__)

CHARACTER_DIR = "app/static/characters"
THUMBNAIL_DIR = "app/static/thumbnail"
os.makedirs(CHARACTER_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

FONT_PATH = "/nix/store/94k49bsd164kndrvpnj7a3pqd98hnjnv-noto-fonts-cjk-serif-2.002/share/fonts/opentype/noto-cjk/NotoSerifCJK-VF.otf.ttc"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true&seed={seed}"


def _noop(step, msg):
    pass


def _generate_pollinations_image(prompt: str, output_path: str, width: int = 1080, height: int = 1920, seed: int = 42) -> bool:
    encoded_prompt = urllib.parse.quote(prompt, safe='')
    url = POLLINATIONS_URL.format(prompt=encoded_prompt, w=width, h=height, seed=seed)
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code == 200 and len(response.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Pollinations image generated: {output_path} ({len(response.content)} bytes)")
                return True
    except Exception as e:
        logger.warning(f"Pollinations image error: {e}")
    return False


def generate_character_image(character_description: str, drama_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(CHARACTER_DIR, exist_ok=True)
    output_path = os.path.join(CHARACTER_DIR, f"drama_{drama_id}_character.png")

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if api_key:
        prompt = (
            f"{character_description}, "
            "photorealistic, beautiful character portrait, high quality, "
            "dramatic lighting, detailed face, expressive eyes, "
            "vertical composition 9:16, cinematic"
        )
        progress_callback(3, f"キャラクター画像を生成中 (Stable Diffusion)...")
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    "https://api.stability.ai/v2beta/stable-image/generate/sd3",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "image/*"
                    },
                    data={
                        "prompt": prompt,
                        "negative_prompt": "blurry, low quality, deformed, ugly, watermark, text, anime, cartoon, illustration",
                        "aspect_ratio": "9:16",
                        "output_format": "png",
                    },
                    files={"none": ("", "")}
                )
                if response.status_code == 200:
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    progress_callback(3, "キャラクター画像生成完了 (Stability AI)")
                    return output_path
                else:
                    logger.warning(f"Stability API error: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            logger.error(f"Stability AI character generation failed: {e}")

    progress_callback(3, f"キャラクター画像を生成中 (Pollinations AI)...")
    poll_prompt = (
        f"{character_description}, photorealistic, cinematic portrait, "
        "dramatic lighting, detailed face, expressive eyes, "
        "vertical composition, high quality photography"
    )
    if _generate_pollinations_image(poll_prompt, output_path, seed=drama_id * 7):
        progress_callback(3, "キャラクター画像生成完了 (Pollinations)")
        return output_path

    return _create_placeholder_image(output_path, "Character", drama_id)


def generate_thumbnail(title: str, genre: str, drama_id: int, character_image: str = None, progress_callback=None, episode_number: int = None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    output_path = os.path.join(THUMBNAIL_DIR, f"drama_{drama_id}.png")

    progress_callback(3, f"サムネイル画像を生成中...")

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if api_key:
        try:
            result = _generate_thumbnail_stability(title, genre, api_key, output_path)
            if result:
                _compress_thumbnail(output_path)
                progress_callback(3, "サムネイル生成完了 (Stability AI)")
                return output_path
        except Exception as e:
            logger.warning(f"Stability thumbnail failed: {e}")

    genre_style = {
        "CEOドラマ": "luxury penthouse office, city skyline at night, dramatic blue lighting",
        "恋愛": "romantic sunset, cherry blossoms, warm golden light",
        "復讐": "dramatic dark atmosphere, fire reflections, intense shadows",
    }
    style = genre_style.get(genre, "cinematic dramatic lighting, luxury setting")
    poll_prompt = (
        f"dramatic thumbnail for short drama, {style}, "
        "beautiful Japanese woman emotional expression, "
        "photorealistic, high contrast, eye-catching, vertical 9:16"
    )
    progress_callback(3, f"サムネイル画像を生成中 (Pollinations AI)...")
    poll_path = output_path.replace(".png", "_poll.png")
    if _generate_pollinations_image(poll_prompt, poll_path, seed=drama_id * 13):
        result = _generate_thumbnail_ffmpeg(title, drama_id, output_path, poll_path, episode_number)
        if os.path.exists(poll_path) and poll_path != output_path:
            os.remove(poll_path)
        _compress_thumbnail(output_path)
        progress_callback(3, "サムネイル生成完了 (Pollinations + FFmpeg)")
        return result

    resolved_char = _resolve_path(character_image) if character_image else None
    result = _generate_thumbnail_ffmpeg(title, drama_id, output_path, resolved_char, episode_number)
    _compress_thumbnail(output_path)
    progress_callback(3, "サムネイル生成完了 (FFmpeg)")
    return result


def _compress_thumbnail(path: str, max_bytes: int = 1_800_000):
    if not os.path.exists(path):
        return
    size = os.path.getsize(path)
    if size <= max_bytes:
        return
    jpg_path = path.replace(".png", "_compressed.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-i", path,
        "-q:v", "5",
        "-frames:v", "1",
        jpg_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode == 0 and os.path.exists(jpg_path):
        os.remove(path)
        os.rename(jpg_path, path)
        logger.info(f"Thumbnail compressed: {size} -> {os.path.getsize(path)} bytes")


def _generate_thumbnail_stability(title: str, genre: str, api_key: str, output_path: str) -> bool:
    genre_style = {
        "恋愛": "romantic atmosphere, cherry blossoms, warm colors, sunset",
        "浮気": "dramatic tension, dark shadows, mystery, split scene",
        "復讐": "intense dramatic lighting, fire, dark tones, powerful expression",
        "CEOドラマ": "luxury office, city skyline, elegant, modern, blue tones",
        "怖い話": "horror atmosphere, dark, eerie lighting, shadows, cold tones",
    }
    style_desc = genre_style.get(genre, "cinematic dramatic lighting, luxury office")

    prompt = (
        f"YouTube Shorts thumbnail, dramatic scene, "
        f"{style_desc}, "
        "photorealistic, eye-catching vertical thumbnail, dramatic composition, "
        "high contrast, vibrant colors, vertical 9:16 aspect ratio, "
        "beautiful Japanese woman in dramatic pose, cinematic lighting"
    )

    with httpx.Client(timeout=120) as client:
        response = client.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "image/*"
            },
            data={
                "prompt": prompt,
                "negative_prompt": "blurry, low quality, deformed, text, watermark, anime, cartoon",
                "aspect_ratio": "9:16",
                "output_format": "png",
            },
            files={"none": ("", "")}
        )

        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            logger.info(f"Thumbnail generated via Stability: {output_path}")
            return True
        else:
            logger.warning(f"Stability API thumbnail error: {response.status_code} - {response.text[:200]}")
            return False


def _resolve_path(path: str) -> str:
    if not path:
        return None
    if path.startswith("/static/"):
        full = "app" + path
    elif path.startswith("app/static/"):
        full = path
    elif path.startswith("static/"):
        full = "app/" + path
    else:
        full = path
    if os.path.exists(full):
        return full
    return None


def _generate_thumbnail_ffmpeg(title: str, drama_id: int, output_path: str, character_image: str = None, episode_number: int = None) -> str:
    subtitle = title
    if "「" in title:
        subtitle = title.split("「")[-1].rstrip("」")
    if len(subtitle) > 10:
        subtitle = subtitle[:10]

    ep_num = episode_number or drama_id
    ep_text = f"第{ep_num}話"

    font_arg = f"fontfile={FONT_PATH}:" if os.path.exists(FONT_PATH) else ""

    if character_image and os.path.exists(character_image):
        vf = (
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"eq=brightness=-0.08:contrast=1.3:saturation=1.2,"
            f"drawbox=y=0:w=iw:h=ih*0.12:color=black@0.85:t=fill,"
            f"drawbox=y=ih*0.72:w=iw:h=ih*0.28:color=black@0.80:t=fill,"
            f"drawbox=y=ih*0.72:w=iw:h=6:color=0x00897b:t=fill,"
            f"drawtext={font_arg}text='CEOの扉':fontsize=38:fontcolor=0x00E5A0:x=(w-text_w)/2:y=h*0.035:shadowcolor=black@0.9:shadowx=2:shadowy=2,"
            f"drawtext={font_arg}text='{ep_text}':fontsize=30:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=h*0.075:shadowcolor=black@0.8:shadowx=1:shadowy=1,"
            f"drawtext={font_arg}text='{_escape_ffmpeg_text(subtitle)}':fontsize=72:fontcolor=white:x=(w-text_w)/2:y=h*0.78:shadowcolor=black:shadowx=4:shadowy=4,"
            f"drawtext={font_arg}text='▶ 続きが気になる...':fontsize=28:fontcolor=0xFFD700:x=(w-text_w)/2:y=h*0.90:shadowcolor=black@0.8:shadowx=2:shadowy=2"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", character_image,
            "-vf", vf,
            "-frames:v", "1",
            output_path
        ]
    else:
        vf = (
            f"drawbox=y=0:w=iw:h=ih*0.15:color=0x00897b:t=fill,"
            f"drawbox=y=ih*0.70:w=iw:h=ih*0.30:color=0x004D40:t=fill,"
            f"drawbox=y=ih*0.70:w=iw:h=6:color=0x00E5A0:t=fill,"
            f"drawtext={font_arg}text='CEOの扉':fontsize=52:fontcolor=white:x=(w-text_w)/2:y=h*0.04:shadowcolor=black@0.5:shadowx=2:shadowy=2,"
            f"drawtext={font_arg}text='{ep_text}':fontsize=36:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=h*0.095,"
            f"drawtext={font_arg}text='♦':fontsize=120:fontcolor=0x00897b@0.3:x=(w-text_w)/2:y=h*0.35,"
            f"drawtext={font_arg}text='{_escape_ffmpeg_text(subtitle)}':fontsize=76:fontcolor=white:x=(w-text_w)/2:y=h*0.78:shadowcolor=black:shadowx=4:shadowy=4,"
            f"drawtext={font_arg}text='▶ 続きが気になる...':fontsize=28:fontcolor=0xFFD700:x=(w-text_w)/2:y=h*0.90:shadowcolor=black@0.8:shadowx=2:shadowy=2"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:s=1080x1920:d=1",
            "-vframes", "1",
            "-vf", vf,
            output_path
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.warning(f"FFmpeg thumbnail failed: {result.stderr[:300]}")
        cmd_simple = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:s=1080x1920:d=1",
            "-vframes", "1",
            output_path
        ]
        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=30)

    logger.info(f"FFmpeg thumbnail generated: {output_path}")
    return output_path


def _escape_ffmpeg_text(text: str) -> str:
    text = text.replace("'", "'\\''")
    text = text.replace(":", "\\:")
    text = text.replace("\\", "\\\\")
    return text


def _create_placeholder_image(output_path: str, label: str, drama_id: int) -> str:
    font_arg = f"fontfile={FONT_PATH}:" if os.path.exists(FONT_PATH) else ""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "color=c=0x00897b:s=1080x1920:d=1",
        "-vframes", "1",
        "-vf", (
            f"drawtext={font_arg}text='CEOの扉':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h/2-60),"
            f"drawtext={font_arg}text='Episode {drama_id}':fontsize=36:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=(h/2+20)"
        ),
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        cmd_simple = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x00897b:s=1080x1920:d=1",
            "-vframes", "1",
            output_path
        ]
        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=30)

    return output_path
