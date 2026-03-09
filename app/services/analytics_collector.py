import logging
from app.db.database import get_published_videos, update_video
from app.services.youtube.youtube_service import get_video_analytics, is_youtube_connected

logger = logging.getLogger(__name__)


def collect_all_analytics():
    if not is_youtube_connected():
        logger.info("YouTube not connected, skipping analytics collection")
        return

    videos = get_published_videos()
    if not videos:
        logger.info("No published videos to collect analytics for")
        return

    logger.info(f"Collecting analytics for {len(videos)} videos...")
    updated = 0

    for video in videos:
        youtube_id = video.get("youtube_id")
        if not youtube_id:
            continue

        try:
            stats = get_video_analytics(youtube_id)
            if stats:
                update_video(
                    video["id"],
                    views=stats.get("views", 0),
                )
                updated += 1
                logger.info(f"Updated analytics for video {video['id']} (YT: {youtube_id}): views={stats.get('views', 0)}")
        except Exception as e:
            logger.error(f"Failed to collect analytics for video {video['id']}: {e}")

    logger.info(f"Analytics collection complete: {updated}/{len(videos)} videos updated")
