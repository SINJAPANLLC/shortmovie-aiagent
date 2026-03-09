import os
import logging
import subprocess
import httpx

logger = logging.getLogger(__name__)

CHARACTER_DIR = "app/static/characters"
THUMBNAIL_DIR = "app/static/thumbnail"
os.makedirs(CHARACTER_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)


def _noop(step, msg):
    pass


def generate_character_image(character_description: str, drama_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(CHARACTER_DIR, exist_ok=True)
    output_path = os.path.join(CHARACTER_DIR, f"drama_{drama_id}_character.png")

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if not api_key:
        logger.warning("STABILITY_API_KEY not set, creating placeholder character")
        return _create_placeholder_image(output_path, "Character", drama_id)

    prompt = (
        f"{character_description}, "
        "anime style, beautiful character portrait, high quality, "
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
                    "negative_prompt": "blurry, low quality, deformed, ugly, watermark, text",
                    "aspect_ratio": "9:16",
                    "output_format": "png",
                },
                files={"none": ("", "")}
            )

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                progress_callback(3, "キャラクター画像生成完了")
                logger.info(f"Character image generated: {output_path}")
                return output_path
            else:
                logger.warning(f"Stability API error: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"Character image generation failed: {e}")

    return _create_placeholder_image(output_path, "Character", drama_id)


def generate_thumbnail(title: str, genre: str, drama_id: int, character_image: str = None, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    output_path = os.path.join(THUMBNAIL_DIR, f"drama_{drama_id}.png")

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if not api_key:
        logger.warning("STABILITY_API_KEY not set, creating placeholder thumbnail")
        return _create_placeholder_image(output_path, "Thumbnail", drama_id)

    genre_style = {
        "恋愛": "romantic atmosphere, cherry blossoms, warm colors, sunset",
        "浮気": "dramatic tension, dark shadows, mystery, split scene",
        "復讐": "intense dramatic lighting, fire, dark tones, powerful expression",
        "CEOドラマ": "luxury office, city skyline, elegant, modern, blue tones",
        "怖い話": "horror atmosphere, dark, eerie lighting, shadows, cold tones",
    }
    style_desc = genre_style.get(genre, "cinematic dramatic lighting")

    prompt = (
        f"YouTube Shorts thumbnail for drama titled '{title}', "
        f"{style_desc}, "
        "eye-catching vertical thumbnail, dramatic composition, "
        "anime style, high contrast, vibrant colors, "
        "vertical 9:16 aspect ratio"
    )

    progress_callback(3, f"サムネイル画像を生成中 (Stable Diffusion)...")

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
                    "negative_prompt": "blurry, low quality, deformed, text, watermark",
                    "aspect_ratio": "9:16",
                    "output_format": "png",
                },
                files={"none": ("", "")}
            )

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                progress_callback(3, "サムネイル生成完了")
                logger.info(f"Thumbnail generated: {output_path}")
                return output_path
            else:
                logger.warning(f"Stability API thumbnail error: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"Thumbnail generation failed: {e}")

    return _create_placeholder_image(output_path, "Thumbnail", drama_id)


def _create_placeholder_image(output_path: str, label: str, drama_id: int) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "color=c=0x00897b:s=1080x1920:d=1",
        "-vframes", "1",
        "-vf", (
            "drawtext=text='CEOの扉':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h/2-60),"
            f"drawtext=text='Episode {drama_id}':fontsize=36:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=(h/2+20)"
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
