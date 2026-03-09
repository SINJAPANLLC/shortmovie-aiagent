import os
import time
import logging
import httpx

logger = logging.getLogger(__name__)

LUMA_API_BASE = "https://api.lumalabs.ai/dream-machine/v1"
POLL_INTERVAL = 5
MAX_POLL_TIME = 300


def _get_api_key():
    return os.environ.get("LUMA_API_KEY", "")


def _headers():
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _poll_generation(generation_id: str, timeout: int = MAX_POLL_TIME) -> dict:
    url = f"{LUMA_API_BASE}/generations/{generation_id}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=_headers())
                if resp.status_code != 200:
                    logger.warning(f"Luma poll error: {resp.status_code}")
                    time.sleep(POLL_INTERVAL)
                    continue
                data = resp.json()
                state = data.get("state", "")
                if state == "completed":
                    return data
                elif state == "failed":
                    logger.error(f"Luma generation failed: {data.get('failure_reason', 'unknown')}")
                    return None
                logger.debug(f"Luma generation {generation_id}: {state}")
        except Exception as e:
            logger.warning(f"Luma poll exception: {e}")
        time.sleep(POLL_INTERVAL)
    logger.error(f"Luma generation {generation_id} timed out after {timeout}s")
    return None


def _download_asset(url: str, output_path: str) -> bool:
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Luma asset downloaded: {output_path} ({len(resp.content)} bytes)")
                return True
    except Exception as e:
        logger.warning(f"Luma download error: {e}")
    return False


def generate_image_luma(prompt: str, output_path: str, aspect_ratio: str = "9:16") -> bool:
    api_key = _get_api_key()
    if not api_key:
        return False

    try:
        body = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "model": "photon-1",
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{LUMA_API_BASE}/generations/image",
                headers=_headers(),
                json=body,
            )
            if resp.status_code not in (200, 201):
                logger.warning(f"Luma image create error: {resp.status_code} - {resp.text[:300]}")
                return False
            data = resp.json()
            gen_id = data.get("id")
            if not gen_id:
                logger.warning(f"Luma image: no generation ID returned")
                return False

        logger.info(f"Luma image generation started: {gen_id}")
        result = _poll_generation(gen_id, timeout=120)
        if not result:
            return False

        assets = result.get("assets", {})
        image_url = assets.get("image")
        if not image_url:
            logger.warning(f"Luma image completed but no image URL in assets: {assets}")
            return False

        return _download_asset(image_url, output_path)

    except Exception as e:
        logger.error(f"Luma image generation error: {e}")
        return False


def generate_video_luma(prompt: str, output_path: str, aspect_ratio: str = "9:16",
                        image_url: str = None) -> bool:
    api_key = _get_api_key()
    if not api_key:
        return False

    try:
        body = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "model": "ray-2",
        }

        if image_url:
            body["keyframes"] = {
                "frame0": {
                    "type": "image",
                    "url": image_url,
                }
            }

        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{LUMA_API_BASE}/generations",
                headers=_headers(),
                json=body,
            )
            if resp.status_code not in (200, 201):
                logger.warning(f"Luma video create error: {resp.status_code} - {resp.text[:300]}")
                return False
            data = resp.json()
            gen_id = data.get("id")
            if not gen_id:
                logger.warning(f"Luma video: no generation ID")
                return False

        logger.info(f"Luma video generation started: {gen_id}")
        result = _poll_generation(gen_id, timeout=MAX_POLL_TIME)
        if not result:
            return False

        assets = result.get("assets", {})
        video_url = assets.get("video")
        if not video_url:
            logger.warning(f"Luma video completed but no video URL in assets: {assets}")
            return False

        return _download_asset(video_url, output_path)

    except Exception as e:
        logger.error(f"Luma video generation error: {e}")
        return False


def is_luma_available() -> bool:
    return bool(_get_api_key())
