import os
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.database import init_db, create_admin_user
from app.api.auth import hash_password
from app.api.routes import router
from app.services.pipeline import run_full_pipeline
from app.services.analytics_collector import collect_all_analytics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Short Movie AI AGENT SIN JAPAN")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(router)


@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database...")
    init_db()

    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    password_hash = hash_password(admin_password)
    create_admin_user(admin_username, password_hash)
    logger.info(f"Admin user '{admin_username}' ensured")

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        run_full_pipeline,
        trigger=CronTrigger(hour=21, minute=0),
        id="daily_video_pipeline",
        name="Daily Video Generation Pipeline",
        replace_existing=True
    )

    scheduler.add_job(
        collect_all_analytics,
        trigger=CronTrigger(hour=9, minute=0),
        id="morning_analytics",
        name="Morning Analytics Collection",
        replace_existing=True
    )

    scheduler.add_job(
        collect_all_analytics,
        trigger=CronTrigger(hour=15, minute=0),
        id="afternoon_analytics",
        name="Afternoon Analytics Collection",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started - pipeline at 21:00, analytics at 09:00 & 15:00")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
