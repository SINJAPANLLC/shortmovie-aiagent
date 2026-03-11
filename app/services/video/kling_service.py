import os
import time
import base64
import logging
import threading
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


def _prepare_reference_image(reference_image):
    ext = os.path.splitext(reference_image)[1].lower()
    tmp_path = None
    if ext == ".png":
        import subprocess as sp
        jpg_path = reference_image.replace(".png", "_kling_tmp.jpg")
        sp.run(["ffmpeg", "-y", "-i", reference_image, "-q:v", "2", jpg_path],
               capture_output=True, timeout=10)
        if os.path.exists(jpg_path):
            reference_image = jpg_path
            tmp_path = jpg_path

    image_b64 = _encode_image_base64(reference_image)

    if tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)

    return image_b64


def _submit_image2video(api_key, prompt, reference_image, scene_number):
    try:
        file_size = os.path.getsize(reference_image)
        if file_size > 10 * 1024 * 1024:
            logger.warning(f"Image too large for Kling i2v ({file_size} bytes), skipping")
            return None, None

        image_b64 = _prepare_reference_image(reference_image)

        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/image2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v3",
                    "prompt": prompt,
                    "image": image_b64,
                    "duration": "15",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on",
                }
            )

            if response.status_code == 200:
                result = response.json()
                task_id = result.get("data", {}).get("task_id")
                if task_id:
                    logger.info(f"Scene {scene_number}: Kling image2video task submitted: {task_id}")
                    return task_id, "image2video"

            logger.warning(f"Kling image2video submit for scene {scene_number}: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Kling image2video submit error for scene {scene_number}: {e}")
    return None, None


def _submit_text2video(api_key, prompt, scene_number):
    try:
        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/text2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v3",
                    "prompt": prompt,
                    "duration": "15",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on",
                }
            )

            if response.status_code == 200:
                result = response.json()
                task_id = result.get("data", {}).get("task_id")
                if task_id:
                    logger.info(f"Scene {scene_number}: Kling text2video task submitted: {task_id}")
                    return task_id, "text2video"

            logger.warning(f"Kling text2video submit for scene {scene_number}: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Kling text2video submit error for scene {scene_number}: {e}")
    return None, None


def submit_kling_task(scene_description: str, scene_number: int,
                      reference_image: str = None, narration: str = ""):
    api_key = _get_kling_token()
    if not api_key:
        return None, None

    dialogue_part = ""
    if narration:
        dialogue_part = f' The character says: "{narration}"'
    full_prompt = f"{scene_description},{dialogue_part} same character, same face, same clothes, cinematic lighting, vertical video 9:16, dramatic"
    enhanced_prompt = full_prompt[:2500]

    use_image2video = reference_image and os.path.exists(reference_image)

    if use_image2video:
        task_id, endpoint = _submit_image2video(api_key, enhanced_prompt, reference_image, scene_number)
        if task_id:
            return task_id, endpoint
        logger.warning(f"image2video submit failed for scene {scene_number}, trying text2video")

    task_id, endpoint = _submit_text2video(api_key, enhanced_prompt, scene_number)
    return task_id, endpoint


def poll_kling_tasks(tasks: list, progress_callback=None, max_wait: int = 900):
    if progress_callback is None:
        progress_callback = _noop

    results = {}
    pending = {}
    for t in tasks:
        scene_number = t["scene_number"]
        task_id = t.get("task_id")
        endpoint = t.get("endpoint", "text2video")
        if task_id:
            pending[scene_number] = {"task_id": task_id, "endpoint": endpoint}
        else:
            results[scene_number] = None

    if not pending:
        return results

    total = len(pending)
    progress_callback(5, f"Kling AI: {total}シーンを並行生成中...")

    start = time.time()
    while pending and time.time() - start < max_wait:
        try:
            current_token = _get_kling_token()
            done_scenes = []
            for scene_number, info in pending.items():
                try:
                    with httpx.Client(timeout=60) as client:
                        response = client.get(
                            f"https://api.klingai.com/v1/videos/{info['endpoint']}/{info['task_id']}",
                            headers={"Authorization": f"Bearer {current_token}"}
                        )
                        if response.status_code == 401:
                            _cached_token["token"] = None
                            _cached_token["expires"] = 0
                            logger.warning("Kling token expired, refreshing...")
                            break
                        if response.status_code == 200:
                            data = response.json().get("data", {})
                            status = data.get("task_status", "")
                            if status == "succeed":
                                videos = data.get("task_result", {}).get("videos", [])
                                if videos:
                                    results[scene_number] = videos[0].get("url")
                                else:
                                    results[scene_number] = None
                                done_scenes.append(scene_number)
                                completed = len(results)
                                progress_callback(5, f"Kling AI: シーン{scene_number}完了 ({completed}/{total})")
                            elif status == "failed":
                                fail_msg = data.get("task_status_msg", "")
                                logger.error(f"Kling task failed for scene {scene_number}: {fail_msg}")
                                results[scene_number] = None
                                done_scenes.append(scene_number)
                                progress_callback(5, f"Kling AI: シーン{scene_number}失敗 — フォールバックへ")
                            else:
                                elapsed = int(time.time() - start)
                                progress_callback(5, f"Kling AI: シーン{scene_number} {status} ({elapsed}秒経過)")
                except Exception as e:
                    logger.warning(f"Polling error for scene {scene_number}: {e}")

            for sn in done_scenes:
                del pending[sn]

            if not pending:
                break
        except Exception as e:
            logger.warning(f"Polling cycle error: {e}")

        time.sleep(10)

    for scene_number in pending:
        logger.warning(f"Kling task timed out for scene {scene_number}")
        results[scene_number] = None

    completed_count = sum(1 for v in results.values() if v)
    progress_callback(5, f"Kling AI: {completed_count}/{total}シーン生成完了")
    return results


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
    full_prompt = f"{scene_description},{dialogue_part} same character, same face, same clothes, cinematic lighting, vertical video 9:16, dramatic"
    enhanced_prompt = full_prompt[:2500]

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

        image_b64 = _prepare_reference_image(reference_image)

        with httpx.Client(timeout=300) as client:
            response = client.post(
                "https://api.klingai.com/v1/videos/image2video",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model_name": "kling-v3",
                    "prompt": prompt,
                    "image": image_b64,
                    "duration": "15",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on",
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
                    "model_name": "kling-v3",
                    "prompt": prompt,
                    "duration": "15",
                    "aspect_ratio": "9:16",
                    "mode": "pro",
                    "sound": "on",
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


def download_kling_video(url: str, drama_id: int, scene_number: int) -> str:
    os.makedirs(SCENES_DIR, exist_ok=True)
    output_path = os.path.join(SCENES_DIR, f"drama_{drama_id}_scene_{scene_number}.mp4")
    _download_video(url, output_path)
    return output_path


def _create_placeholder_scene(drama_id: int, scene_number: int, duration: float = 15, reference_image: str = None) -> str:
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
