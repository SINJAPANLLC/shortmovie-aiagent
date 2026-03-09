import logging
from app.db.database import get_published_dramas, update_drama
from app.services.youtube.youtube_service import get_video_analytics, is_youtube_connected

logger = logging.getLogger(__name__)


def collect_all_analytics():
    if not is_youtube_connected():
        logger.info("YouTube not connected, skipping analytics collection")
        return

    dramas = get_published_dramas()
    if not dramas:
        logger.info("No published dramas to collect analytics for")
        return

    logger.info(f"Collecting analytics for {len(dramas)} dramas...")
    updated = 0

    for drama in dramas:
        youtube_id = drama.get("youtube_id")
        if not youtube_id:
            continue

        try:
            stats = get_video_analytics(youtube_id)
            if stats:
                update_drama(
                    drama["id"],
                    views=stats.get("views", 0),
                    likes=stats.get("likes", 0),
                )
                updated += 1
                logger.info(f"Updated analytics for drama {drama['id']} (YT: {youtube_id}): views={stats.get('views', 0)}")
        except Exception as e:
            logger.error(f"Failed to collect analytics for drama {drama['id']}: {e}")

    logger.info(f"Analytics collection complete: {updated}/{len(dramas)} dramas updated")
