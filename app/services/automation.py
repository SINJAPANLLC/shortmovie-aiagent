import os
import logging
import threading
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.db.database import get_connection

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Tokyo")
_current_jobs = {}
_running_status = {}


def init_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started")
    reload_schedules()


def reload_schedules():
    for job_id in list(_current_jobs.keys()):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    _current_jobs.clear()

    schedules = get_all_schedules()
    for s in schedules:
        if s["enabled"]:
            _add_job(s)
    logger.info(f"Loaded {len(schedules)} automation schedules")


def _add_job(schedule):
    job_id = f"auto_{schedule['id']}"
    time_parts = schedule["schedule_time"].split(":")
    hour = int(time_parts[0])
    minute = int(time_parts[1]) if len(time_parts) > 1 else 0

    days = schedule.get("days_of_week", "mon,tue,wed,thu,fri,sat,sun")
    day_map = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
    day_list = [d.strip() for d in days.split(",") if d.strip() in day_map]
    if not day_list:
        day_list = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    trigger = CronTrigger(
        hour=hour,
        minute=minute,
        day_of_week=",".join(day_list),
        timezone="Asia/Tokyo"
    )

    scheduler.add_job(
        run_automation_job,
        trigger=trigger,
        id=job_id,
        args=[schedule["id"]],
        replace_existing=True,
        misfire_grace_time=3600
    )
    _current_jobs[job_id] = schedule["id"]
    logger.info(f"Scheduled job {job_id} at {schedule['schedule_time']} ({days})")


def run_automation_job(schedule_id):
    schedule = get_schedule_by_id(schedule_id)
    if not schedule or not schedule["enabled"]:
        return

    log_id = create_automation_log(schedule_id)
    _running_status[schedule_id] = {
        "log_id": log_id,
        "status": "running",
        "step": "開始",
        "message": "自動生成を開始しています...",
        "progress": 0,
        "started_at": datetime.now().isoformat()
    }

    try:
        def progress_cb(step, message):
            _running_status[schedule_id].update({
                "step": str(step),
                "message": message,
                "progress": min(step * 10, 90)
            })
            update_automation_log(log_id, step=str(step), message=message)

        from app.services.pipeline import run_full_pipeline
        result = run_full_pipeline(
            progress_callback=progress_cb,
            custom_theme=schedule.get("custom_theme") or None,
            max_scenes=schedule.get("max_scenes") or None
        )

        drama_id = result.get("drama_id") if result else None
        youtube_id = None

        if schedule.get("auto_upload_youtube") and drama_id:
            try:
                progress_cb(9, "YouTube Shortsにアップロード中...")
                from app.services.youtube.youtube_service import is_youtube_connected
                if is_youtube_connected():
                    from app.db.database import get_drama_by_id
                    drama = get_drama_by_id(drama_id)
                    if drama and drama.get("video_url"):
                        video_path = drama["video_url"]
                        if video_path.startswith("/static/"):
                            video_path = "app" + video_path
                        if os.path.exists(video_path):
                            from app.services.youtube.youtube_service import upload_video
                            title = drama.get("title", "CEOの扉") + " #Shorts"
                            youtube_id = upload_video(
                                video_path=video_path,
                                title=title,
                                description=f"{drama.get('theme', '')}\n\n#Shorts #CEOドラマ",
                                tags=["Shorts", "CEOドラマ", "AIドラマ", "ショートドラマ"],
                                privacy_status=schedule.get("youtube_privacy", "public")
                            )
                            from app.db.database import update_drama
                            update_drama(drama_id, youtube_id=youtube_id)
                            progress_cb(9, f"YouTube投稿完了: {youtube_id}")
            except Exception as e:
                logger.error(f"Auto YouTube upload failed: {e}")
                progress_cb(9, f"YouTube投稿エラー: {str(e)[:100]}")

        _running_status[schedule_id].update({
            "status": "completed",
            "step": "完了",
            "message": "自動生成が完了しました",
            "progress": 100
        })
        update_automation_log(
            log_id, status="completed", step="完了",
            message="自動生成完了", drama_id=drama_id, youtube_id=youtube_id
        )

    except Exception as e:
        logger.error(f"Automation job {schedule_id} failed: {e}")
        import traceback
        traceback.print_exc()
        _running_status[schedule_id].update({
            "status": "failed",
            "step": "エラー",
            "message": str(e)[:300],
            "progress": 0
        })
        update_automation_log(log_id, status="failed", error=str(e)[:500])


def get_running_status(schedule_id=None):
    if schedule_id:
        return _running_status.get(schedule_id)
    return dict(_running_status)


def run_job_now(schedule_id):
    t = threading.Thread(target=run_automation_job, args=[schedule_id], daemon=True)
    t.start()
    return True


def get_all_schedules():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM automation_schedules ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_schedule_by_id(schedule_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM automation_schedules WHERE id = %s", (schedule_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_schedule(name, schedule_time, days_of_week="mon,tue,wed,thu,fri,sat,sun",
                    pipeline_mode="full", auto_upload_youtube=False, auto_upload_tiktok=False,
                    youtube_privacy="public", custom_theme=None, max_scenes=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO automation_schedules
        (name, schedule_time, days_of_week, pipeline_mode, auto_upload_youtube,
         auto_upload_tiktok, youtube_privacy, custom_theme, max_scenes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (name, schedule_time, days_of_week, pipeline_mode, auto_upload_youtube,
          auto_upload_tiktok, youtube_privacy, custom_theme, max_scenes))
    sid = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    reload_schedules()
    return sid


def update_schedule(schedule_id, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    allowed = ["name", "enabled", "schedule_time", "days_of_week", "pipeline_mode",
               "auto_upload_youtube", "auto_upload_tiktok", "youtube_privacy",
               "custom_theme", "max_scenes"]
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = %s")
            vals.append(v)
    if sets:
        vals.append(schedule_id)
        cur.execute(f"UPDATE automation_schedules SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
    cur.close()
    conn.close()
    reload_schedules()


def delete_schedule(schedule_id):
    job_id = f"auto_{schedule_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    _current_jobs.pop(job_id, None)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM automation_schedules WHERE id = %s", (schedule_id,))
    cur.execute("DELETE FROM automation_logs WHERE schedule_id = %s", (schedule_id,))
    conn.commit()
    cur.close()
    conn.close()


def create_automation_log(schedule_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO automation_logs (schedule_id, status, step, message)
        VALUES (%s, 'running', '開始', '自動生成を開始')
        RETURNING id
    """, (schedule_id,))
    lid = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return lid


def update_automation_log(log_id, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    allowed = ["status", "step", "message", "drama_id", "youtube_id", "error"]
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = %s")
            vals.append(v)
    if kwargs.get("status") in ("completed", "failed"):
        sets.append("finished_at = NOW()")
    if sets:
        vals.append(log_id)
        cur.execute(f"UPDATE automation_logs SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
    cur.close()
    conn.close()


def get_automation_logs(limit=20, schedule_id=None):
    conn = get_connection()
    cur = conn.cursor()
    if schedule_id:
        cur.execute("""
            SELECT al.*, asm.name as schedule_name
            FROM automation_logs al
            LEFT JOIN automation_schedules asm ON al.schedule_id = asm.id
            WHERE al.schedule_id = %s
            ORDER BY al.started_at DESC LIMIT %s
        """, (schedule_id, limit))
    else:
        cur.execute("""
            SELECT al.*, asm.name as schedule_name
            FROM automation_logs al
            LEFT JOIN automation_schedules asm ON al.schedule_id = asm.id
            ORDER BY al.started_at DESC LIMIT %s
        """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]
