import os
import logging
import threading
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.api.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_auth
)
from app.db.database import (
    get_admin_user, create_admin_user, get_all_videos,
    get_video_by_id, get_videos_with_analytics, update_video,
    get_ai_logs, get_setting, set_setting
)
from app.services.pipeline import (
    run_full_pipeline, run_test_pipeline, generate_theme_only, generate_story_only
)
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.youtube.youtube_service import (
    get_oauth_flow, save_credentials, is_youtube_connected
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

pipeline_status = {
    "running": False,
    "last_result": None,
    "current_step": 0,
    "total_steps": 7,
    "step_label": "",
    "logs": [],
    "started_at": None,
}

PIPELINE_STEPS = {
    0: "YouTube分析収集",
    1: "AI改善分析",
    2: "テーマ生成",
    3: "ストーリー生成（3部構成）",
    4: "音声生成",
    5: "動画生成",
    6: "YouTube投稿",
}


def pipeline_log(message, step=None):
    import datetime
    entry = {
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "message": message,
        "step": step,
    }
    pipeline_status["logs"].append(entry)
    if len(pipeline_status["logs"]) > 200:
        pipeline_status["logs"] = pipeline_status["logs"][-200:]


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = get_admin_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "ユーザー名またはパスワードが正しくありません"
        })

    token = create_access_token({"sub": username})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("access_token", token, httponly=True, max_age=86400, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    videos = get_all_videos()
    total_views = sum(v.get("views", 0) for v in videos)
    published_count = sum(1 for v in videos if v.get("youtube_id"))
    avg_ctr = 0
    avg_watch = 0
    published_vids = [v for v in videos if v.get("ctr", 0) > 0]
    if published_vids:
        avg_ctr = sum(float(v.get("ctr", 0)) for v in published_vids) / len(published_vids)
        avg_watch = sum(float(v.get("watch_time", 0)) for v in published_vids) / len(published_vids)

    analytics_videos = get_videos_with_analytics()
    improvement = None
    if analytics_videos:
        improvement = analyze_and_improve(analytics_videos)

    has_client_id = bool(os.environ.get("YOUTUBE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID"))
    has_client_secret = bool(os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET"))
    has_refresh_token = bool(os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN"))
    has_channel_id = bool(os.environ.get("YOUTUBE_CHANNEL_ID"))
    has_api_key = bool(os.environ.get("YOUTUBE_API_KEY"))

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "videos": videos[:10],
        "all_videos": videos,
        "total_videos": len(videos),
        "published_count": published_count,
        "total_views": total_views,
        "avg_ctr": round(avg_ctr, 2),
        "avg_watch_time": round(avg_watch, 1),
        "pipeline_running": pipeline_status["running"],
        "last_result": pipeline_status.get("last_result"),
        "youtube_connected": is_youtube_connected(),
        "analytics_videos": analytics_videos,
        "improvement": improvement,
        "has_anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "yt_has_client_id": has_client_id,
        "yt_has_client_secret": has_client_secret,
        "yt_has_refresh_token": has_refresh_token,
        "yt_has_channel_id": has_channel_id,
        "yt_has_api_key": has_api_key,
        "ai_logs": get_ai_logs(limit=20),
        "video_minutes": int(get_setting("video_minutes", "10")),
    })


@router.get("/videos", response_class=HTMLResponse)
async def videos_list(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    videos = get_all_videos()
    return templates.TemplateResponse("videos.html", {
        "request": request,
        "user": user,
        "videos": videos,
    })


@router.get("/videos/{video_id}", response_class=HTMLResponse)
async def video_detail(request: Request, video_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return templates.TemplateResponse("video_detail.html", {
        "request": request,
        "user": user,
        "video": video,
    })


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    videos = get_videos_with_analytics()
    improvement = None
    if videos:
        improvement = analyze_and_improve(videos)

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "user": user,
        "videos": videos,
        "improvement": improvement,
    })


@router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse("generate.html", {
        "request": request,
        "user": user,
        "pipeline_running": pipeline_status["running"],
        "last_result": pipeline_status.get("last_result"),
        "youtube_connected": is_youtube_connected(),
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    has_client_id = bool(os.environ.get("YOUTUBE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID"))
    has_client_secret = bool(os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET"))
    has_refresh_token = bool(os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN"))
    has_channel_id = bool(os.environ.get("YOUTUBE_CHANNEL_ID"))
    has_api_key = bool(os.environ.get("YOUTUBE_API_KEY"))

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "youtube_connected": is_youtube_connected(),
        "has_anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "yt_has_client_id": has_client_id,
        "yt_has_client_secret": has_client_secret,
        "yt_has_refresh_token": has_refresh_token,
        "yt_has_channel_id": has_channel_id,
        "yt_has_api_key": has_api_key,
    })


@router.get("/auth/youtube")
async def youtube_auth_start(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    try:
        flow = get_oauth_flow(request)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return RedirectResponse(url=authorization_url)
    except Exception as e:
        logger.error(f"YouTube auth start failed: {e}")
        return RedirectResponse(url="/settings?error=youtube_auth_failed", status_code=303)


@router.get("/auth/callback")
async def youtube_auth_callback(request: Request, code: str = None, error: str = None):
    if error:
        logger.error(f"YouTube auth error: {error}")
        return RedirectResponse(url="/settings?error=youtube_denied", status_code=303)

    if not code:
        return RedirectResponse(url="/settings?error=no_code", status_code=303)

    try:
        flow = get_oauth_flow(request)
        flow.fetch_token(code=code)
        credentials = flow.credentials

        save_credentials({
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        })

        logger.info("YouTube connected successfully")
        return RedirectResponse(url="/settings?success=youtube_connected", status_code=303)
    except Exception as e:
        logger.error(f"YouTube auth callback failed: {e}")
        return RedirectResponse(url="/settings?error=youtube_callback_failed", status_code=303)


@router.post("/api/generate")
async def api_generate(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if pipeline_status["running"]:
        return JSONResponse({"error": "パイプラインが実行中です"}, status_code=409)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    custom_theme = body.get("theme", "").strip() or None

    import datetime

    def run_pipeline():
        pipeline_status["running"] = True
        pipeline_status["last_result"] = None
        pipeline_status["current_step"] = 0
        pipeline_status["step_label"] = ""
        pipeline_status["logs"] = []
        pipeline_status["started_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        try:
            result = run_full_pipeline(progress_callback=pipeline_progress_callback, custom_theme=custom_theme)
            pipeline_status["last_result"] = result
            pipeline_log("パイプライン完了", step=7)
        except Exception as e:
            pipeline_status["last_result"] = {"success": False, "error": str(e)}
            pipeline_log(f"エラー: {str(e)}", step=pipeline_status["current_step"])
        finally:
            pipeline_status["running"] = False

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()

    return JSONResponse({"message": "パイプラインを開始しました", "status": "running"})


@router.post("/api/generate-test")
async def api_generate_test(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if pipeline_status["running"]:
        return JSONResponse({"error": "パイプラインが実行中です"}, status_code=409)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    custom_theme = body.get("theme", "").strip() or None

    import datetime

    def run_pipeline():
        pipeline_status["running"] = True
        pipeline_status["last_result"] = None
        pipeline_status["current_step"] = 0
        pipeline_status["step_label"] = ""
        pipeline_status["logs"] = []
        pipeline_status["started_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        try:
            result = run_test_pipeline(progress_callback=pipeline_progress_callback, custom_theme=custom_theme)
            pipeline_status["last_result"] = result
            pipeline_log("テストパイプライン完了", step=7)
        except Exception as e:
            pipeline_status["last_result"] = {"success": False, "error": str(e)}
            pipeline_log(f"エラー: {str(e)}", step=pipeline_status["current_step"])
        finally:
            pipeline_status["running"] = False

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()

    return JSONResponse({"message": "テストパイプラインを開始しました", "status": "running"})


def pipeline_progress_callback(step: int, message: str):
    pipeline_status["current_step"] = step
    pipeline_status["step_label"] = PIPELINE_STEPS.get(step, "")
    pipeline_log(message, step=step)


@router.get("/api/pipeline-status")
async def api_pipeline_status(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse({
        "running": pipeline_status["running"],
        "last_result": pipeline_status.get("last_result"),
        "current_step": pipeline_status["current_step"],
        "total_steps": pipeline_status["total_steps"],
        "step_label": pipeline_status["step_label"],
        "logs": pipeline_status["logs"][-50:],
        "started_at": pipeline_status.get("started_at"),
    })


@router.post("/api/generate-theme")
async def api_generate_theme(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    theme = generate_theme_only()
    return JSONResponse(theme)


@router.get("/api/videos")
async def api_videos(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    videos = get_all_videos()
    for v in videos:
        if v.get("created_at"):
            v["created_at"] = v["created_at"].isoformat()
    return JSONResponse(videos)


@router.get("/api/usage")
async def api_usage(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    import requests as req
    import datetime

    result = {"elevenlabs": None, "claude": None}

    try:
        el_key = os.environ.get("ELEVENLABS_API_KEY", "")
        if el_key:
            resp = req.get("https://api.elevenlabs.io/v1/user/subscription",
                           headers={"xi-api-key": el_key}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                reset_ts = data.get("next_character_count_reset_unix")
                reset_date = ""
                if reset_ts:
                    reset_date = datetime.datetime.fromtimestamp(reset_ts).strftime("%Y/%m/%d")
                result["elevenlabs"] = {
                    "tier": data.get("tier", "unknown"),
                    "used": data.get("character_count", 0),
                    "limit": data.get("character_limit", 0),
                    "remaining": data.get("character_limit", 0) - data.get("character_count", 0),
                    "reset_date": reset_date,
                    "videos_possible": max(0, (data.get("character_limit", 0) - data.get("character_count", 0)) // (int(get_setting("video_minutes", "10")) * 350 + 100)),
                }
    except Exception as e:
        logger.warning(f"Failed to fetch ElevenLabs usage: {e}")

    try:
        claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if claude_key:
            resp = req.post("https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": claude_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "."}]
                },
                timeout=10
            )
            headers = resp.headers
            result["claude"] = {
                "requests_limit": headers.get("anthropic-ratelimit-requests-limit", "N/A"),
                "requests_remaining": headers.get("anthropic-ratelimit-requests-remaining", "N/A"),
                "input_tokens_limit": headers.get("anthropic-ratelimit-input-tokens-limit", "N/A"),
                "output_tokens_limit": headers.get("anthropic-ratelimit-output-tokens-limit", "N/A"),
                "output_tokens_remaining": headers.get("anthropic-ratelimit-output-tokens-remaining", "N/A"),
            }
    except Exception as e:
        logger.warning(f"Failed to fetch Claude usage: {e}")

    return JSONResponse(result)


@router.get("/api/settings")
async def api_get_settings(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse({
        "video_minutes": int(get_setting("video_minutes", "10")),
    })


@router.post("/api/settings")
async def api_save_settings(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = await request.json()
    video_minutes = body.get("video_minutes")
    if video_minutes is not None:
        try:
            video_minutes = max(1, min(120, int(video_minutes)))
        except (ValueError, TypeError):
            return JSONResponse({"error": "無効な値です"}, status_code=400)
        set_setting("video_minutes", str(video_minutes))

    return JSONResponse({"message": "設定を保存しました", "video_minutes": int(get_setting("video_minutes", "10"))})
