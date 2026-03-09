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
    get_admin_user, create_admin_user, get_all_dramas,
    get_drama_by_id, get_dramas_with_analytics, update_drama,
    get_ai_logs, get_setting, set_setting,
    get_active_series, get_all_series
)
from app.services.pipeline import run_full_pipeline, generate_theme_only
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.youtube.youtube_service import (
    get_oauth_flow, save_credentials, is_youtube_connected
)
from app.services.tiktok.tiktok_service import is_tiktok_connected

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

pipeline_lock = threading.Lock()

pipeline_status = {
    "running": False,
    "last_result": None,
    "current_step": 0,
    "total_steps": 10,
    "step_label": "",
    "logs": [],
    "started_at": None,
}

PIPELINE_STEPS = {
    0: "初期化",
    1: "テーマ生成",
    2: "脚本生成",
    3: "画像生成(SD)",
    4: "シーン分割",
    5: "動画シーン生成(Kling)",
    6: "音声生成(ElevenLabs)",
    7: "動画編集(FFmpeg)",
    8: "投稿(YouTube/TikTok)",
    9: "完了",
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

    dramas = get_all_dramas()
    total_views = sum(d.get("views", 0) for d in dramas)
    total_likes = sum(d.get("likes", 0) for d in dramas)
    published_count = sum(1 for d in dramas if d.get("youtube_id") or d.get("tiktok_id"))

    genre_counts = {}
    for d in dramas:
        g = d.get("genre", "不明")
        genre_counts[g] = genre_counts.get(g, 0) + 1

    active_series = get_active_series()
    all_series = get_all_series()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "dramas": dramas[:10],
        "all_dramas": dramas,
        "total_dramas": len(dramas),
        "published_count": published_count,
        "total_views": total_views,
        "total_likes": total_likes,
        "genre_counts": genre_counts,
        "pipeline_running": pipeline_status["running"],
        "last_result": pipeline_status.get("last_result"),
        "youtube_connected": is_youtube_connected(),
        "has_anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "has_kling": bool(os.environ.get("KLING_API_KEY")),
        "has_stability": bool(os.environ.get("STABILITY_API_KEY")),
        "has_tiktok": is_tiktok_connected(),
        "ai_logs": get_ai_logs(limit=20),
        "active_series": active_series,
        "all_series": all_series,
        "total_series": len(all_series),
    })


@router.get("/dramas", response_class=HTMLResponse)
async def dramas_list(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    dramas = get_all_dramas()
    return templates.TemplateResponse("dramas.html", {
        "request": request,
        "user": user,
        "dramas": dramas,
    })


@router.get("/dramas/{drama_id}", response_class=HTMLResponse)
async def drama_detail(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    drama = get_drama_by_id(drama_id)
    if not drama:
        raise HTTPException(status_code=404, detail="Drama not found")

    ai_logs = get_ai_logs(drama_id=drama_id)
    return templates.TemplateResponse("drama_detail.html", {
        "request": request,
        "user": user,
        "drama": drama,
        "ai_logs": ai_logs,
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

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "youtube_connected": is_youtube_connected(),
        "has_anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "has_kling": bool(os.environ.get("KLING_API_KEY")),
        "has_stability": bool(os.environ.get("STABILITY_API_KEY")),
        "has_tiktok": is_tiktok_connected(),
        "yt_has_client_id": has_client_id,
        "yt_has_client_secret": has_client_secret,
        "yt_has_refresh_token": has_refresh_token,
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
    custom_genre = body.get("genre", "").strip() or None

    import datetime

    def run_pipeline():
        if not pipeline_lock.acquire(blocking=False):
            logger.warning("Pipeline already running, skipping duplicate request")
            return
        try:
            pipeline_status["running"] = True
            pipeline_status["last_result"] = None
            pipeline_status["current_step"] = 0
            pipeline_status["step_label"] = ""
            pipeline_status["logs"] = []
            pipeline_status["started_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            try:
                result = run_full_pipeline(
                    progress_callback=pipeline_progress_callback,
                    custom_theme=custom_theme,
                    custom_genre=custom_genre
                )
                pipeline_status["last_result"] = result
                pipeline_log("パイプライン完了", step=9)
            except Exception as e:
                pipeline_status["last_result"] = {"success": False, "error": str(e)}
                pipeline_log(f"エラー: {str(e)}", step=pipeline_status["current_step"])
        finally:
            pipeline_status["running"] = False
            pipeline_lock.release()

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()

    return JSONResponse({"message": "パイプラインを開始しました", "status": "running"})


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


@router.post("/api/analyze")
async def api_analyze(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    dramas = get_all_dramas()
    dramas_data = [
        {
            "title": d.get("title", ""),
            "genre": d.get("genre", ""),
            "views": d.get("views", 0),
            "likes": d.get("likes", 0),
        }
        for d in dramas
    ]

    try:
        result = analyze_and_improve(dramas_data)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return JSONResponse({
            "analysis": f"分析エラー: {str(e)}",
            "best_genre": "N/A",
            "improvement_suggestions": "APIキーを確認してください",
            "next_theme_recommendation": "N/A"
        })


@router.post("/api/generate-theme")
async def api_generate_theme(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    genre = body.get("genre", "").strip() or None

    theme = generate_theme_only(genre=genre)
    return JSONResponse(theme)


@router.get("/api/dramas")
async def api_dramas(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    dramas = get_all_dramas()
    for d in dramas:
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
    return JSONResponse(dramas)


@router.get("/api/usage")
async def api_usage(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    import requests as req
    import datetime

    result = {"elevenlabs": None, "claude": None, "kling": None}

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
            }
    except Exception as e:
        logger.warning(f"Failed to fetch Claude usage: {e}")

    result["kling"] = {"configured": bool(os.environ.get("KLING_API_KEY"))}

    return JSONResponse(result)
