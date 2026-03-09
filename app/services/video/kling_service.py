import os
import time
import base64
import logging
import httpx

logger = logging.getLogger(__name__)

SCENES_DIR = "app/static/scenes"
os.makedirs(SCENES_DIR, exist_ok=True)

MAX_RETRIES = 3
RETRY_DELAY = 30


def _noop(step, msg):
    pass


def _encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def generate_scene_video(scene_description: str, scene_number: int, drama_id: int,
                         reference_image: str = None, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(SCENES_DIR, exist_ok=True)
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    api_key = os.environ.get("KLING_API_KEY", "")
    if not api_key:
        logger.warning("KLING_API_KEY not set, creating placeholder scene")
        return _create_placeholder_scene(drama_id, scene_number)

    enhanced_prompt = f"{scene_description}, same character, same face, same clothes, cinematic lighting, vertical video 9:16, dramatic"

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
    return _create_placeholder_scene(drama_id, scene_number)


def _try_image2video(api_key, prompt, reference_image, progress_callback, scene_number):
    try:
        image_b64 = _encode_image_base64(reference_image)
        ext = os.path.splitext(reference_image)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        image_data_url = f"data:{mime};base64,{image_b64}"

        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/image2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v1",
                    "prompt": prompt,
                    "image": image_data_url,
                    "duration": "5",
                    "aspect_ratio": "9:16",
                    "mode": "std"
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
                    "prompt": prompt,
                    "duration": "5",
                    "aspect_ratio": "9:16",
                    "model": "kling-v1"
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


def _poll_task(api_key: str, task_id: str, progress_callback, scene_number: int, endpoint_name: str = "text2video", max_wait: int = 300):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            with httpx.Client(timeout=60) as client:
                response = client.get(
                    f"https://api.klingai.com/v1/videos/{endpoint_name}/{task_id}",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
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


def _create_placeholder_scene(drama_id: int, scene_number: int, duration: float = 6) -> str:
    import subprocess
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1a2e2e:s=1080x1920:d={duration}",
        "-vf", f"drawtext=text='Scene {scene_number}':fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        cmd_simple = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x1a2e2e:s=1080x1920:d={duration}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            output_path
        ]
        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=60)

    return output_path
