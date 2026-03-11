import os
import json
import time
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

UPLOAD_MAX_RETRIES = 3
UPLOAD_RETRY_DELAY = 30

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CREDENTIALS_FILE = "youtube_credentials.json"


def _get_client_id():
    return os.environ.get("YOUTUBE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")


def _get_client_secret():
    return os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", "")


def _get_channel_id():
    return os.environ.get("YOUTUBE_CHANNEL_ID", "")


def _get_redirect_uri(request=None):
    replit_domains = os.environ.get("REPLIT_DOMAINS", "")
    if replit_domains:
        domain = replit_domains.split(",")[0].strip()
        return f"https://{domain}/auth/callback"
    replit_dev = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if replit_dev:
        return f"https://{replit_dev}/auth/callback"
    if request:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        scheme = request.headers.get("x-forwarded-proto", "https")
        return f"{scheme}://{host}/auth/callback"
    return "https://localhost:5000/auth/callback"


def get_oauth_flow(request=None):
    client_id = _get_client_id()
    client_secret = _get_client_secret()
    if not client_id or not client_secret:
        raise RuntimeError("YouTube OAuth Client ID / Client Secret が設定されていません")

    redirect_uri = _get_redirect_uri(request)

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    return flow


def save_credentials(creds_data: dict):
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds_data, f)
    logger.info("YouTube credentials saved")


def load_credentials() -> dict:
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)

    refresh_token = os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN", "")
    client_id = _get_client_id()
    client_secret = _get_client_secret()
    if refresh_token and client_id and client_secret:
        return {
            "token": None,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": client_id,
            "client_secret": client_secret,
        }

    return None


def is_youtube_connected() -> bool:
    creds = load_credentials()
    return creds is not None and bool(creds.get("refresh_token"))


def get_youtube_service():
    creds_data = load_credentials()
    if not creds_data:
        raise RuntimeError("YouTube is not connected. Please connect via Settings.")

    credentials = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id", _get_client_id()),
        client_secret=creds_data.get("client_secret", _get_client_secret()),
        scopes=SCOPES,
    )

    if credentials.expired or not credentials.token:
        if credentials.refresh_token:
            from google.auth.transport.requests import Request as GoogleRequest
            import requests as _req
            session = _req.Session()
            session.timeout = 30
            credentials.refresh(GoogleRequest(session=session))
            save_credentials({
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            })

    return build("youtube", "v3", credentials=credentials)


def upload_video(video_path: str, title: str, description: str, tags: list, thumbnail_path: str = None, privacy_status: str = "public"):
    youtube = get_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": privacy_status or "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    channel_id = _get_channel_id()
    if channel_id:
        body["snippet"]["channelId"] = channel_id

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 10
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retry_count = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504] and retry_count < UPLOAD_MAX_RETRIES:
                retry_count += 1
                delay = UPLOAD_RETRY_DELAY * retry_count
                logger.warning(f"YouTube upload error {e.resp.status}, retrying in {delay}s ({retry_count}/{UPLOAD_MAX_RETRIES})")
                time.sleep(delay)
            else:
                raise
        except (ConnectionError, TimeoutError, OSError) as e:
            if retry_count < UPLOAD_MAX_RETRIES:
                retry_count += 1
                delay = UPLOAD_RETRY_DELAY * retry_count
                logger.warning(f"YouTube upload network error, retrying in {delay}s ({retry_count}/{UPLOAD_MAX_RETRIES}): {e}")
                time.sleep(delay)
            else:
                raise

    video_id = response["id"]
    logger.info(f"Video uploaded: {video_id}")

    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/png" if thumbnail_path.endswith(".png") else "image/jpeg")
            ).execute()
            logger.info(f"Thumbnail set for video: {video_id}")
        except Exception as e:
            logger.error(f"Thumbnail upload failed: {e}")

    return video_id


def get_video_analytics(youtube_video_id: str):
    try:
        youtube = get_youtube_service()

        response = youtube.videos().list(
            part="statistics",
            id=youtube_video_id
        ).execute()

        if response["items"]:
            stats = response["items"][0]["statistics"]
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
    except Exception as e:
        logger.error(f"Analytics fetch failed: {e}")

    return {"views": 0, "likes": 0, "comments": 0}
