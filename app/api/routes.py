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
from fastapi import UploadFile, File
from app.db.database import (
    get_admin_user, create_admin_user, get_all_dramas,
    get_drama_by_id, get_dramas_with_analytics, update_drama,
    get_ai_logs, get_setting, set_setting,
    get_active_series, get_all_series,
    get_characters, get_characters_by_series, get_character_by_id,
    create_character, update_character, delete_character
)
from app.services.pipeline import run_full_pipeline, generate_theme_only, generate_script_only, continue_pipeline_from_script
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.youtube.youtube_service import (
    get_oauth_flow, save_credentials, is_youtube_connected
)
from app.services.tiktok.tiktok_rpa import (
    is_tiktok_rpa_connected, save_tiktok_cookies, clear_tiktok_cookies
)

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
        "has_kling": bool(os.environ.get("KLING_ACCESS_KEY") and os.environ.get("KLING_SECRET_KEY")) or bool(os.environ.get("KLING_API_KEY")),
        "has_stability": bool(os.environ.get("STABILITY_API_KEY")),
        "has_tiktok": is_tiktok_rpa_connected(),
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

    active_series = get_active_series()
    characters_list = []
    if active_series:
        characters_list = get_characters_by_series(active_series["id"])
    return templates.TemplateResponse("generate.html", {
        "request": request,
        "user": user,
        "pipeline_running": pipeline_status["running"],
        "last_result": pipeline_status.get("last_result"),
        "youtube_connected": is_youtube_connected(),
        "active_series": active_series,
        "characters": characters_list,
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
        "has_kling": bool(os.environ.get("KLING_ACCESS_KEY") and os.environ.get("KLING_SECRET_KEY")) or bool(os.environ.get("KLING_API_KEY")),
        "has_stability": bool(os.environ.get("STABILITY_API_KEY")),
        "has_tiktok": is_tiktok_rpa_connected(),
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
async def auth_callback(request: Request, code: str = None, error: str = None, state: str = None):
    return await _youtube_auth_callback(request, code, error)


async def _youtube_auth_callback(request: Request, code: str = None, error: str = None):
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


@router.post("/api/tiktok/cookies")
async def save_tiktok_cookies_api(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    try:
        body = await request.json()
        cookies_data = body.get("cookies", [])
        if not cookies_data:
            return JSONResponse({"error": "Cookieデータが空です"}, status_code=400)

        save_tiktok_cookies(cookies_data)
        connected = is_tiktok_rpa_connected()
        return JSONResponse({
            "success": True,
            "connected": connected,
            "message": "TikTok Cookieを保存しました" if connected else "sessionidが見つかりません。TikTokにログインした状態でCookieをエクスポートしてください。"
        })
    except Exception as e:
        logger.error(f"TikTok cookies save failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/tiktok/disconnect")
async def disconnect_tiktok(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    clear_tiktok_cookies()
    return JSONResponse({"success": True, "message": "TikTok連携を解除しました"})


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
    max_scenes = body.get("max_scenes")
    if max_scenes:
        try:
            max_scenes = int(max_scenes)
        except (ValueError, TypeError):
            max_scenes = None
    target_episode = body.get("target_episode")
    if target_episode:
        try:
            target_episode = int(target_episode)
        except (ValueError, TypeError):
            target_episode = None

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
                    custom_genre=custom_genre,
                    max_scenes=max_scenes,
                    target_episode=target_episode
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


@router.post("/api/generate-script")
async def api_generate_script(request: Request):
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
    target_episode = body.get("target_episode")
    if target_episode:
        try:
            target_episode = int(target_episode)
        except (ValueError, TypeError):
            target_episode = None

    active_series = get_active_series()
    characters_list = []
    if active_series:
        characters_list = get_characters_by_series(active_series["id"])

    import datetime

    def run_script_gen():
        if not pipeline_lock.acquire(blocking=False):
            return
        try:
            pipeline_status["running"] = True
            pipeline_status["last_result"] = None
            pipeline_status["current_step"] = 0
            pipeline_status["step_label"] = ""
            pipeline_status["logs"] = []
            pipeline_status["started_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            try:
                result = generate_script_only(
                    progress_callback=pipeline_progress_callback,
                    custom_theme=custom_theme,
                    target_episode=target_episode,
                    characters=characters_list
                )
                pipeline_status["last_result"] = result
                pipeline_log("脚本生成完了", step=2)
            except Exception as e:
                pipeline_status["last_result"] = {"success": False, "error": str(e)}
                pipeline_log(f"エラー: {str(e)}", step=pipeline_status["current_step"])
        finally:
            pipeline_status["running"] = False
            pipeline_lock.release()

    thread = threading.Thread(target=run_script_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": "脚本生成を開始しました", "status": "running"})


@router.post("/api/generate-video/{drama_id}")
async def api_generate_video(request: Request, drama_id: int):
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

    max_scenes = body.get("max_scenes")
    if max_scenes:
        try:
            max_scenes = int(max_scenes)
        except (ValueError, TypeError):
            max_scenes = None

    import datetime

    def run_video_gen():
        if not pipeline_lock.acquire(blocking=False):
            return
        try:
            pipeline_status["running"] = True
            pipeline_status["last_result"] = None
            pipeline_status["current_step"] = 3
            pipeline_status["step_label"] = ""
            pipeline_status["logs"] = []
            pipeline_status["started_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            try:
                result = continue_pipeline_from_script(
                    drama_id=drama_id,
                    progress_callback=pipeline_progress_callback,
                    max_scenes=max_scenes
                )
                pipeline_status["last_result"] = result
                pipeline_log("パイプライン完了", step=9)
            except Exception as e:
                pipeline_status["last_result"] = {"success": False, "error": str(e)}
                pipeline_log(f"エラー: {str(e)}", step=pipeline_status["current_step"])
        finally:
            pipeline_status["running"] = False
            pipeline_lock.release()

    thread = threading.Thread(target=run_video_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": "動画生成を開始しました", "status": "running"})


@router.put("/api/dramas/{drama_id}/script")
async def api_update_script(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = await request.json()
    script_data = body.get("script_data")
    if not script_data:
        return JSONResponse({"error": "脚本データが必要です"}, status_code=400)

    import json
    update_drama(drama_id, script=json.dumps(script_data, ensure_ascii=False))
    return JSONResponse({"success": True})


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


@router.get("/character-images", response_class=HTMLResponse)
async def character_images_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    import glob
    char_dir = "app/static/characters"
    images = []
    for f in sorted(glob.glob(os.path.join(char_dir, "*.png")) + glob.glob(os.path.join(char_dir, "*.jpg")) + glob.glob(os.path.join(char_dir, "*.webp"))):
        fname = os.path.basename(f)
        images.append({
            "filename": fname,
            "url": f"/static/characters/{fname}",
        })

    characters = get_characters()
    return templates.TemplateResponse("character_images.html", {
        "request": request,
        "user": user,
        "images": images,
        "characters": characters,
    })


@router.get("/characters", response_class=HTMLResponse)
async def characters_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    characters = get_characters()
    all_series = get_all_series()
    return templates.TemplateResponse("characters.html", {
        "request": request,
        "user": user,
        "characters": characters,
        "all_series": all_series,
    })


@router.post("/api/characters")
async def api_create_character(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名前は必須です"}, status_code=400)

    char_id = create_character(
        name=name,
        role=body.get("role", "主人公"),
        description=body.get("description", ""),
        voice_id=body.get("voice_id", ""),
        image_path=body.get("image_path", ""),
        series_id=int(body["series_id"]) if body.get("series_id") else None
    )
    return JSONResponse({"success": True, "id": char_id})


@router.put("/api/characters/{character_id}")
async def api_update_character(request: Request, character_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    body = await request.json()
    updates = {}
    if "name" in body:
        updates["name"] = body["name"].strip()
    if "role" in body:
        updates["role"] = body["role"]
    if "description" in body:
        updates["description"] = body["description"]
    if "voice_id" in body:
        updates["voice_id"] = body["voice_id"]
    if "image_path" in body:
        updates["image_path"] = body["image_path"]
    if "image_face" in body:
        updates["image_face"] = body["image_face"]
    if "image_bust" in body:
        updates["image_bust"] = body["image_bust"]
    if "image_fullbody" in body:
        updates["image_fullbody"] = body["image_fullbody"]
    if "series_id" in body:
        updates["series_id"] = int(body["series_id"]) if body["series_id"] else None

    if updates:
        update_character(character_id, **updates)
    return JSONResponse({"success": True})


@router.delete("/api/characters/{character_id}")
async def api_delete_character(request: Request, character_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    delete_character(character_id)
    return JSONResponse({"success": True})


@router.post("/api/characters/generate-image")
async def api_generate_character_image(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    body = await request.json()
    prompt = body.get("prompt", "").strip()
    char_name = body.get("char_name", "character").strip()
    shot_type = body.get("shot_type", "bust").strip()

    if not prompt:
        return JSONResponse({"error": "プロンプトを入力してください"}, status_code=400)

    import uuid
    import httpx

    api_key = os.environ.get("STABILITY_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "STABILITY_API_KEYが設定されていません"}, status_code=500)

    aspect_map = {"face": "1:1", "bust": "3:4", "fullbody": "9:16"}
    aspect = aspect_map.get(shot_type, "3:4")

    full_prompt = (
        f"{prompt}, photorealistic, high resolution, professional photography, "
        "natural lighting, shallow depth of field, Canon EOS R5, 85mm lens"
    )
    negative = "anime, cartoon, illustration, CGI, artificial, plastic skin, overly smooth, watermark, text, blurry, deformed"

    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(
                "https://api.stability.ai/v2beta/stable-image/generate/sd3",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "image/*"
                },
                data={
                    "prompt": full_prompt,
                    "negative_prompt": negative,
                    "aspect_ratio": aspect,
                    "output_format": "png",
                    "model": "sd3-medium"
                }
            )
        if response.status_code == 200:
            os.makedirs("app/static/characters", exist_ok=True)
            uid = uuid.uuid4().hex[:8]
            safe_name = char_name.replace(" ", "_").replace("/", "_")[:20]
            filename = f"gen_{safe_name}_{shot_type}_{uid}.png"
            filepath = f"app/static/characters/{filename}"
            with open(filepath, "wb") as f:
                f.write(response.content)
            return JSONResponse({
                "success": True,
                "filename": filename,
                "url": f"/static/characters/{filename}"
            })
        else:
            error_msg = response.text[:200]
            logger.error(f"Stability API error: {response.status_code} {error_msg}")
            return JSONResponse({"error": f"画像生成に失敗しました ({response.status_code})"}, status_code=500)
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return JSONResponse({"error": f"画像生成エラー: {str(e)}"}, status_code=500)


@router.post("/api/characters/upload-image")
async def api_upload_character_image(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    form = await request.form()
    image = form.get("image")
    if not image:
        return JSONResponse({"error": "画像が必要です"}, status_code=400)

    import uuid
    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
    MAX_SIZE = 10 * 1024 * 1024

    ext = os.path.splitext(image.filename)[1].lower() if image.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"許可されていないファイル形式です。{', '.join(ALLOWED_EXTENSIONS)} のみ"}, status_code=400)

    contents = await image.read()
    if len(contents) > MAX_SIZE:
        return JSONResponse({"error": "ファイルサイズが大きすぎます（最大10MB）"}, status_code=400)

    os.makedirs("app/static/characters", exist_ok=True)
    filename = f"char_{uuid.uuid4().hex[:8]}{ext}"
    filepath = f"app/static/characters/{filename}"

    with open(filepath, "wb") as f:
        f.write(contents)

    return JSONResponse({"success": True, "path": filepath})


production_tasks = {}


def _get_production_task_key(drama_id, task_type, scene_num=None):
    if scene_num is not None:
        return f"{drama_id}_{task_type}_{scene_num}"
    return f"{drama_id}_{task_type}"


@router.get("/production", response_class=HTMLResponse)
async def production_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    dramas = get_all_dramas()
    ready = [d for d in dramas if d.get("status") in ("script_ready", "generating", "ready", "published")]
    ready.sort(key=lambda d: d.get("id", 0), reverse=True)
    if ready:
        return RedirectResponse(url=f"/production/{ready[0]['id']}", status_code=302)
    return RedirectResponse(url="/dramas", status_code=302)


@router.get("/production/{drama_id}", response_class=HTMLResponse)
async def production_page(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    drama = get_drama_by_id(drama_id)
    if not drama:
        raise HTTPException(status_code=404, detail="Drama not found")

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    scenes = script_data.get("scenes", [])

    scene_assets = []
    for s in scenes:
        sn = s.get("scene_number", 0)
        img_path = f"app/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png"
        vid_path = f"app/static/scenes/drama_{drama_id}_scene_{sn}.mp4"
        scene_assets.append({
            "scene_number": sn,
            "has_image": os.path.exists(img_path),
            "image_url": f"/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png" if os.path.exists(img_path) else None,
            "has_video": os.path.exists(vid_path),
            "video_url": f"/static/scenes/drama_{drama_id}_scene_{sn}.mp4" if os.path.exists(vid_path) else None,
        })

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    thumb_path = f"app/static/thumbnail/drama_{drama_id}.png"
    video_path = f"app/static/videos/drama_{drama_id}.mp4"

    all_dramas = get_all_dramas()
    available_dramas = [d for d in all_dramas if d.get("status") in ("script_ready", "generating", "ready", "published")]
    available_dramas.sort(key=lambda d: d.get("id", 0), reverse=True)

    series = None
    if drama.get("series_id"):
        all_series = get_all_series()
        for s in all_series:
            if s.get("id") == drama["series_id"]:
                series = s
                break

    return templates.TemplateResponse("production.html", {
        "request": request,
        "user": user,
        "drama": drama,
        "series": series,
        "available_dramas": available_dramas,
        "script_data": script_data,
        "scenes": scenes,
        "scene_assets": scene_assets,
        "has_audio": os.path.exists(audio_path),
        "audio_url": f"/static/audio/drama_{drama_id}.mp3" if os.path.exists(audio_path) else None,
        "has_thumbnail": os.path.exists(thumb_path),
        "thumbnail_url": f"/static/thumbnail/drama_{drama_id}.png" if os.path.exists(thumb_path) else None,
        "has_final_video": os.path.exists(video_path),
        "final_video_url": f"/static/videos/drama_{drama_id}.mp4" if os.path.exists(video_path) else None,
    })


@router.get("/api/production/{drama_id}/assets")
async def api_production_assets(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    scenes = script_data.get("scenes", [])
    scene_assets = []
    for s in scenes:
        sn = s.get("scene_number", 0)
        img_path = f"app/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png"
        vid_path = f"app/static/scenes/drama_{drama_id}_scene_{sn}.mp4"
        img_size = os.path.getsize(img_path) if os.path.exists(img_path) else 0
        vid_size = os.path.getsize(vid_path) if os.path.exists(vid_path) else 0

        task_img_key = _get_production_task_key(drama_id, "image", sn)
        task_vid_key = _get_production_task_key(drama_id, "video", sn)

        scene_assets.append({
            "scene_number": sn,
            "has_image": os.path.exists(img_path) and img_size > 1000,
            "image_url": f"/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png?t={int(os.path.getmtime(img_path))}" if os.path.exists(img_path) and img_size > 1000 else None,
            "has_video": os.path.exists(vid_path) and vid_size > 5000,
            "video_url": f"/static/scenes/drama_{drama_id}_scene_{sn}.mp4?t={int(os.path.getmtime(vid_path))}" if os.path.exists(vid_path) and vid_size > 5000 else None,
            "image_generating": production_tasks.get(task_img_key, {}).get("status") == "running",
            "video_generating": production_tasks.get(task_vid_key, {}).get("status") == "running",
            "image_error": production_tasks.get(task_img_key, {}).get("error", ""),
            "video_error": production_tasks.get(task_vid_key, {}).get("error", ""),
        })

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    thumb_path = f"app/static/thumbnail/drama_{drama_id}.png"
    video_path = f"app/static/videos/drama_{drama_id}.mp4"

    task_audio_key = _get_production_task_key(drama_id, "audio")
    task_thumb_key = _get_production_task_key(drama_id, "thumbnail")
    task_assemble_key = _get_production_task_key(drama_id, "assemble")

    return JSONResponse({
        "scenes": scene_assets,
        "has_audio": os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000,
        "audio_url": f"/static/audio/drama_{drama_id}.mp3?t={int(os.path.getmtime(audio_path))}" if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000 else None,
        "audio_generating": production_tasks.get(task_audio_key, {}).get("status") == "running",
        "audio_error": production_tasks.get(task_audio_key, {}).get("error", ""),
        "has_thumbnail": os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 1000,
        "thumbnail_url": f"/static/thumbnail/drama_{drama_id}.png?t={int(os.path.getmtime(thumb_path))}" if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 1000 else None,
        "thumbnail_generating": production_tasks.get(task_thumb_key, {}).get("status") == "running",
        "thumbnail_error": production_tasks.get(task_thumb_key, {}).get("error", ""),
        "has_final_video": os.path.exists(video_path) and os.path.getsize(video_path) > 10000,
        "final_video_url": f"/static/videos/drama_{drama_id}.mp4?t={int(os.path.getmtime(video_path))}" if os.path.exists(video_path) and os.path.getsize(video_path) > 10000 else None,
        "assemble_generating": production_tasks.get(task_assemble_key, {}).get("status") == "running",
        "assemble_error": production_tasks.get(task_assemble_key, {}).get("error", ""),
        "drama_status": drama.get("status", ""),
    })


@router.post("/api/production/{drama_id}/scene-image/{scene_num}")
async def api_production_scene_image(request: Request, drama_id: int, scene_num: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    scenes = script_data.get("scenes", [])
    scene = None
    for s in scenes:
        if s.get("scene_number") == scene_num:
            scene = s
            break

    if not scene:
        return JSONResponse({"error": f"Scene {scene_num} not found"}, status_code=404)

    task_key = _get_production_task_key(drama_id, "image", scene_num)
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_image_gen():
        try:
            from app.services.video.scene_generator import _generate_scene_specific_image
            result = _generate_scene_specific_image(
                scene.get("description", ""),
                drama_id, scene_num
            )
            if result:
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "画像生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_image_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": f"Scene {scene_num} image generation started", "status": "running"})


@router.post("/api/production/{drama_id}/scene-video/{scene_num}")
async def api_production_scene_video(request: Request, drama_id: int, scene_num: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    scenes = script_data.get("scenes", [])
    scene = None
    for s in scenes:
        if s.get("scene_number") == scene_num:
            scene = s
            break

    if not scene:
        return JSONResponse({"error": f"Scene {scene_num} not found"}, status_code=404)

    task_key = _get_production_task_key(drama_id, "video", scene_num)
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_video_gen():
        try:
            from app.services.video.scene_generator import generate_scene_video
            img_path = f"app/static/scene_images/drama_{drama_id}_scene_{scene_num}_ai.png"
            ref_image = img_path if os.path.exists(img_path) else None

            result = generate_scene_video(
                scene_description=scene.get("description", ""),
                scene_number=scene_num,
                drama_id=drama_id,
                reference_image=ref_image,
                emotion=scene.get("emotion", ""),
                duration=float(scene.get("duration", 6))
            )
            if result and os.path.exists(result):
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "動画生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_video_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": f"Scene {scene_num} video generation started", "status": "running"})


@router.post("/api/production/{drama_id}/audio")
async def api_production_audio(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    task_key = _get_production_task_key(drama_id, "audio")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_audio_gen():
        try:
            from app.services.video.audio_generator import generate_voice
            narration = script_data.get("narration", "")
            scenes = script_data.get("scenes", [])
            result = generate_voice(narration, drama_id, scenes=scenes)
            if result and os.path.exists(result):
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "音声生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_audio_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": "Audio generation started", "status": "running"})


@router.post("/api/production/{drama_id}/thumbnail")
async def api_production_thumbnail(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    task_key = _get_production_task_key(drama_id, "thumbnail")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_thumb_gen():
        try:
            from app.services.video.image_generator import generate_thumbnail
            result = generate_thumbnail(
                title=drama.get("title", ""),
                genre=drama.get("genre", "CEOドラマ"),
                drama_id=drama_id,
                episode_number=drama.get("series_episode") or drama.get("episode_number")
            )
            if result and os.path.exists(result):
                update_drama(drama_id, thumbnail_url=result)
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "サムネイル生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_thumb_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": "Thumbnail generation started", "status": "running"})


@router.post("/api/production/{drama_id}/assemble")
async def api_production_assemble(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import json
    script_data = {}
    if drama.get("script"):
        try:
            script_data = json.loads(drama["script"]) if isinstance(drama["script"], str) else drama["script"]
        except (json.JSONDecodeError, TypeError):
            pass

    scenes = script_data.get("scenes", [])
    scene_videos = []
    missing = []
    for s in scenes:
        sn = s.get("scene_number", 0)
        vid_path = f"app/static/scenes/drama_{drama_id}_scene_{sn}.mp4"
        if os.path.exists(vid_path) and os.path.getsize(vid_path) > 5000:
            scene_videos.append(vid_path)
        else:
            missing.append(sn)

    if missing:
        return JSONResponse({"error": f"シーン {', '.join(map(str, missing))} の動画がありません"}, status_code=400)

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    if not os.path.exists(audio_path):
        return JSONResponse({"error": "音声がまだ生成されていません"}, status_code=400)

    task_key = _get_production_task_key(drama_id, "assemble")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already assembling"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_assemble():
        try:
            from app.services.video.subtitle_generator import generate_subtitle
            from app.services.video.video_generator import edit_video

            subtitle_path = generate_subtitle(scenes, drama_id)
            final_video = edit_video(scene_videos, audio_path, drama_id, subtitle_path=subtitle_path)
            update_drama(drama_id, video_url=final_video, status="ready")
            production_tasks[task_key] = {"status": "done", "error": ""}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_assemble, daemon=True)
    thread.start()

    return JSONResponse({"message": "Video assembly started", "status": "running"})


@router.post("/api/production/{drama_id}/save-script")
async def api_production_save_script(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "ドラマが見つかりません"}, status_code=404)

    body = await request.json()
    script_data = body.get("script_data")
    if not script_data:
        return JSONResponse({"error": "脚本データが必要です"}, status_code=400)

    import json
    update_drama(drama_id, script=json.dumps(script_data, ensure_ascii=False))
    return JSONResponse({"success": True})


@router.post("/api/production/{drama_id}/save-theme")
async def api_production_save_theme(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "ドラマが見つかりません"}, status_code=404)

    body = await request.json()
    theme = body.get("theme", "").strip()
    update_drama(drama_id, theme=theme)
    return JSONResponse({"success": True})


@router.post("/api/production/{drama_id}/generate-script")
async def api_production_generate_script(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "ドラマが見つかりません"}, status_code=404)

    body = await request.json()
    theme = body.get("theme", "").strip()
    if not theme:
        return JSONResponse({"error": "テーマが必要です"}, status_code=400)

    task_key = _get_production_task_key(drama_id, "script")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    import threading

    def run_script_gen():
        try:
            import json as json_mod
            update_drama(drama_id, theme=theme)

            from app.services.ai.story_generator import generate_script
            series = get_active_series()
            characters = get_characters()

            characters_ctx = ""
            if characters:
                lines = []
                for c in characters:
                    lines.append(f"- {c.get('name','')}: {c.get('role','')} / {c.get('personality','')}")
                characters_ctx = "\n".join(lines)

            script_data = generate_script(
                theme=theme,
                genre=drama.get("genre", "CEOドラマ"),
                drama_id=drama_id,
                series_info=series,
                characters_context=characters_ctx
            )

            if script_data:
                script_json = json_mod.dumps(script_data, ensure_ascii=False)
                scene_count = len(script_data.get("scenes", []))
                update_drama(drama_id, script=script_json, scene_count=scene_count, status="script_ready")
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "脚本生成に失敗しました"}
        except Exception as e:
            logger.error(f"Script generation error: {e}")
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_script_gen, daemon=True)
    thread.start()

    thread.join(timeout=120)

    task_status = production_tasks.get(task_key, {})
    if task_status.get("status") == "done":
        return JSONResponse({"success": True, "message": "脚本を生成しました"})
    elif task_status.get("status") == "error":
        return JSONResponse({"error": task_status.get("error", "脚本生成に失敗しました")}, status_code=500)
    else:
        return JSONResponse({"success": True, "message": "脚本生成中です。しばらくお待ちください。"})
