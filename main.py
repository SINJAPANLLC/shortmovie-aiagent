import os
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.database import init_db, create_admin_user
from app.api.auth import hash_password
from app.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="CEOの扉 - AI Short Drama Generator")

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

    try:
        from app.services.automation import init_scheduler
        init_scheduler()
        logger.info("Automation scheduler initialized")
    except Exception as e:
        logger.error(f"Scheduler init error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
