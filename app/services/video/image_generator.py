import os
import logging
import subprocess
import httpx

logger = logging.getLogger(__name__)

CHARACTER_DIR = "app/static/characters"
THUMBNAIL_DIR = "app/static/thumbnail"
os.makedirs(CHARACTER_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

FONT_PATH = "/nix/store/94k49bsd164kndrvpnj7a3pqd98hnjnv-noto-fonts-cjk-serif-2.002/share/fonts/opentype/noto-cjk/NotoSerifCJK-VF.otf.ttc"


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

    progress_callback(3, f"サムネイル画像を生成中...")

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if api_key:
        try:
            result = _generate_thumbnail_stability(title, genre, api_key, output_path)
            if result:
                progress_callback(3, "サムネイル生成完了 (Stability AI)")
                return output_path
        except Exception as e:
            logger.warning(f"Stability thumbnail failed: {e}")

    resolved_char = _resolve_path(character_image) if character_image else None
    result = _generate_thumbnail_ffmpeg(title, drama_id, output_path, resolved_char)
    progress_callback(3, "サムネイル生成完了 (FFmpeg)")
    return result


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


def _generate_thumbnail_ffmpeg(title: str, drama_id: int, output_path: str, character_image: str = None) -> str:
    short_title = title
    if "「" in title:
        short_title = title.split("「")[-1].rstrip("」")
    if len(short_title) > 12:
        short_title = short_title[:12]

    font_arg = f"fontfile={FONT_PATH}:" if os.path.exists(FONT_PATH) else ""

    if character_image and os.path.exists(character_image):
        vf = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
            f"[bg]colorbalance=bs=0.1:gs=-0.05:rs=-0.1[tinted];"
            f"[tinted]drawbox=y=ih*0.75:w=iw:h=ih*0.25:color=black@0.7:t=fill[boxed];"
            f"[boxed]drawtext={font_arg}text='{_escape_ffmpeg_text(short_title)}':"
            f"fontsize=56:fontcolor=white:x=(w-text_w)/2:y=h*0.80:shadowcolor=black@0.8:shadowx=3:shadowy=3[titled];"
            f"[titled]drawtext={font_arg}text='CEOの扉':"
            f"fontsize=32:fontcolor=0x00897b:x=(w-text_w)/2:y=h*0.90"
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
            f"color=c=0x1a1a2e:s=1080x1920:d=1[bg];"
            f"[bg]drawbox=y=0:w=iw:h=4:color=0x00897b:t=fill[line];"
            f"[line]drawbox=y=ih-4:w=iw:h=4:color=0x00897b:t=fill[line2];"
            f"[line2]drawtext={font_arg}text='{_escape_ffmpeg_text(short_title)}':"
            f"fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h/2-80):shadowcolor=black@0.8:shadowx=3:shadowy=3[titled];"
            f"[titled]drawtext={font_arg}text='CEOの扉':"
            f"fontsize=42:fontcolor=0x00897b:x=(w-text_w)/2:y=(h/2+40)[branded];"
            f"[branded]drawtext={font_arg}text='Episode {drama_id}':"
            f"fontsize=28:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=(h/2+100)"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:s=1080x1920:d=1",
            "-vf", vf.split(";", 1)[1] if ";" in vf else vf,
            "-frames:v", "1",
            output_path
        ]
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:s=1080x1920:d=1",
            "-vframes", "1",
            "-vf", (
                f"drawtext={font_arg}text='{_escape_ffmpeg_text(short_title)}':"
                f"fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h/2-80):shadowcolor=black@0.8:shadowx=3:shadowy=3,"
                f"drawtext={font_arg}text='CEOの扉':"
                f"fontsize=42:fontcolor=0x00897b:x=(w-text_w)/2:y=(h/2+40),"
                f"drawtext={font_arg}text='Episode {drama_id}':"
                f"fontsize=28:fontcolor=0xB2DFDB:x=(w-text_w)/2:y=(h/2+100)"
            ),
            output_path
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.warning(f"FFmpeg thumbnail with text failed: {result.stderr[:200]}")
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
