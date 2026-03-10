import os
import time
import logging
import subprocess
import urllib.parse
import httpx

from app.services.video.luma_service import generate_video_luma, generate_image_luma, is_luma_available
from app.services.video.kling_service import generate_scene_video as kling_generate_scene_video, _get_kling_token

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


def _generate_scene_specific_image(scene_description: str, drama_id: int, scene_number: int, progress_callback=None, character_image_urls: list = None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    output_path = os.path.join(SCENE_IMAGES_DIR, f"drama_{drama_id}_scene_{scene_number}_ai.png")

    char_ref_url = character_image_urls[0] if character_image_urls else None

    if is_luma_available():
        ref_label = " (キャラ参照あり)" if char_ref_url else ""
        progress_callback(5, f"シーン{scene_number}: シーン画像生成中 (Luma Photon{ref_label})...")
        img_prompt = (
            f"{scene_description}, photorealistic, cinematic lighting, "
            "dramatic atmosphere, beautiful Japanese actors, "
            "high quality film still, vertical composition 9:16"
        )
        if generate_image_luma(img_prompt, output_path, aspect_ratio="9:16", character_image_url=char_ref_url):
            logger.info(f"Scene {scene_number} specific image via Luma Photon (ref={bool(char_ref_url)}): {output_path}")
            return output_path
        logger.warning(f"Luma Photon scene image failed for scene {scene_number}")

    progress_callback(5, f"シーン{scene_number}: シーン画像生成中 (Pollinations)...")
    poll_img = _generate_scene_image(scene_description, drama_id, scene_number)
    if poll_img:
        return poll_img

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
    emotion_lower = emotion.lower() if emotion else ""

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
        "混乱": "pan_right",
        "罪悪感": "zoom_out",
        "苦痛": "zoom_face",
        "絶望": "zoom_out",
    }

    for key, effect in emotion_effects.items():
        if key in emotion_lower:
            return effect

    cycle = ["zoom_in", "pan_left", "zoom_face", "pan_right", "zoom_out", "pan_up"]
    return cycle[scene_number % len(cycle)]


def _is_kling_available() -> bool:
    try:
        token = _get_kling_token()
        return bool(token)
    except Exception:
        return False


def generate_scene_video(scene_description: str, scene_number: int, drama_id: int,
                         reference_image: str = None, progress_callback=None,
                         emotion: str = "", duration: float = 6,
                         character_image_urls: list = None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(SCENES_DIR, exist_ok=True)
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    scene_image = _generate_scene_specific_image(
        scene_description, drama_id, scene_number, progress_callback,
        character_image_urls=character_image_urls
    )

    kling_ref = scene_image or reference_image

    if _is_kling_available():
        progress_callback(5, f"シーン{scene_number}: AI動画生成中 (Kling AI)...")
        try:
            kling_path = kling_generate_scene_video(
                scene_description=scene_description,
                scene_number=scene_number,
                drama_id=drama_id,
                reference_image=kling_ref,
                progress_callback=progress_callback
            )
            if kling_path and os.path.exists(kling_path) and os.path.getsize(kling_path) > 10000:
                final = _ensure_duration(kling_path, duration)
                logger.info(f"Scene {scene_number} video created via Kling AI: {final}")
                progress_callback(5, f"シーン{scene_number}: Kling AI動画生成完了")
                _cleanup_temp_image(scene_image, reference_image)
                return final
        except Exception as e:
            logger.warning(f"Scene {scene_number}: Kling AI failed: {e}")

    scene_image_url = None
    if scene_image:
        scene_image_url = character_image_urls[0] if character_image_urls else None

    if is_luma_available():
        luma_ref = scene_image_url
        ref_label = " (キャラ参照あり)" if luma_ref else ""
        progress_callback(5, f"シーン{scene_number}: AI動画生成中 (Luma Dream Machine{ref_label})...")
        luma_prompt = (
            f"{scene_description}, cinematic, dramatic lighting, "
            "photorealistic, vertical 9:16, film quality, emotional scene"
        )
        if emotion:
            luma_prompt += f", {emotion} mood"

        luma_success = generate_video_luma(
            prompt=luma_prompt,
            output_path=output_path,
            aspect_ratio="9:16",
            image_url=luma_ref,
        )
        if luma_success and os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
            final = _ensure_duration(output_path, duration)
            logger.info(f"Scene {scene_number} video created via Luma: {final}")
            progress_callback(5, f"シーン{scene_number}: Luma動画生成完了")
            _cleanup_temp_image(scene_image, reference_image)
            return final

        logger.warning(f"Scene {scene_number}: Luma video failed, falling back to image+Ken Burns")

    if not scene_image:
        progress_callback(5, f"シーン{scene_number}: 画像生成中...")
        if is_luma_available():
            progress_callback(5, f"シーン{scene_number}: 画像生成中 (Luma Photon)...")
            luma_img_path = os.path.join(SCENE_IMAGES_DIR, f"drama_{drama_id}_scene_{scene_number}_luma.png")
            img_prompt = (
                f"{scene_description}, photorealistic, cinematic lighting, "
                "dramatic atmosphere, vertical composition 9:16"
            )
            if generate_image_luma(img_prompt, luma_img_path, aspect_ratio="9:16"):
                scene_image = luma_img_path
                logger.info(f"Scene {scene_number} image via Luma Photon")

        if not scene_image:
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
        _cleanup_temp_image(scene_image, reference_image)
        return output_path

    if reference_image and os.path.exists(reference_image) and reference_image != scene_image:
        success = _apply_ken_burns(reference_image, output_path, duration, "zoom_in")
        if success:
            _cleanup_temp_image(scene_image, reference_image)
            return output_path

    return _create_color_placeholder(output_path, duration)


def _cleanup_temp_image(scene_image: str, reference_image: str = None):
    if not scene_image or not os.path.exists(scene_image):
        return
    if scene_image == reference_image:
        return
    if SCENE_IMAGES_DIR in scene_image:
        try:
            os.remove(scene_image)
            logger.debug(f"Cleaned up temp scene image: {scene_image}")
        except Exception:
            pass


def _ensure_duration(video_path: str, target_duration: float) -> str:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10
        )
        actual = float(probe.stdout.strip())
        if abs(actual - target_duration) < 1.0:
            return video_path

        trimmed = video_path.replace(".mp4", "_trimmed.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", trimmed
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and os.path.exists(trimmed):
            os.replace(trimmed, video_path)
            logger.info(f"Video trimmed to {target_duration}s: {video_path}")
    except Exception as e:
        logger.warning(f"Duration adjustment failed: {e}")
    return video_path


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
