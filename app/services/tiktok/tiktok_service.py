import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

TIKTOK_CREDENTIALS_FILE = "tiktok_credentials.json"


def _get_tiktok_client_key():
    return os.environ.get("TIKTOK_CLIENT_KEY", "")


def _get_tiktok_client_secret():
    return os.environ.get("TIKTOK_CLIENT_SECRET", "")


def _get_stored_access_token():
    if os.path.exists(TIKTOK_CREDENTIALS_FILE):
        try:
            with open(TIKTOK_CREDENTIALS_FILE, "r") as f:
                data = json.load(f)
                return data.get("access_token", "")
        except Exception:
            pass
    return ""


def is_tiktok_connected() -> bool:
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN") or _get_stored_access_token())


def get_tiktok_access_token():
    token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    if token:
        return token
    return _get_stored_access_token()


def get_tiktok_oauth_url(redirect_uri: str) -> str:
    client_key = _get_tiktok_client_key()
    if not client_key:
        raise RuntimeError("TIKTOK_CLIENT_KEY が設定されていません")

    scopes = "video.upload,video.publish,video.list"
    return (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={client_key}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&state=tiktok_oauth"
    )


def exchange_tiktok_code(code: str, redirect_uri: str) -> dict:
    client_key = _get_tiktok_client_key()
    client_secret = _get_tiktok_client_secret()

    if not client_key or not client_secret:
        raise RuntimeError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET が設定されていません")

    with httpx.Client(timeout=30) as client:
        response = client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )

        if response.status_code == 200:
            data = response.json()
            access_token = data.get("access_token", "")
            if access_token:
                save_data = {
                    "access_token": access_token,
                    "refresh_token": data.get("refresh_token", ""),
                    "open_id": data.get("open_id", ""),
                    "expires_in": data.get("expires_in", 0),
                }
                with open(TIKTOK_CREDENTIALS_FILE, "w") as f:
                    json.dump(save_data, f)
                logger.info(f"TikTok OAuth success: open_id={save_data['open_id']}")
                return save_data
            else:
                raise RuntimeError(f"TikTok token exchange failed: {data}")
        else:
            raise RuntimeError(f"TikTok OAuth error: {response.status_code} - {response.text[:300]}")


def upload_to_tiktok(video_path: str, title: str, description: str, tags: list = None) -> str:
    access_token = get_tiktok_access_token()
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
    access_token = get_tiktok_access_token()
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
