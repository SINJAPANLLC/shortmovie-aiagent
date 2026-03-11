import os
import logging
import threading
import asyncio
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
    get_active_series, get_all_series, update_series,
    get_series_by_id, get_dramas_by_series, get_next_series_number,
    create_series, create_drama, get_next_episode_number,
    get_characters, get_characters_by_series, get_character_by_id,
    create_character, update_character, delete_character,
    delete_drama,
    delete_series
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


@router.get("/series", response_class=HTMLResponse)
async def series_list(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    import json as _json
    all_series = get_all_series()
    series_episodes = {}
    episode_scripts = {}
    for s in all_series:
        eps = get_dramas_by_series(s["id"])
        series_episodes[s["id"]] = eps
        for ep in eps:
            if ep.get("script"):
                try:
                    sd = _json.loads(ep["script"]) if isinstance(ep["script"], str) else ep["script"]
                    episode_scripts[ep["id"]] = sd
                except Exception:
                    pass
    return templates.TemplateResponse("series.html", {
        "request": request,
        "user": user,
        "all_series": all_series,
        "series_episodes": series_episodes,
        "episode_scripts": episode_scripts,
    })


@router.post("/api/series")
async def api_create_series(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return JSONResponse({"success": False, "error": "シリーズ名を入力してください"})

        description = body.get("description", "").strip()
        synopsis = body.get("synopsis", "").strip()
        total_episodes = int(body.get("total_episodes", 30))
        series_number = get_next_series_number()

        current_active = get_active_series()
        if current_active:
            update_series(current_active["id"], status="completed")

        series_id = create_series(
            series_number=series_number,
            name=name,
            description=description,
            synopsis=synopsis,
            total_episodes=total_episodes,
        )
        return JSONResponse({"success": True, "series_id": series_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/api/series/{series_id}/add-episode")
async def api_series_add_episode(request: Request, series_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        series = get_series_by_id(series_id)
        if not series:
            return JSONResponse({"success": False, "error": "シリーズが見つかりません"})

        current_ep = series.get("current_episode", 0)
        total_ep = series.get("total_episodes", 30)
        if current_ep >= total_ep:
            return JSONResponse({"success": False, "error": f"このシリーズは全{total_ep}話で完結しています"})

        next_ep = current_ep + 1
        global_ep = get_next_episode_number("CEOドラマ")
        title = f"CEOの扉 | {series['name']} 第{next_ep}話"

        drama_id = create_drama(
            title=title,
            genre="CEOドラマ",
            theme="",
            script="",
            status="draft",
            episode_number=global_ep,
            series_id=series_id,
            series_episode=next_ep,
        )
        update_series(series_id, current_episode=next_ep)
        return JSONResponse({"success": True, "drama_id": drama_id, "episode": next_ep, "title": title})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/api/series/{series_id}/activate")
async def api_activate_series(request: Request, series_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        series = get_series_by_id(series_id)
        if not series:
            return JSONResponse({"success": False, "error": "シリーズが見つかりません"})

        all_s = get_all_series()
        for s in all_s:
            if s["status"] == "active":
                update_series(s["id"], status="completed")

        update_series(series_id, status="active")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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
        "has_luma": bool(os.environ.get("LUMA_API_KEY")),
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


@router.post("/api/dramas/add-episode")
async def api_add_episode(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        series = get_active_series()
        if not series:
            return JSONResponse({"success": False, "error": "アクティブなシリーズがありません"})

        series_id = series["id"]
        series_name = series["name"]
        current_ep = series.get("current_episode", 0)
        next_ep = current_ep + 1
        global_ep = get_next_episode_number("CEOドラマ")
        title = f"CEOの扉 | {series_name} 第{next_ep}話"

        drama_id = create_drama(
            title=title,
            genre="CEOドラマ",
            theme="",
            script="",
            status="draft",
            episode_number=global_ep,
            series_id=series_id,
            series_episode=next_ep,
        )
        update_series(series_id, current_episode=next_ep)

        return JSONResponse({"success": True, "drama_id": drama_id, "episode": next_ep, "title": title})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/api/dramas/{drama_id}")
async def api_delete_drama(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    import glob as glob_mod
    patterns = [
        f"app/static/scene_images/drama_{drama_id}_*",
        f"app/static/scenes/drama_{drama_id}_*",
        f"app/static/audio/drama_{drama_id}*",
        f"app/static/thumbnail/drama_{drama_id}*",
        f"app/static/videos/drama_{drama_id}*",
        f"app/static/subtitle/drama_{drama_id}*",
    ]
    for pattern in patterns:
        for f in glob_mod.glob(pattern):
            try:
                os.remove(f)
            except Exception:
                pass

    delete_drama(drama_id)
    return JSONResponse({"success": True, "message": "ドラマを削除しました"})


@router.delete("/api/series/{series_id}")
async def api_delete_series(request: Request, series_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    series = get_series_by_id(series_id)
    if not series:
        return JSONResponse({"error": "Not found"}, status_code=404)

    dramas = get_dramas_by_series(series_id)
    for drama in dramas:
        import glob as glob_mod
        drama_id = drama["id"]
        patterns = [
            f"app/static/scene_images/drama_{drama_id}_*",
            f"app/static/scenes/drama_{drama_id}_*",
            f"app/static/audio/drama_{drama_id}*",
            f"app/static/thumbnail/drama_{drama_id}*",
            f"app/static/videos/drama_{drama_id}*",
        ]
        for pat in patterns:
            for f in glob_mod.glob(pat):
                try:
                    os.remove(f)
                except Exception:
                    pass
        delete_drama(drama_id)

    delete_series(series_id)
    return JSONResponse({"success": True, "message": "シリーズを削除しました"})


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
    if body.get("appearance"):
        update_character(char_id, appearance=body["appearance"])
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
    if "appearance" in body:
        updates["appearance"] = body["appearance"]
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


@router.get("/api/new-production/characters-list")
async def new_production_characters_list(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    active = get_active_series()
    if active:
        from app.db.database import get_characters_by_series
        chars = get_characters_by_series(active["id"])
    else:
        chars = get_characters()
    result = []
    for c in chars:
        result.append({
            "id": c["id"], "name": c["name"], "role": c.get("role", ""),
            "image_path": c.get("image_path", ""),
            "image_face": c.get("image_face", ""),
            "image_bust": c.get("image_bust", ""),
            "image_fullbody": c.get("image_fullbody", ""),
        })
    return JSONResponse({"characters": result, "series_id": active["id"] if active else None})


@router.post("/api/new-production/assign-character-image")
async def assign_character_image(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        image_url = body.get("image_url", "")
        character_id = body.get("character_id")
        image_type = body.get("image_type", "image_path")
        new_character_name = body.get("new_character_name", "")

        if not image_url:
            return JSONResponse({"error": "画像URLが必要です"})

        src_path = image_url.lstrip("/")
        if src_path.startswith("static/"):
            src_path = "app/" + src_path
        if not os.path.exists(src_path):
            return JSONResponse({"error": "画像ファイルが見つかりません"})

        import shutil, uuid
        os.makedirs("app/static/characters", exist_ok=True)
        ext = os.path.splitext(src_path)[1] or ".png"
        dest_name = f"char_{uuid.uuid4().hex[:8]}{ext}"
        dest_path = f"app/static/characters/{dest_name}"
        shutil.copy2(src_path, dest_path)

        if character_id and character_id != "new":
            update_character(int(character_id), **{image_type: dest_path})
            from app.db.database import get_character_by_id
            char = get_character_by_id(int(character_id))
            return JSONResponse({"ok": True, "character_name": char["name"] if char else "", "path": dest_path})
        else:
            if not new_character_name:
                return JSONResponse({"error": "キャラクター名を入力してください"})
            active = get_active_series()
            series_id = active["id"] if active else None
            char_id = create_character(
                name=new_character_name,
                role="主人公",
                image_path=dest_path,
                series_id=series_id
            )
            return JSONResponse({"ok": True, "character_id": char_id, "character_name": new_character_name, "path": dest_path})
    except Exception as e:
        return JSONResponse({"error": str(e)})


production_tasks = {}


def _get_production_task_key(drama_id, task_type, scene_num=None):
    if scene_num is not None:
        return f"{drama_id}_{task_type}_{scene_num}"
    return f"{drama_id}_{task_type}"


@router.get("/new-production", response_class=HTMLResponse)
async def new_production_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("new_production.html", {"request": request, "user": user})


@router.post("/api/new-production/chat")
async def new_production_chat(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        provider = body.get("provider", "claude")
        message = body.get("message", "").strip()
        history = body.get("history", [])
        if not message:
            return JSONResponse({"error": "メッセージを入力してください"})

        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)
            messages = []
            for h in history:
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": message})
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=messages
            )
            response_text = resp.content[0].text
            return JSONResponse({"response": response_text})

        elif provider == "gemini":
            import httpx
            gemini_key = os.environ.get("GEMINI_API_KEY", "")
            if not gemini_key:
                return JSONResponse({"error": "GEMINI_API_KEYが設定されていません。設定画面で追加してください。"})
            contents = []
            for h in history:
                role = "user" if h["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": h["content"]}]})
            contents.append({"role": "user", "parts": [{"text": message}]})
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    json={"contents": contents}
                )
                if resp.status_code != 200:
                    return JSONResponse({"error": f"Gemini API error: {resp.status_code} {resp.text}"})
                data = resp.json()
                try:
                    response_text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    return JSONResponse({"error": "Geminiからの応答を解析できませんでした"})
                return JSONResponse({"response": response_text})
        else:
            return JSONResponse({"error": "不明なプロバイダー"})
    except Exception as e:
        logger.error(f"New production chat error: {e}")
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/gemini-generate-image")
async def new_production_gemini_image(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        import httpx
        import time as _time

        luma_key = os.environ.get("LUMA_API_KEY", "")
        if not luma_key:
            return JSONResponse({"error": "LUMA_API_KEYが設定されていません。設定画面で追加してください。"})

        form = await request.form()
        prompt = form.get("prompt", "").strip()
        style = form.get("style", "")
        aspect_ratio = form.get("aspect_ratio", "9:16")
        if not prompt:
            return JSONResponse({"error": "プロンプトを入力してください"})

        full_prompt = prompt
        if style:
            style_map = {
                "photorealistic": "photorealistic, ultra-realistic photography",
                "cinematic": "cinematic lighting, movie scene, dramatic",
                "anime": "anime style, Japanese animation",
                "illustration": "digital illustration, detailed artwork",
                "oil_painting": "oil painting style, classical art"
            }
            full_prompt = f"{style_map.get(style, style)}, {prompt}"

        ref_image_urls = []

        ref_images = form.getlist("ref_images")
        upload_dir = "app/static/gemini_images"
        os.makedirs(upload_dir, exist_ok=True)
        for ref_img in ref_images:
            if hasattr(ref_img, 'read'):
                img_data = await ref_img.read()
                if img_data:
                    ext = ".jpg"
                    ct = getattr(ref_img, 'content_type', '') or ''
                    if 'png' in ct:
                        ext = ".png"
                    tmp_name = f"ref_{int(_time.time())}_{len(ref_image_urls)}{ext}"
                    tmp_path = os.path.join(upload_dir, tmp_name)
                    with open(tmp_path, "wb") as f:
                        f.write(img_data)
                    host = str(request.base_url).rstrip("/")
                    ref_image_urls.append(f"{host}/static/gemini_images/{tmp_name}")

        saved_ref_urls_raw = form.get("saved_ref_urls", "")
        if saved_ref_urls_raw:
            import json as _json
            try:
                saved_urls = _json.loads(saved_ref_urls_raw)
                for surl in saved_urls:
                    spath = surl.lstrip("/")
                    if spath.startswith("static/"):
                        if os.path.exists("app/" + spath):
                            host = str(request.base_url).rstrip("/")
                            ref_image_urls.append(f"{host}/{spath}")
            except Exception:
                pass

        luma_headers = {
            "Authorization": f"Bearer {luma_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        body = {
            "prompt": full_prompt,
            "aspect_ratio": aspect_ratio,
            "model": "photon-1",
        }
        if ref_image_urls:
            body["image_ref"] = [{"url": u, "weight": 0.85} for u in ref_image_urls[:4]]

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                "https://api.lumalabs.ai/dream-machine/v1/generations/image",
                headers=luma_headers,
                json=body
            )
            if resp.status_code not in (200, 201):
                logger.error(f"Luma Photon create error: {resp.status_code} {resp.text[:500]}")
                return JSONResponse({"error": f"Luma Photon API error: {resp.status_code}"})

            gen_data = resp.json()
            gen_id = gen_data.get("id")
            if not gen_id:
                return JSONResponse({"error": "Luma Photon: generation IDが取得できませんでした"})

            logger.info(f"Luma Photon image generation started: {gen_id}")

            for _ in range(60):
                await asyncio.sleep(3)
                poll_resp = await client.get(
                    f"https://api.lumalabs.ai/dream-machine/v1/generations/{gen_id}",
                    headers=luma_headers
                )
                if poll_resp.status_code != 200:
                    continue
                poll_data = poll_resp.json()
                state = poll_data.get("state", "")
                if state == "completed":
                    image_url = poll_data.get("assets", {}).get("image")
                    if not image_url:
                        return JSONResponse({"error": "Luma Photon: 画像URLが取得できませんでした"})

                    img_resp = await client.get(image_url)
                    if img_resp.status_code != 200:
                        return JSONResponse({"error": "Luma Photon: 画像ダウンロードに失敗しました"})

                    save_dir = "app/static/gemini_images"
                    os.makedirs(save_dir, exist_ok=True)
                    filename = f"luma_{int(_time.time())}.jpg"
                    filepath = os.path.join(save_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(img_resp.content)

                    return JSONResponse({"images": [f"/static/gemini_images/{filename}"]})
                elif state == "failed":
                    fail_reason = poll_data.get("failure_reason", "不明なエラー")
                    logger.error(f"Luma Photon generation failed: {fail_reason}")
                    return JSONResponse({"error": f"Luma Photon生成失敗: {fail_reason}"})

            return JSONResponse({"error": "Luma Photon: タイムアウト（3分以内に完了しませんでした）"})
    except Exception as e:
        logger.error(f"Gemini image generation error: {e}")
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/gemini-save")
async def new_production_gemini_save(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        url = body.get("url", "")
        prompt = body.get("prompt", "")
        category = body.get("category", "other")
        if not url:
            return JSONResponse({"error": "URLが必要です"})
        save_dir = "app/static/gemini_saved"
        os.makedirs(save_dir, exist_ok=True)
        import time as _time
        src_path = url.lstrip("/")
        if src_path.startswith("static/"):
            src_path = "app/" + src_path
        if not os.path.exists(src_path):
            return JSONResponse({"error": "画像が見つかりません"})
        import shutil
        ext = os.path.splitext(src_path)[1] or ".png"
        filename = f"saved_{int(_time.time())}_{len(os.listdir(save_dir))}{ext}"
        dst_path = os.path.join(save_dir, filename)
        shutil.copy2(src_path, dst_path)
        meta_path = dst_path + ".meta"
        import json as _json
        meta_data = {"prompt": prompt, "category": category}
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump(meta_data, f, ensure_ascii=False)
        return JSONResponse({"ok": True, "filename": filename})
    except Exception as e:
        logger.error(f"Gemini save error: {e}")
        return JSONResponse({"error": str(e)})


@router.get("/api/new-production/gemini-gallery")
async def new_production_gemini_gallery(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    save_dir = "app/static/gemini_saved"
    os.makedirs(save_dir, exist_ok=True)
    images = []
    for f in sorted(os.listdir(save_dir), reverse=True):
        if f.endswith(".meta"):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        prompt = ""
        category = "other"
        meta_path = os.path.join(save_dir, f + ".meta")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                raw = mf.read().strip()
                try:
                    import json as _json
                    meta_obj = _json.loads(raw)
                    prompt = meta_obj.get("prompt", "")
                    category = meta_obj.get("category", "other")
                    scene_num = meta_obj.get("scene_number")
                    if category == "scene" and scene_num:
                        category = f"scene{scene_num}"
                except Exception:
                    prompt = raw
        images.append({
            "filename": f,
            "url": f"/static/gemini_saved/{f}",
            "prompt": prompt,
            "category": category
        })
    return JSONResponse({"images": images})


@router.post("/api/new-production/gemini-delete")
async def new_production_gemini_delete(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        filename = os.path.basename(body.get("filename", ""))
        if not filename:
            return JSONResponse({"error": "ファイル名が必要です"})
        save_dir = "app/static/gemini_saved"
        filepath = os.path.join(save_dir, filename)
        meta_path = filepath + ".meta"
        if os.path.exists(filepath):
            os.remove(filepath)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"Gemini delete error: {e}")
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/save-content")
async def new_production_save_content(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        content_type = body.get("type", "other")
        content = body.get("content", "")
        if not name or not content:
            return JSONResponse({"error": "名前と内容を入力してください"})
        save_dir = "app/static/saved_contents"
        os.makedirs(save_dir, exist_ok=True)
        import time as _time
        ts = int(_time.time())
        safe_name = "".join(c for c in name if c.isalnum() or c in "ぁ-ん゛゜ァ-ヶー一-龠々〇〻_ -").strip()[:50]
        filename = f"{ts}_{content_type}_{safe_name}.txt"
        filepath = os.path.join(save_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n# Type: {content_type}\n# Date: {_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{content}")
        return JSONResponse({"ok": True, "filename": filename})
    except Exception as e:
        logger.error(f"Save content error: {e}")
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/kling-generate")
async def new_production_kling_generate(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        form = await request.form()
        prompt = form.get("prompt", "").strip()
        mode = form.get("mode", "text2video")
        aspect_ratio = form.get("aspect_ratio", "9:16")
        duration = form.get("duration", "5")
        if not prompt:
            return JSONResponse({"error": "プロンプトを入力してください"})
        if len(prompt) > 2500:
            prompt = prompt[:2500]

        from app.services.video.kling_service import _get_kling_token
        token = _get_kling_token()
        if not token:
            return JSONResponse({"error": "Kling APIキーが設定されていません"})

        import httpx
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        if mode == "image2video":
            import base64
            image_b64 = None
            image_file = form.get("image")
            if image_file and hasattr(image_file, 'read'):
                image_data = await image_file.read()
                if image_data:
                    image_b64 = base64.b64encode(image_data).decode("utf-8")

            if not image_b64:
                saved_ref_raw = form.get("saved_ref_urls", "")
                if saved_ref_raw:
                    import json as _json
                    try:
                        ref_urls = _json.loads(saved_ref_raw)
                        if ref_urls:
                            ref_path = ref_urls[0].lstrip("/")
                            if ref_path.startswith("static/"):
                                ref_path = "app/" + ref_path
                            if os.path.exists(ref_path):
                                with open(ref_path, "rb") as rf:
                                    image_b64 = base64.b64encode(rf.read()).decode("utf-8")
                    except Exception:
                        pass

            if image_b64:
                model = "kling-v3" if duration in ("15",) else "kling-v2-master"
                payload = {
                    "model_name": model,
                    "prompt": prompt,
                    "image": image_b64,
                    "aspect_ratio": aspect_ratio,
                    "duration": duration,
                    "cfg_scale": 0.5,
                }
                if model == "kling-v3":
                    payload["mode"] = "pro"
                    payload["sound"] = "on"
                else:
                    payload["with_audio"] = True
                api_url = "https://api.klingai.com/v1/videos/image2video"
            else:
                return JSONResponse({"error": "画像を選択してください（ファイルまたはGemini保存画像）"})
        else:
            model = "kling-v3" if duration in ("15",) else "kling-v2-master"
            payload = {
                "model_name": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "duration": duration,
                "cfg_scale": 0.5,
            }
            if model == "kling-v3":
                payload["mode"] = "pro"
                payload["sound"] = "on"
            else:
                payload["with_audio"] = True
            api_url = "https://api.klingai.com/v1/videos/text2video"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            if resp.status_code != 200:
                err_detail = resp.text
                logger.error(f"Kling API error {resp.status_code}: {err_detail}")
                return JSONResponse({"error": f"Kling API error: {resp.status_code} - {err_detail}"})
            data = resp.json()
            task_id = data.get("data", {}).get("task_id", "")
            if not task_id:
                return JSONResponse({"error": "タスクIDを取得できませんでした"})
            return JSONResponse({"task_id": task_id})
    except Exception as e:
        logger.error(f"Kling generate error: {e}")
        return JSONResponse({"error": str(e)})


@router.get("/api/new-production/kling-status/{task_id}")
async def new_production_kling_status(request: Request, task_id: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.services.video.kling_service import _get_kling_token
        token = _get_kling_token()
        import httpx
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.klingai.com/v1/videos/text2video/{task_id}",
                headers=headers
            )
            if resp.status_code != 200:
                return JSONResponse({"status": "processing"})
            data = resp.json()
            task_data = data.get("data", {})
            status = task_data.get("task_status", "processing")
            if status == "succeed":
                videos = task_data.get("task_result", {}).get("videos", [])
                if videos:
                    video_url = videos[0].get("url", "")
                    return JSONResponse({"status": "completed", "video_url": video_url})
            elif status == "failed":
                return JSONResponse({"status": "failed", "error": task_data.get("task_status_msg", "")})
            return JSONResponse({"status": status})
    except Exception as e:
        return JSONResponse({"status": "processing"})


@router.post("/api/new-production/kling-save")
async def new_production_kling_save(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        video_url = body.get("video_url", "")
        prompt = body.get("prompt", "")
        if not video_url:
            return JSONResponse({"error": "動画URLが必要です"})
        is_local = body.get("is_local", False)
        save_dir = "app/static/kling_saved"
        os.makedirs(save_dir, exist_ok=True)
        import time as _time
        import httpx
        import shutil
        ts = int(_time.time())
        filename = f"kling_{ts}.mp4"
        filepath = os.path.join(save_dir, filename)
        if is_local:
            local_path = video_url.lstrip("/")
            if local_path.startswith("static/"):
                local_path = "app/" + local_path
            if os.path.exists(local_path):
                shutil.copy2(local_path, filepath)
            else:
                return JSONResponse({"error": "ローカルファイルが見つかりません"})
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(video_url)
                if resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                else:
                    return JSONResponse({"error": f"動画ダウンロード失敗: {resp.status_code}"})
        meta_path = filepath + ".meta"
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        return JSONResponse({"ok": True, "filename": filename})
    except Exception as e:
        logger.error(f"Kling save error: {e}")
        return JSONResponse({"error": str(e)})


@router.get("/api/new-production/kling-gallery")
async def new_production_kling_gallery(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    save_dir = "app/static/kling_saved"
    os.makedirs(save_dir, exist_ok=True)
    videos = []
    for f in sorted(os.listdir(save_dir), reverse=True):
        if f.endswith(".meta"):
            continue
        if not f.endswith(".mp4"):
            continue
        prompt = ""
        meta_path = os.path.join(save_dir, f + ".meta")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                prompt = mf.read().strip()
        videos.append({
            "filename": f,
            "url": f"/static/kling_saved/{f}",
            "prompt": prompt
        })
    return JSONResponse({"videos": videos})


@router.post("/api/new-production/kling-delete")
async def new_production_kling_delete(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        filename = os.path.basename(body.get("filename", ""))
        if not filename:
            return JSONResponse({"error": "ファイル名が必要です"})
        save_dir = "app/static/kling_saved"
        filepath = os.path.join(save_dir, filename)
        meta_path = filepath + ".meta"
        if os.path.exists(filepath):
            os.remove(filepath)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"Kling delete error: {e}")
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/editor-combine")
async def new_production_editor_combine(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        import time as _time
        import subprocess
        import json as _json

        form = await request.form()
        clips_raw = form.get("clips", "[]")
        clips = _json.loads(clips_raw)
        img_duration = float(form.get("img_duration", "3"))
        transition = form.get("transition", "none")
        transition_dur = float(form.get("transition_duration", "0.5"))
        resolution = form.get("resolution", "1080x1920")
        res_parts = resolution.split("x")
        width = int(res_parts[0])
        height = int(res_parts[1])

        if not clips:
            return JSONResponse({"error": "素材がありません"})

        work_dir = "app/static/editor_tmp"
        os.makedirs(work_dir, exist_ok=True)
        out_dir = "app/static/editor_output"
        os.makedirs(out_dir, exist_ok=True)
        ts = int(_time.time())

        audio_file = form.get("audio")
        audio_volume = int(form.get("audio_volume", "100"))
        audio_path = None
        if audio_file and hasattr(audio_file, 'read'):
            audio_data = await audio_file.read()
            if audio_data:
                audio_ext = ".mp3"
                if hasattr(audio_file, 'filename') and audio_file.filename:
                    audio_ext = os.path.splitext(audio_file.filename)[1] or ".mp3"
                audio_path = os.path.join(work_dir, f"audio_{ts}{audio_ext}")
                with open(audio_path, "wb") as f:
                    f.write(audio_data)

        input_files = []
        for i, clip in enumerate(clips):
            trim_start = clip.get("trim_start")
            trim_end = clip.get("trim_end")
            if clip["source"] == "file":
                file_key = clip["file_key"]
                uploaded = form.get(file_key)
                if uploaded:
                    ext = ".mp4" if clip["type"] == "video" else ".png"
                    tmp_path = os.path.join(work_dir, f"clip_{ts}_{i}{ext}")
                    content = await uploaded.read()
                    with open(tmp_path, "wb") as f:
                        f.write(content)
                    input_files.append({"path": tmp_path, "type": clip["type"], "trim_start": trim_start, "trim_end": trim_end})
            elif clip["source"] == "server":
                url = clip["url"].lstrip("/")
                if url.startswith("static/"):
                    url = "app/" + url
                if os.path.exists(url):
                    input_files.append({"path": url, "type": clip["type"], "trim_start": trim_start, "trim_end": trim_end})

        if not input_files:
            return JSONResponse({"error": "有効な素材がありません"})

        scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"

        segment_files = []
        for i, item in enumerate(input_files):
            seg_path = os.path.join(work_dir, f"seg_{ts}_{i}.mp4")
            if item["type"] == "image":
                cmd = [
                    "ffmpeg", "-y", "-loop", "1", "-i", item["path"],
                    "-c:v", "libx264", "-t", str(img_duration),
                    "-vf", scale_filter,
                    "-pix_fmt", "yuv420p", "-r", "30",
                    seg_path
                ]
            else:
                cmd = ["ffmpeg", "-y"]
                if item.get("trim_start"):
                    cmd.extend(["-ss", str(item["trim_start"])])
                cmd.extend(["-i", item["path"]])
                if item.get("trim_end"):
                    duration = float(item["trim_end"])
                    if item.get("trim_start"):
                        duration -= float(item["trim_start"])
                    cmd.extend(["-t", str(max(0.1, duration))])
                cmd.extend([
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    "-an",
                    seg_path
                ])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.error(f"FFmpeg segment error: {result.stderr}")
                return JSONResponse({"error": f"セグメント{i+1}の処理に失敗: {result.stderr[:200]}"})
            segment_files.append(seg_path)

        output_filename = f"combined_{ts}.mp4"
        output_path = os.path.join(out_dir, output_filename)

        if len(segment_files) == 1:
            import shutil
            shutil.copy2(segment_files[0], output_path)
        else:
            if transition == "none":
                concat_list = os.path.join(work_dir, f"concat_{ts}.txt")
                with open(concat_list, "w") as f:
                    for seg in segment_files:
                        f.write(f"file '{os.path.abspath(seg)}'\n")
                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_list, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    return JSONResponse({"error": f"結合失敗: {result.stderr[:200]}"})
            else:
                filter_parts = []
                inputs_cmd = []
                for i, seg in enumerate(segment_files):
                    inputs_cmd.extend(["-i", seg])

                n = len(segment_files)
                td = transition_dur

                for i in range(n):
                    filter_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}];")

                cur = "v0"
                for i in range(1, n):
                    out = f"vout{i}" if i < n - 1 else "vfinal"
                    filter_parts.append(
                        f"[{cur}][v{i}]xfade=transition={transition}:duration={td}:offset={{off_{i}}}[{out}];"
                    )
                    cur = out

                filter_str = "".join(filter_parts).rstrip(";")

                probe_durations = []
                for seg in segment_files:
                    probe_cmd = [
                        "ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", seg
                    ]
                    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
                    try:
                        dur = float(probe_result.stdout.strip())
                    except ValueError:
                        dur = 5.0
                    probe_durations.append(dur)

                offset = probe_durations[0] - td
                for i in range(1, n):
                    filter_str = filter_str.replace(f"{{off_{i}}}", f"{offset:.3f}")
                    if i < n - 1:
                        offset += probe_durations[i] - td

                cmd = ["ffmpeg", "-y"] + inputs_cmd + [
                    "-filter_complex", filter_str,
                    "-map", "[vfinal]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    logger.error(f"FFmpeg combine error: {result.stderr}")
                    concat_list = os.path.join(work_dir, f"concat_{ts}.txt")
                    with open(concat_list, "w") as f:
                        for seg in segment_files:
                            f.write(f"file '{os.path.abspath(seg)}'\n")
                    cmd_fallback = [
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", concat_list, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        output_path
                    ]
                    result2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
                    if result2.returncode != 0:
                        return JSONResponse({"error": f"結合失敗: {result2.stderr[:200]}"})

        if audio_path and os.path.exists(audio_path):
            video_with_audio = os.path.join(out_dir, f"combined_audio_{ts}.mp4")
            vol = audio_volume / 100.0
            cmd_audio = [
                "ffmpeg", "-y",
                "-i", output_path,
                "-i", audio_path,
                "-filter_complex", f"[1:a]volume={vol}[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                video_with_audio
            ]
            result_audio = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=300)
            if result_audio.returncode == 0:
                os.replace(video_with_audio, output_path)
            else:
                logger.error(f"Audio mix error: {result_audio.stderr}")

        for seg in segment_files:
            try:
                os.remove(seg)
            except Exception:
                pass
        for item in input_files:
            if item["path"].startswith(work_dir):
                try:
                    os.remove(item["path"])
                except Exception:
                    pass
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass

        return JSONResponse({"ok": True, "url": f"/static/editor_output/{output_filename}"})

    except Exception as e:
        logger.error(f"Editor combine error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/youtube-upload")
async def new_production_youtube_upload(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        video_url = body.get("video_url", "")
        title = body.get("title", "")
        description = body.get("description", "")
        tags_str = body.get("tags", "")
        privacy = body.get("privacy", "public")

        if not video_url or not title:
            return JSONResponse({"error": "タイトルと動画が必要です"})

        video_path = video_url.lstrip("/")
        if video_path.startswith("static/"):
            video_path = "app/" + video_path
        if not os.path.exists(video_path):
            return JSONResponse({"error": "動画ファイルが見つかりません"})

        if not title.endswith("#Shorts") and "#Shorts" not in title:
            title = title + " #Shorts"

        if "#Shorts" not in description:
            description = description + "\n\n#Shorts" if description else "#Shorts"

        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        if "Shorts" not in tags:
            tags.append("Shorts")

        from app.services.youtube.youtube_service import upload_video, is_youtube_connected
        if not is_youtube_connected():
            return JSONResponse({"error": "YouTubeが接続されていません。設定ページからYouTube OAuthを設定してください。"})

        import threading
        result = {"video_id": None, "error": None}

        def do_upload():
            try:
                vid = upload_video(
                    video_path=video_path,
                    title=title,
                    description=description,
                    tags=tags,
                    thumbnail_path=None,
                    privacy_status=privacy
                )
                result["video_id"] = vid
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=do_upload)
        t.start()
        t.join(timeout=300)

        if result["error"]:
            return JSONResponse({"error": result["error"]})
        if result["video_id"]:
            return JSONResponse({"ok": True, "video_id": result["video_id"]})
        return JSONResponse({"error": "アップロードがタイムアウトしました"})

    except Exception as e:
        logger.error(f"YouTube upload error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)})


@router.get("/api/new-production/automation-schedules")
async def get_automation_schedules(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from app.services.automation import get_all_schedules, get_running_status
    schedules = get_all_schedules()
    running = get_running_status()
    for s in schedules:
        s["running_status"] = running.get(s["id"])
        if s.get("created_at"):
            s["created_at"] = s["created_at"].isoformat()
    return JSONResponse({"schedules": schedules})


@router.post("/api/new-production/automation-schedule")
async def create_automation_schedule(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        from app.services.automation import create_schedule
        sid = create_schedule(
            name=body.get("name", "自動生成"),
            schedule_time=body.get("schedule_time", "09:00"),
            days_of_week=body.get("days_of_week", "mon,tue,wed,thu,fri,sat,sun"),
            pipeline_mode=body.get("pipeline_mode", "full"),
            auto_upload_youtube=body.get("auto_upload_youtube", False),
            auto_upload_tiktok=body.get("auto_upload_tiktok", False),
            youtube_privacy=body.get("youtube_privacy", "public"),
            custom_theme=body.get("custom_theme") or None,
            max_scenes=body.get("max_scenes") or None
        )
        return JSONResponse({"ok": True, "id": sid})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/automation-schedule/{schedule_id}/update")
async def update_automation_schedule(request: Request, schedule_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        from app.services.automation import update_schedule
        update_schedule(schedule_id, **body)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/automation-schedule/{schedule_id}/toggle")
async def toggle_automation_schedule(request: Request, schedule_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.services.automation import get_schedule_by_id, update_schedule
        s = get_schedule_by_id(schedule_id)
        if not s:
            return JSONResponse({"error": "スケジュールが見つかりません"})
        update_schedule(schedule_id, enabled=not s["enabled"])
        return JSONResponse({"ok": True, "enabled": not s["enabled"]})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/automation-schedule/{schedule_id}/run")
async def run_automation_now(request: Request, schedule_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.services.automation import run_job_now, get_running_status
        status = get_running_status(schedule_id)
        if status and status.get("status") == "running":
            return JSONResponse({"error": "既に実行中です"})
        run_job_now(schedule_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@router.post("/api/new-production/automation-schedule/{schedule_id}/delete")
async def delete_automation_schedule(request: Request, schedule_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.services.automation import delete_schedule
        delete_schedule(schedule_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@router.get("/api/new-production/automation-logs")
async def get_automation_logs_api(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from app.services.automation import get_automation_logs, get_running_status
    schedule_id = request.query_params.get("schedule_id")
    logs = get_automation_logs(limit=30, schedule_id=int(schedule_id) if schedule_id else None)
    running = get_running_status()
    for l in logs:
        if l.get("started_at"):
            l["started_at"] = l["started_at"].isoformat()
        if l.get("finished_at"):
            l["finished_at"] = l["finished_at"].isoformat()
    return JSONResponse({"logs": logs, "running": {str(k): v for k, v in running.items()}})


@router.get("/production", response_class=HTMLResponse)
async def production_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    dramas = get_all_dramas()
    if dramas:
        dramas.sort(key=lambda d: d.get("id", 0), reverse=True)
        return RedirectResponse(url=f"/production/{dramas[0]['id']}", status_code=302)
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
        saudio_path = f"app/static/audio/drama_{drama_id}_scene_{sn}.mp3"
        scene_assets.append({
            "scene_number": sn,
            "has_image": os.path.exists(img_path),
            "image_url": f"/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png" if os.path.exists(img_path) else None,
            "has_video": os.path.exists(vid_path),
            "video_url": f"/static/scenes/drama_{drama_id}_scene_{sn}.mp4" if os.path.exists(vid_path) else None,
            "has_scene_audio": os.path.exists(saudio_path) and os.path.getsize(saudio_path) > 500,
            "scene_audio_url": f"/static/audio/drama_{drama_id}_scene_{sn}.mp3" if os.path.exists(saudio_path) and os.path.getsize(saudio_path) > 500 else None,
        })

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    thumb_path = f"app/static/thumbnail/drama_{drama_id}.png"
    video_path = f"app/static/videos/drama_{drama_id}.mp4"

    all_series_list = get_all_series()
    series_dramas_map = {}
    for s in all_series_list:
        s_dramas = get_dramas_by_series(s["id"])
        s_dramas.sort(key=lambda d: d.get("series_episode", 0))
        series_dramas_map[s["id"]] = s_dramas

    no_series_dramas = [d for d in get_all_dramas() if not d.get("series_id")]
    no_series_dramas.sort(key=lambda d: d.get("id", 0), reverse=True)

    series = None
    characters = []
    if drama.get("series_id"):
        for s in all_series_list:
            if s.get("id") == drama["series_id"]:
                series = s
                break
        characters = get_characters_by_series(drama["series_id"])
    else:
        characters = get_characters()

    subtitle_path = f"app/static/subtitle/drama_{drama_id}.srt"

    return templates.TemplateResponse("production.html", {
        "request": request,
        "user": user,
        "drama": drama,
        "series": series,
        "characters": characters,
        "all_series_list": all_series_list,
        "series_dramas_map": series_dramas_map,
        "no_series_dramas": no_series_dramas,
        "script_data": script_data,
        "scenes": scenes,
        "scene_assets": scene_assets,
        "has_audio": os.path.exists(audio_path),
        "audio_url": f"/static/audio/drama_{drama_id}.mp3" if os.path.exists(audio_path) else None,
        "has_thumbnail": os.path.exists(thumb_path),
        "thumbnail_url": f"/static/thumbnail/drama_{drama_id}.png" if os.path.exists(thumb_path) else None,
        "has_final_video": os.path.exists(video_path),
        "final_video_url": f"/static/videos/drama_{drama_id}.mp4" if os.path.exists(video_path) else None,
        "has_subtitle": os.path.exists(subtitle_path),
        "subtitle_url": f"/static/subtitle/drama_{drama_id}.srt" if os.path.exists(subtitle_path) else None,
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
        task_saudio_key = _get_production_task_key(drama_id, "scene_audio", sn)

        scene_audio_path = f"app/static/audio/drama_{drama_id}_scene_{sn}.mp3"
        scene_audio_size = os.path.getsize(scene_audio_path) if os.path.exists(scene_audio_path) else 0

        scene_assets.append({
            "scene_number": sn,
            "has_image": os.path.exists(img_path) and img_size > 1000,
            "image_url": f"/static/scene_images/drama_{drama_id}_scene_{sn}_ai.png?t={int(os.path.getmtime(img_path))}" if os.path.exists(img_path) and img_size > 1000 else None,
            "has_video": os.path.exists(vid_path) and vid_size > 5000,
            "video_url": f"/static/scenes/drama_{drama_id}_scene_{sn}.mp4?t={int(os.path.getmtime(vid_path))}" if os.path.exists(vid_path) and vid_size > 5000 else None,
            "has_scene_audio": os.path.exists(scene_audio_path) and scene_audio_size > 500,
            "scene_audio_url": f"/static/audio/drama_{drama_id}_scene_{sn}.mp3?t={int(os.path.getmtime(scene_audio_path))}" if os.path.exists(scene_audio_path) and scene_audio_size > 500 else None,
            "image_generating": production_tasks.get(task_img_key, {}).get("status") == "running",
            "video_generating": production_tasks.get(task_vid_key, {}).get("status") == "running",
            "scene_audio_generating": production_tasks.get(task_saudio_key, {}).get("status") == "running",
            "image_error": production_tasks.get(task_img_key, {}).get("error", ""),
            "video_error": production_tasks.get(task_vid_key, {}).get("error", ""),
            "scene_audio_error": production_tasks.get(task_saudio_key, {}).get("error", ""),
        })

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    thumb_path = f"app/static/thumbnail/drama_{drama_id}.png"
    video_path = f"app/static/videos/drama_{drama_id}.mp4"

    task_audio_key = _get_production_task_key(drama_id, "audio")
    task_thumb_key = _get_production_task_key(drama_id, "thumbnail")
    task_assemble_key = _get_production_task_key(drama_id, "assemble")
    task_subtitle_key = _get_production_task_key(drama_id, "subtitle")

    subtitle_path = f"app/static/subtitle/drama_{drama_id}.srt"

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
        "has_subtitle": os.path.exists(subtitle_path) and os.path.getsize(subtitle_path) > 10,
        "subtitle_url": f"/static/subtitle/drama_{drama_id}.srt?t={int(os.path.getmtime(subtitle_path))}" if os.path.exists(subtitle_path) and os.path.getsize(subtitle_path) > 10 else None,
        "subtitle_generating": production_tasks.get(task_subtitle_key, {}).get("status") == "running",
        "subtitle_error": production_tasks.get(task_subtitle_key, {}).get("error", ""),
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

    try:
        body = await request.json()
        custom_prompt = body.get("prompt", "").strip()
        req_char_ids = body.get("character_ids", [])
    except Exception:
        custom_prompt = ""
        req_char_ids = []

    image_prompt = custom_prompt if custom_prompt else scene.get("description", "")

    speaker = scene.get("speaker", "")
    series_id = drama.get("series_id")
    scene_characters = []
    if series_id:
        all_chars = get_characters_by_series(series_id)
    else:
        all_chars = get_characters()

    character_ids = req_char_ids if req_char_ids else scene.get("character_ids", [])
    character_id = scene.get("character_id")
    if not character_ids and character_id:
        character_ids = [character_id]
    character_img = None

    if character_ids:
        char_descs = []
        char_appearances = []
        for cid in character_ids:
            ch = get_character_by_id(int(cid))
            if ch:
                scene_characters.append(ch)
                if ch.get("appearance"):
                    char_appearances.append(f"{ch.get('role', ch['name'])}: {ch['appearance']}")
                elif ch.get("description"):
                    char_descs.append(f"{ch.get('role', ch['name'])}: {ch['description']}")
                if not character_img:
                    character_img = ch.get("image_face") or ch.get("image_bust") or ch.get("image_path")
        if char_appearances:
            image_prompt = f"{image_prompt}. Characters: {'; '.join(char_appearances)}"
        elif char_descs:
            image_prompt = f"{image_prompt}。登場人物: {', '.join(char_descs)}"
    elif speaker and speaker != "ナレーション":
        speaker_parts = [sp.strip() for sp in speaker.split("+")]
        for sp in speaker_parts:
            if sp == "ナレーション":
                continue
            for ch in all_chars:
                if (ch.get("role") == sp or ch.get("name") == sp) and ch not in scene_characters:
                    scene_characters.append(ch)
                    break

        if scene_characters:
            char_descs = []
            char_appearances = []
            for ch in scene_characters:
                if ch.get("appearance"):
                    char_appearances.append(f"{ch.get('role', ch['name'])}: {ch['appearance']}")
                elif ch.get("description"):
                    char_descs.append(f"{ch.get('role', ch['name'])}: {ch['description']}")
                if not character_img:
                    character_img = ch.get("image_face") or ch.get("image_bust") or ch.get("image_path")
            if char_appearances:
                image_prompt = f"{image_prompt}. Characters: {'; '.join(char_appearances)}"
            elif char_descs:
                image_prompt = f"{image_prompt}。登場人物: {', '.join(char_descs)}"

    char_image_urls = []
    base_url = str(request.base_url).rstrip("/")
    for ch in scene_characters:
        img = ch.get("image_face") or ch.get("image_bust") or ch.get("image_path") or ""
        if img:
            if img.startswith("/static/"):
                char_image_urls.append(f"{base_url}{img}")
            elif img.startswith("http"):
                char_image_urls.append(img)
            else:
                char_image_urls.append(f"{base_url}/static/{img}")

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_image_gen():
        try:
            from app.services.video.scene_generator import _generate_scene_specific_image
            result = _generate_scene_specific_image(
                image_prompt,
                drama_id, scene_num,
                character_image_urls=list(char_image_urls) if char_image_urls else None
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

    try:
        body = await request.json()
        custom_prompt = body.get("prompt", "").strip()
    except Exception:
        custom_prompt = ""

    video_prompt = custom_prompt if custom_prompt else scene.get("description", "")

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_video_gen():
        try:
            from app.services.video.scene_generator import generate_scene_video
            img_path = f"app/static/scene_images/drama_{drama_id}_scene_{scene_num}_ai.png"
            ref_image = img_path if os.path.exists(img_path) else None

            result = generate_scene_video(
                scene_description=video_prompt,
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
            result = generate_voice(narration, drama_id, scenes=scenes, series_id=drama.get("series_id"))
            if result and os.path.exists(result):
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "音声生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_audio_gen, daemon=True)
    thread.start()

    return JSONResponse({"message": "Audio generation started", "status": "running"})


@router.post("/api/production/{drama_id}/scene-audio/{scene_num}")
async def api_production_scene_audio(request: Request, drama_id: int, scene_num: int):
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

    narration = scene.get("narration", "").strip()
    if not narration:
        return JSONResponse({"error": "このシーンにセリフがありません"}, status_code=400)

    try:
        body = await request.json()
        voice_settings = body.get("voice_settings", None)
    except Exception:
        voice_settings = None

    if voice_settings:
        scene["voice_settings"] = voice_settings
        try:
            script_json = json.dumps(script_data, ensure_ascii=False)
            update_drama(drama_id, script=script_json)
        except Exception:
            pass

    task_key = _get_production_task_key(drama_id, "scene_audio", scene_num)
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_scene_audio():
        try:
            from app.services.video.audio_generator import generate_scene_audio
            result = generate_scene_audio(
                narration=narration,
                speaker=scene.get("speaker", "ナレーション"),
                drama_id=drama_id,
                scene_num=scene_num,
                voice_settings=voice_settings,
                series_id=drama.get("series_id"),
            )
            if result and os.path.exists(result):
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "シーン音声生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_scene_audio, daemon=True)
    thread.start()

    return JSONResponse({"message": f"Scene {scene_num} audio generation started", "status": "running"})


@router.post("/api/production/{drama_id}/thumbnail")
async def api_production_thumbnail(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        body = await request.json()
        custom_prompt = body.get("prompt", "").strip()
        req_char_ids = body.get("character_ids", [])
    except Exception:
        custom_prompt = ""
        req_char_ids = []

    if custom_prompt:
        update_drama(drama_id, thumbnail_prompt=custom_prompt)

    series_id = drama.get("series_id")
    if series_id:
        all_chars = get_characters_by_series(series_id)
    else:
        all_chars = get_characters()

    scene_characters = []
    if req_char_ids:
        for cid in req_char_ids:
            for ch in all_chars:
                if ch["id"] == cid:
                    scene_characters.append(ch)
                    break

    char_image_urls = []
    base_url = str(request.base_url).rstrip("/")
    for ch in scene_characters:
        img = ch.get("image_face") or ch.get("image_bust") or ch.get("image_path") or ""
        if img:
            if img.startswith("/static/"):
                char_image_urls.append(f"{base_url}{img}")
            elif img.startswith("http"):
                char_image_urls.append(img)
            else:
                char_image_urls.append(f"{base_url}/static/{img}")

    char_descs = []
    for ch in scene_characters:
        desc = ch.get("description", "")
        if desc:
            char_descs.append(f"{ch.get('role', ch.get('name', ''))}: {desc}")

    task_key = _get_production_task_key(drama_id, "thumbnail")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    thumb_prompt = custom_prompt if custom_prompt else None
    if thumb_prompt and char_descs:
        thumb_prompt = f"{thumb_prompt}。登場人物: {', '.join(char_descs)}"
    elif not thumb_prompt and char_descs:
        thumb_prompt = None

    def run_thumb_gen():
        try:
            from app.services.video.image_generator import generate_thumbnail
            result = generate_thumbnail(
                title=drama.get("title", ""),
                genre=drama.get("genre", "CEOドラマ"),
                drama_id=drama_id,
                episode_number=drama.get("series_episode") or drama.get("episode_number"),
                custom_prompt=thumb_prompt,
                character_image_url=char_image_urls[0] if len(char_image_urls) == 1 else None,
                character_image_urls=char_image_urls if len(char_image_urls) > 1 else None,
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


@router.post("/api/production/{drama_id}/upload-scene-image/{scene_num}")
async def api_upload_scene_image(request: Request, drama_id: int, scene_num: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    form = await request.form()
    image = form.get("image")
    if not image:
        return JSONResponse({"error": "画像が必要です"}, status_code=400)

    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
    MAX_SIZE = 10 * 1024 * 1024

    ext = os.path.splitext(image.filename)[1].lower() if image.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"許可されていないファイル形式です。{', '.join(ALLOWED_EXTENSIONS)} のみ"}, status_code=400)

    contents = await image.read()
    if len(contents) > MAX_SIZE:
        return JSONResponse({"error": "ファイルサイズが大きすぎます（最大10MB）"}, status_code=400)

    os.makedirs("app/static/scene_images", exist_ok=True)
    output_path = f"app/static/scene_images/drama_{drama_id}_scene_{scene_num}_ai.png"

    if ext in (".jpg", ".jpeg", ".webp"):
        import subprocess
        temp_path = f"app/static/scene_images/drama_{drama_id}_scene_{scene_num}_upload{ext}"
        with open(temp_path, "wb") as f:
            f.write(contents)
        subprocess.run(["ffmpeg", "-y", "-i", temp_path, output_path], capture_output=True, timeout=10)
        if os.path.exists(temp_path) and temp_path != output_path:
            os.remove(temp_path)
    else:
        with open(output_path, "wb") as f:
            f.write(contents)

    url = f"/static/scene_images/drama_{drama_id}_scene_{scene_num}_ai.png"
    return JSONResponse({"url": url, "message": "アップロード完了"})


@router.post("/api/production/{drama_id}/upload-thumbnail")
async def api_upload_thumbnail(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "認証が必要です"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    form = await request.form()
    image = form.get("image")
    if not image:
        return JSONResponse({"error": "画像が必要です"}, status_code=400)

    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
    MAX_SIZE = 10 * 1024 * 1024

    ext = os.path.splitext(image.filename)[1].lower() if image.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"許可されていないファイル形式です。{', '.join(ALLOWED_EXTENSIONS)} のみ"}, status_code=400)

    contents = await image.read()
    if len(contents) > MAX_SIZE:
        return JSONResponse({"error": "ファイルサイズが大きすぎます（最大10MB）"}, status_code=400)

    os.makedirs("app/static/thumbnail", exist_ok=True)
    output_path = f"app/static/thumbnail/drama_{drama_id}.png"

    if ext in (".jpg", ".jpeg", ".webp"):
        import subprocess
        temp_path = f"app/static/thumbnail/drama_{drama_id}_upload{ext}"
        with open(temp_path, "wb") as f:
            f.write(contents)
        subprocess.run(["ffmpeg", "-y", "-i", temp_path, output_path], capture_output=True, timeout=10)
        if os.path.exists(temp_path) and temp_path != output_path:
            os.remove(temp_path)
    else:
        with open(output_path, "wb") as f:
            f.write(contents)

    update_drama(drama_id, thumbnail_url=output_path)

    url = f"/static/thumbnail/drama_{drama_id}.png"
    return JSONResponse({"url": url, "message": "アップロード完了"})


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

    try:
        body = await request.json()
    except Exception:
        body = {}
    bgm_file = body.get("bgm_file", "")
    bgm_volume = float(body.get("bgm_volume", 0.15))
    bgm_path = None
    if bgm_file:
        safe_bgm = os.path.basename(bgm_file)
        bgm_path = os.path.join("app/static/bgm", safe_bgm)
        if not os.path.exists(bgm_path):
            bgm_path = None

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_assemble():
        try:
            from app.services.video.subtitle_generator import generate_subtitle
            from app.services.video.video_generator import edit_video

            existing_sub = f"app/static/subtitle/drama_{drama_id}.srt"
            if os.path.exists(existing_sub) and os.path.getsize(existing_sub) > 10:
                subtitle_path = existing_sub
            else:
                subtitle_path = generate_subtitle(scenes, drama_id)
            final_video = edit_video(scene_videos, audio_path, drama_id, subtitle_path=subtitle_path, bgm_path=bgm_path, bgm_volume=bgm_volume)
            update_drama(drama_id, video_url=final_video, status="ready")
            production_tasks[task_key] = {"status": "done", "error": ""}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_assemble, daemon=True)
    thread.start()

    return JSONResponse({"message": "Video assembly started", "status": "running"})


@router.post("/api/production/{drama_id}/subtitle")
async def api_production_subtitle(request: Request, drama_id: int):
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
    if not scenes:
        return JSONResponse({"error": "シーンがありません"}, status_code=400)

    task_key = _get_production_task_key(drama_id, "subtitle")
    if production_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already generating"}, status_code=409)

    production_tasks[task_key] = {"status": "running", "error": ""}

    def run_subtitle():
        try:
            from app.services.video.subtitle_generator import generate_subtitle
            result = generate_subtitle(scenes, drama_id)
            if result and os.path.exists(result):
                production_tasks[task_key] = {"status": "done", "error": ""}
            else:
                production_tasks[task_key] = {"status": "error", "error": "字幕生成に失敗しました"}
        except Exception as e:
            production_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_subtitle, daemon=True)
    thread.start()

    return JSONResponse({"message": "Subtitle generation started", "status": "running"})


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


@router.post("/api/production/{drama_id}/generate-theme")
async def api_production_generate_theme(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "ドラマが見つかりません"}, status_code=404)

    result = generate_theme_only(genre=drama.get("genre", "CEOドラマ"))
    theme_text = result.get("theme", "") if isinstance(result, dict) else str(result)
    if theme_text:
        update_drama(drama_id, theme=theme_text)
    return JSONResponse({"success": True, "theme": theme_text})


@router.post("/api/production/{drama_id}/generate-script")
async def api_production_generate_script(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "ドラマが見つかりません"}, status_code=404)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    theme = body.get("theme", "").strip() or (drama.get("theme") or "").strip()
    if not theme:
        return JSONResponse({"error": "テーマが必要です。先にテーマを設定してください。"}, status_code=400)

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


@router.get("/api/bgm")
async def api_bgm_list(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    bgm_dir = "app/static/bgm"
    os.makedirs(bgm_dir, exist_ok=True)
    files = []
    for f in sorted(os.listdir(bgm_dir)):
        if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")):
            fpath = os.path.join(bgm_dir, f)
            size_kb = round(os.path.getsize(fpath) / 1024)
            files.append({"filename": f, "url": f"/static/bgm/{f}", "size_kb": size_kb})
    return JSONResponse(files)


@router.post("/api/bgm/upload")
async def api_bgm_upload(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    form = await request.form()
    file = form.get("file")
    if not file:
        return JSONResponse({"error": "ファイルが必要です"}, status_code=400)

    filename = file.filename.replace(" ", "_")
    if not filename.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")):
        return JSONResponse({"error": "MP3/WAV/M4A/OGG形式のみ対応"}, status_code=400)

    bgm_dir = "app/static/bgm"
    os.makedirs(bgm_dir, exist_ok=True)
    save_path = os.path.join(bgm_dir, filename)
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    return JSONResponse({"success": True, "filename": filename, "url": f"/static/bgm/{filename}"})


@router.delete("/api/bgm/{filename}")
async def api_bgm_delete(request: Request, filename: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    safe_name = os.path.basename(filename)
    fpath = os.path.join("app/static/bgm", safe_name)
    if not os.path.exists(fpath):
        return JSONResponse({"error": "Not found"}, status_code=404)

    os.remove(fpath)
    return JSONResponse({"success": True})


@router.get("/editor/{drama_id}", response_class=HTMLResponse)
async def editor_page(request: Request, drama_id: int):
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
            "has_video": os.path.exists(vid_path) and os.path.getsize(vid_path) > 5000,
            "video_url": f"/static/scenes/drama_{drama_id}_scene_{sn}.mp4?t={int(os.path.getmtime(vid_path))}" if os.path.exists(vid_path) and os.path.getsize(vid_path) > 5000 else None,
        })

    video_path = f"app/static/videos/drama_{drama_id}.mp4"
    subtitle_path = f"app/static/subtitle/drama_{drama_id}.srt"

    return templates.TemplateResponse("editor.html", {
        "request": request,
        "user": user,
        "drama": drama,
        "scenes": scenes,
        "scene_assets": scene_assets,
        "has_final_video": os.path.exists(video_path),
        "final_video_url": f"/static/videos/drama_{drama_id}.mp4?t={int(os.path.getmtime(video_path))}" if os.path.exists(video_path) else None,
        "has_subtitle": os.path.exists(subtitle_path),
    })


editor_tasks = {}

@router.post("/api/editor/{drama_id}/assemble")
async def api_editor_assemble(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    drama = get_drama_by_id(drama_id)
    if not drama:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    scene_order = body.get("scene_order", [])
    if not scene_order:
        return JSONResponse({"error": "シーンが指定されていません"}, status_code=400)

    missing = []
    for item in scene_order:
        sn = item.get("scene_number", 0)
        vid_path = f"app/static/scenes/drama_{drama_id}_scene_{sn}.mp4"
        if not os.path.exists(vid_path) or os.path.getsize(vid_path) < 5000:
            missing.append(sn)

    if missing:
        return JSONResponse({"error": f"シーン {', '.join(map(str, missing))} の動画がありません"}, status_code=400)

    audio_path = f"app/static/audio/drama_{drama_id}.mp3"
    if not os.path.exists(audio_path):
        return JSONResponse({"error": "音声がまだ生成されていません"}, status_code=400)

    task_key = f"editor_{drama_id}"
    if editor_tasks.get(task_key, {}).get("status") == "running":
        return JSONResponse({"error": "Already assembling"}, status_code=409)

    bgm_file = body.get("bgm_file", "")
    bgm_volume = float(body.get("bgm_volume", 0.15))
    include_subtitle = body.get("include_subtitle", True)

    bgm_path = None
    if bgm_file:
        safe_bgm = os.path.basename(bgm_file)
        bgm_path = os.path.join("app/static/bgm", safe_bgm)
        if not os.path.exists(bgm_path):
            bgm_path = None

    editor_tasks[task_key] = {"status": "running", "error": ""}

    def run_editor_assemble():
        try:
            from app.services.video.video_generator import edit_video_custom

            scene_clips = []
            for item in scene_order:
                sn = item["scene_number"]
                ts = float(item.get("trim_start", 0))
                te = float(item.get("trim_end", 0))
                clip_data = {
                    "path": f"app/static/scenes/drama_{drama_id}_scene_{sn}.mp4",
                    "trim_start": ts,
                }
                if te > 0:
                    clip_data["trim_end"] = te
                scene_clips.append(clip_data)

            subtitle_path_val = None
            if include_subtitle:
                sub_path = f"app/static/subtitle/drama_{drama_id}.srt"
                if os.path.exists(sub_path) and os.path.getsize(sub_path) > 10:
                    subtitle_path_val = sub_path

            final_video = edit_video_custom(
                scene_clips=scene_clips,
                audio_path=audio_path,
                drama_id=drama_id,
                subtitle_path=subtitle_path_val,
                bgm_path=bgm_path,
                bgm_volume=bgm_volume
            )
            update_drama(drama_id, video_url=final_video, status="ready")
            editor_tasks[task_key] = {"status": "done", "error": ""}
        except Exception as e:
            logger.error(f"Editor assemble error: {e}")
            editor_tasks[task_key] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=run_editor_assemble, daemon=True)
    thread.start()

    return JSONResponse({"message": "Editor assembly started", "status": "running"})


@router.get("/api/editor/{drama_id}/status")
async def api_editor_status(request: Request, drama_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    task_key = f"editor_{drama_id}"
    task = editor_tasks.get(task_key, {"status": "idle", "error": ""})
    return JSONResponse(task)
