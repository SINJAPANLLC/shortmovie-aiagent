import os
import time
import base64
import logging
import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

SCENES_DIR = "app/static/scenes"
os.makedirs(SCENES_DIR, exist_ok=True)

MAX_RETRIES = 1
RETRY_DELAY = 10

_cached_token = {"token": None, "expires": 0}


def _noop(step, msg):
    pass


def _encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_kling_token():
    access_key = os.environ.get("KLING_ACCESS_KEY", "")
    secret_key = os.environ.get("KLING_SECRET_KEY", "")

    if not access_key or not secret_key:
        return os.environ.get("KLING_API_KEY", "")

    now = time.time()
    if _cached_token["token"] and _cached_token["expires"] > now + 60:
        return _cached_token["token"]

    payload = {
        "iss": access_key,
        "exp": int(now) + 1800,
        "nbf": int(now) - 5
    }
    token = pyjwt.encode(payload, secret_key, algorithm="HS256",
                          headers={"alg": "HS256", "typ": "JWT"})
    _cached_token["token"] = token
    _cached_token["expires"] = int(now) + 1800
    logger.info("Kling JWT token generated")
    return token


def generate_scene_video(scene_description: str, scene_number: int, drama_id: int,
                         reference_image: str = None, progress_callback=None,
                         narration: str = "") -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(SCENES_DIR, exist_ok=True)
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    api_key = _get_kling_token()
    if not api_key:
        logger.warning("KLING_API_KEY/KLING_ACCESS_KEY not set, creating placeholder scene")
        return _create_placeholder_scene(drama_id, scene_number, reference_image=reference_image)

    dialogue_part = ""
    if narration:
        dialogue_part = f' The character says: "{narration}"'
    enhanced_prompt = f"{scene_description},{dialogue_part} same character, same face, same clothes, cinematic lighting, vertical video 9:16, dramatic"

    use_image2video = reference_image and os.path.exists(reference_image)

    for attempt in range(MAX_RETRIES):
        try:
            progress_callback(5, f"シーン{scene_number}を生成中 (Kling API)...")

            result_url = None
            if use_image2video:
                result_url = _try_image2video(api_key, enhanced_prompt, reference_image, progress_callback, scene_number)
                if not result_url:
                    logger.warning(f"image2video failed for scene {scene_number}, falling back to text2video")
                    use_image2video = False

            if not result_url:
                result_url = _try_text2video(api_key, enhanced_prompt, progress_callback, scene_number)

            if result_url:
                _download_video(result_url, output_path)
                return output_path

            logger.warning(f"Kling API returned no video for scene {scene_number} (attempt {attempt+1})")

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"Kling API error scene {scene_number}, retrying in {RETRY_DELAY}s: {e}")
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"Kling API failed for scene {scene_number}: {e}")

    logger.warning(f"Using placeholder for scene {scene_number}")
    return _create_placeholder_scene(drama_id, scene_number, reference_image=reference_image)


def _try_image2video(api_key, prompt, reference_image, progress_callback, scene_number):
    try:
        file_size = os.path.getsize(reference_image)
        if file_size > 10 * 1024 * 1024:
            logger.warning(f"Image too large for Kling i2v ({file_size} bytes), skipping")
            return None

        ext = os.path.splitext(reference_image)[1].lower()
        if ext == ".png":
            import subprocess as sp
            jpg_path = reference_image.replace(".png", "_kling_tmp.jpg")
            sp.run(["ffmpeg", "-y", "-i", reference_image, "-q:v", "2", jpg_path],
                   capture_output=True, timeout=10)
            if os.path.exists(jpg_path):
                reference_image = jpg_path
                ext = ".jpg"

        image_b64 = _encode_image_base64(reference_image)

        if reference_image.endswith("_kling_tmp.jpg") and os.path.exists(reference_image):
            os.remove(reference_image)

        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/image2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v3-omni",
                    "prompt": prompt,
                    "image": image_b64,
                    "duration": "10",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on"
                }
            )

            if response.status_code == 200:
                result = response.json()
                task_id = result.get("data", {}).get("task_id")
                if task_id:
                    return _poll_task(api_key, task_id, progress_callback, scene_number, "image2video")

            logger.warning(f"Kling image2video response: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Kling image2video error for scene {scene_number}: {e}")
    return None


def _try_text2video(api_key, prompt, progress_callback, scene_number):
    try:
        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/text2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v3-omni",
                    "prompt": prompt,
                    "duration": "10",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on"
                }
            )

            if response.status_code == 200:
                result = response.json()
                task_id = result.get("data", {}).get("task_id")
                if task_id:
                    return _poll_task(api_key, task_id, progress_callback, scene_number, "text2video")

            logger.warning(f"Kling text2video response: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Kling text2video error for scene {scene_number}: {e}")
    return None


def _poll_task(api_key: str, task_id: str, progress_callback, scene_number: int, endpoint_name: str = "text2video", max_wait: int = 600):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            current_token = _get_kling_token()
            with httpx.Client(timeout=60) as client:
                response = client.get(
                    f"https://api.klingai.com/v1/videos/{endpoint_name}/{task_id}",
                    headers={"Authorization": f"Bearer {current_token}"}
                )
                if response.status_code == 401:
                    _cached_token["token"] = None
                    _cached_token["expires"] = 0
                    logger.warning(f"Kling token expired, refreshing...")
                    time.sleep(2)
                    continue
                if response.status_code == 200:
                    data = response.json().get("data", {})
                    status = data.get("task_status", "")
                    if status == "succeed":
                        videos = data.get("task_result", {}).get("videos", [])
                        if videos:
                            return videos[0].get("url")
                    elif status == "failed":
                        logger.error(f"Kling task failed for scene {scene_number}")
                        return None
                    progress_callback(5, f"シーン{scene_number}生成中... ({status})")
        except Exception as e:
            logger.warning(f"Polling error: {e}")
        time.sleep(10)
    return None


def _download_video(url: str, output_path: str):
    with httpx.Client(timeout=120) as client:
        response = client.get(url)
        with open(output_path, "wb") as f:
            f.write(response.content)


def _create_placeholder_scene(drama_id: int, scene_number: int, duration: float = 6, reference_image: str = None) -> str:
    import subprocess
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    if reference_image and os.path.exists(reference_image):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", reference_image,
            "-t", str(duration),
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                f"zoompan=z='min(zoom+0.0008,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*25)}:s=1080x1920:fps=25"
            ),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "25",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return output_path
        logger.warning(f"Ken Burns placeholder failed, falling back to simple: {result.stderr[:200]}")

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
