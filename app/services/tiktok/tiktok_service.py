import os
import logging
import httpx

logger = logging.getLogger(__name__)


def is_tiktok_connected() -> bool:
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))


def upload_to_tiktok(video_path: str, title: str, description: str, tags: list = None) -> str:
    access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    if not access_token:
        logger.warning("TIKTOK_ACCESS_TOKEN not set, skipping TikTok upload")
        return None

    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return None

    hashtags = ""
    if tags:
        hashtags = " ".join([f"#{t}" for t in tags])

    full_description = f"{title}\n\n{description}\n\n{hashtags}"

    try:
        with httpx.Client(timeout=300) as client:
            init_response = client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "post_info": {
                        "title": title[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": os.path.getsize(video_path),
                    }
                }
            )

            if init_response.status_code != 200:
                logger.error(f"TikTok init failed: {init_response.status_code} - {init_response.text[:300]}")
                return None

            init_data = init_response.json().get("data", {})
            upload_url = init_data.get("upload_url")
            publish_id = init_data.get("publish_id")

            if not upload_url:
                logger.error("TikTok upload URL not received")
                return None

            with open(video_path, "rb") as f:
                video_data = f.read()

            upload_response = client.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{len(video_data) - 1}/{len(video_data)}"
                },
                content=video_data
            )

            if upload_response.status_code in [200, 201]:
                logger.info(f"TikTok upload complete: publish_id={publish_id}")
                return publish_id
            else:
                logger.error(f"TikTok upload failed: {upload_response.status_code}")
                return None

    except Exception as e:
        logger.error(f"TikTok upload error: {e}")
        return None


def get_tiktok_analytics(publish_id: str):
    access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    if not access_token:
        return {"views": 0, "likes": 0}

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://open.tiktokapis.com/v2/video/query/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "filters": {
                        "video_ids": [publish_id]
                    },
                    "fields": ["like_count", "view_count", "comment_count", "share_count"]
                }
            )

            if response.status_code == 200:
                videos = response.json().get("data", {}).get("videos", [])
                if videos:
                    v = videos[0]
                    return {
                        "views": v.get("view_count", 0),
                        "likes": v.get("like_count", 0),
                        "comments": v.get("comment_count", 0),
                        "shares": v.get("share_count", 0),
                    }
    except Exception as e:
        logger.error(f"TikTok analytics error: {e}")

    return {"views": 0, "likes": 0}
