import os
import logging
from app.db.database import (
    create_drama, update_drama, get_next_episode_number,
    get_dramas_with_analytics, get_all_dramas
)
from app.services.ai.theme_generator import generate_theme
from app.services.ai.story_generator import generate_script
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.video.audio_generator import generate_voice
from app.services.video.video_generator import edit_video, create_placeholder_video
from app.services.video.kling_service import generate_scene_video
from app.services.youtube.youtube_service import upload_video, is_youtube_connected
from app.services.analytics_collector import collect_all_analytics

logger = logging.getLogger(__name__)

CHARACTER_IMAGE = "app/static/characters/main_character.png"
THUMBNAIL_DIR = "app/static/thumbnail"


def _noop_progress(step, message):
    pass


def run_full_pipeline(progress_callback=None, custom_theme=None, custom_genre=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    logger.info("=== Starting AI Short Drama Pipeline ===")
    progress_callback(0, "パイプラインを開始しました")

    try:
        progress_callback(1, "テーマを生成中...")
        existing_dramas = get_all_dramas()
        previous_themes = [d["theme"] for d in existing_dramas if d.get("theme")]

        if custom_theme:
            theme_data = {
                "theme": custom_theme,
                "title_base": custom_theme,
                "hook": "",
                "twist": "",
                "genre": custom_genre or "恋愛"
            }
            progress_callback(1, f"手動テーマ: 「{custom_theme}」")
        else:
            theme_data = generate_theme(previous_themes, genre=custom_genre)
            progress_callback(1, f"テーマ決定: 「{theme_data.get('title_base', '')}」({theme_data.get('genre', '')})")

        genre = theme_data.get("genre", "恋愛")
        episode_num = get_next_episode_number(genre)
        title = f"{theme_data.get('title_base', 'ドラマ')} 第{episode_num}話"

        drama_id = create_drama(
            title=title,
            genre=genre,
            theme=theme_data.get("theme", ""),
            status="generating",
            episode_number=episode_num
        )
        progress_callback(1, f"ドラマ作成: ID={drama_id}, 「{title}」")

        progress_callback(2, "脚本を生成中...")
        script_data = generate_script(
            theme=theme_data.get("theme", ""),
            genre=genre,
            hook=theme_data.get("hook", ""),
            twist=theme_data.get("twist", ""),
            drama_id=drama_id,
            progress_callback=progress_callback
        )
        narration = script_data.get("narration", "")
        scenes = script_data.get("scenes", [])
        update_drama(drama_id, script=narration, scene_count=len(scenes))
        progress_callback(2, f"脚本生成完了: {len(narration)}文字, {len(scenes)}シーン")

        progress_callback(3, "シーン分割完了")
        for i, scene in enumerate(scenes):
            progress_callback(3, f"  シーン{scene.get('scene_number', i+1)}: {scene.get('narration', '')[:30]}...")

        progress_callback(4, "動画シーンを生成中...")
        scene_videos = []
        for i, scene in enumerate(scenes):
            progress_callback(4, f"シーン{i+1}/{len(scenes)}を生成中...")
            scene_path = generate_scene_video(
                scene_description=scene.get("description", ""),
                scene_number=scene.get("scene_number", i+1),
                drama_id=drama_id,
                reference_image=CHARACTER_IMAGE if os.path.exists(CHARACTER_IMAGE) else None,
                progress_callback=progress_callback
            )
            scene_videos.append(scene_path)
            progress_callback(4, f"シーン{i+1}/{len(scenes)}生成完了")

        progress_callback(5, "音声を生成中...")
        audio_path = generate_voice(narration, drama_id, progress_callback=progress_callback)
        progress_callback(5, f"音声生成完了: {audio_path}")

        progress_callback(6, "動画を編集中（FFmpeg）...")
        final_video = edit_video(scene_videos, audio_path, drama_id)
        update_drama(drama_id, video_url=final_video, status="ready")
        progress_callback(6, f"動画編集完了: {final_video}")

        if is_youtube_connected():
            progress_callback(7, "YouTubeにアップロード中...")
            description = (
                f"【AIショートドラマ】\n\n"
                f"{title}\n\n"
                "続きはフォローして待っててください\n\n"
                "#ショートドラマ #AIドラマ #恋愛ドラマ #Shorts"
            )
            thumbnail_path = os.path.join(THUMBNAIL_DIR, f"drama_{drama_id}.png")
            youtube_id = upload_video(
                final_video,
                title=title,
                description=description,
                tags=["ショートドラマ", "AIドラマ", genre, "Shorts"],
                thumbnail_path=thumbnail_path if os.path.exists(thumbnail_path) else None
            )
            update_drama(drama_id, youtube_id=youtube_id, status="published")
            progress_callback(7, f"YouTube投稿完了: {youtube_id}")
        else:
            progress_callback(7, "YouTube未接続 — スキップ")

        progress_callback(8, "パイプライン完了!")
        return {"success": True, "drama_id": drama_id, "title": title}

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        progress_callback(8, f"エラー: {str(e)}")
        return {"success": False, "error": str(e)}


def generate_theme_only(genre=None):
    existing_dramas = get_all_dramas()
    previous_themes = [d["theme"] for d in existing_dramas if d.get("theme")]
    return generate_theme(previous_themes, genre=genre)
