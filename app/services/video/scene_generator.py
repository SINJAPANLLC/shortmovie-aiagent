import os
import time
import logging
import subprocess
import urllib.parse
import httpx

logger = logging.getLogger(__name__)

SCENES_DIR = "app/static/scenes"
SCENE_IMAGES_DIR = "app/static/scene_images"
os.makedirs(SCENES_DIR, exist_ok=True)
os.makedirs(SCENE_IMAGES_DIR, exist_ok=True)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width=1080&height=1920&nologo=true&seed={seed}"
MAX_IMAGE_RETRIES = 2


def _noop(step, msg):
    pass


def _generate_scene_image(prompt: str, drama_id: int, scene_number: int) -> str:
    output_path = os.path.join(SCENE_IMAGES_DIR, f"drama_{drama_id}_scene_{scene_number}.png")

    enhanced_prompt = (
        f"{prompt}, photorealistic, cinematic lighting, "
        "dramatic atmosphere, film grain, shallow depth of field, "
        "vertical composition 9:16, 1080x1920"
    )

    encoded_prompt = urllib.parse.quote(enhanced_prompt, safe='')
    seed = (drama_id * 100 + scene_number) % 99999

    for attempt in range(MAX_IMAGE_RETRIES):
        try:
            url = POLLINATIONS_URL.format(prompt=encoded_prompt, seed=seed + attempt)
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                response = client.get(url)
                if response.status_code == 200 and len(response.content) > 5000:
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"Scene image generated via Pollinations: {output_path} ({len(response.content)} bytes)")
                    return output_path
                else:
                    logger.warning(f"Pollinations response: status={response.status_code}, size={len(response.content)}")
        except Exception as e:
            logger.warning(f"Pollinations image error (attempt {attempt+1}): {e}")
            time.sleep(2)

    return None


def _apply_ken_burns(image_path: str, output_path: str, duration: float = 6,
                     effect_type: str = "zoom_in") -> bool:
    d_frames = int(duration * 25)

    effects = {
        "zoom_in": f"zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d_frames}:s=1080x1920:fps=25",
        "zoom_out": f"zoompan=z='if(eq(on,1),1.5,max(zoom-0.0015,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d_frames}:s=1080x1920:fps=25",
        "pan_left": f"zoompan=z='1.2':x='if(eq(on,1),0,min(x+2,iw-iw/zoom))':y='ih/2-(ih/zoom/2)':d={d_frames}:s=1080x1920:fps=25",
        "pan_right": f"zoompan=z='1.2':x='if(eq(on,1),iw-iw/zoom,max(x-2,0))':y='ih/2-(ih/zoom/2)':d={d_frames}:s=1080x1920:fps=25",
        "pan_up": f"zoompan=z='1.2':x='iw/2-(iw/zoom/2)':y='if(eq(on,1),ih-ih/zoom,max(y-2,0))':d={d_frames}:s=1080x1920:fps=25",
        "zoom_face": f"zoompan=z='min(zoom+0.002,1.8)':x='iw/2-(iw/zoom/2)':y='ih/3-(ih/zoom/3)':d={d_frames}:s=1080x1920:fps=25",
    }

    zp = effects.get(effect_type, effects["zoom_in"])

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-t", str(duration),
        "-vf", (
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"{zp},"
            f"eq=brightness=0.02:contrast=1.05:saturation=1.1"
        ),
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.warning(f"Ken Burns effect failed: {result.stderr[:200]}")
        return False
    return True


def _pick_effect(scene_number: int, emotion: str = "") -> str:
    emotion_effects = {
        "衝撃": "zoom_face",
        "驚き": "zoom_face",
        "切なさ": "zoom_out",
        "悲しみ": "zoom_out",
        "緊張": "zoom_in",
        "不安": "pan_left",
        "期待": "zoom_in",
        "温かさ": "pan_up",
        "怒り": "zoom_face",
        "動揺": "pan_right",
    }

    if emotion and emotion in emotion_effects:
        return emotion_effects[emotion]

    cycle = ["zoom_in", "pan_left", "zoom_face", "pan_right", "zoom_out", "pan_up"]
    return cycle[scene_number % len(cycle)]


def generate_scene_video(scene_description: str, scene_number: int, drama_id: int,
                         reference_image: str = None, progress_callback=None,
                         emotion: str = "", duration: float = 6) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(SCENES_DIR, exist_ok=True)
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    progress_callback(5, f"シーン{scene_number}: 画像生成中 (Pollinations AI)...")
    scene_image = _generate_scene_image(scene_description, drama_id, scene_number)

    if not scene_image:
        if reference_image and os.path.exists(reference_image):
            scene_image = reference_image
            logger.info(f"Scene {scene_number}: using reference image as fallback")
        else:
            logger.warning(f"Scene {scene_number}: no image available, creating color placeholder")
            return _create_color_placeholder(output_path, duration)

    effect = _pick_effect(scene_number, emotion)
    progress_callback(5, f"シーン{scene_number}: {effect} エフェクト適用中...")

    success = _apply_ken_burns(scene_image, output_path, duration, effect)
    if success:
        logger.info(f"Scene {scene_number} video created: {output_path} (effect: {effect})")
        return output_path

    if reference_image and os.path.exists(reference_image) and reference_image != scene_image:
        success = _apply_ken_burns(reference_image, output_path, duration, "zoom_in")
        if success:
            return output_path

    return _create_color_placeholder(output_path, duration)


def _create_color_placeholder(output_path: str, duration: float = 6) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1a2e2e:s=1080x1920:d={duration}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return output_path
