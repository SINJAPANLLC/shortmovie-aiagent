import os
import logging
from app.db.database import (
    create_video, update_video, get_next_video_number,
    get_videos_with_analytics, get_all_videos
)
from app.services.ai.theme_generator import generate_theme
from app.services.ai.story_generator import generate_story
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.video.audio_generator import generate_audio
from app.services.video.video_generator import generate_video
from app.services.youtube.youtube_service import upload_video, is_youtube_connected
from app.services.analytics_collector import collect_all_analytics

logger = logging.getLogger(__name__)

BACKGROUND_IMAGE = "app/static/img/background.png"
THUMBNAIL_IMAGE = "app/static/img/thumbnail.png"

CHANNEL_INTRO = """こんばんは。「3分で眠れるおやすみ物語」へようこそ。
今夜もゆっくりと、穏やかな物語をお届けします。
楽な姿勢で横になって、目を閉じて、ゆっくりと深呼吸をしてみてください。
それでは、今夜のお話を始めましょう。

"""


def _noop_progress(step, message):
    pass


def run_full_pipeline(progress_callback=None, custom_theme=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    logger.info("=== Starting full pipeline ===")
    progress_callback(0, "パイプラインを開始しました")

    try:
        progress_callback(0, "YouTube分析データを収集中...")
        collect_all_analytics()
        progress_callback(0, "YouTube分析データの収集が完了しました")

        existing_videos = get_all_videos()
        previous_themes = [v["theme"] for v in existing_videos if v.get("theme")]

        video_number = get_next_video_number()
        title = f"【睡眠用朗読】3分で眠れるおやすみ物語 #{video_number}"
        video_id = create_video(title=title, theme="", story="", status="generating")
        progress_callback(0, f"動画レコード作成: ID={video_id}, #{video_number}")

        if custom_theme:
            progress_callback(1, "手動テーマが指定されています — AI分析スキップ")
            progress_callback(2, f"手動テーマを使用: 「{custom_theme}」")
            theme = custom_theme
            theme_desc = ""
            update_video(video_id, theme=theme)
        else:
            progress_callback(1, "過去の動画パフォーマンスを分析中...")
            analytics_data = get_videos_with_analytics()
            feedback = None
            if analytics_data:
                improvement = analyze_and_improve(analytics_data, video_id=video_id)
                feedback = improvement.get("next_theme_recommendation", "")
                progress_callback(1, f"AI分析完了: {feedback[:100] if feedback else '推奨なし'}")
            else:
                progress_callback(1, "分析データなし — スキップしました")

            progress_callback(2, "テーマを生成中...")
            theme_data = generate_theme(previous_themes, feedback, video_id=video_id)
            theme = theme_data.get("theme", "静かな夜")
            theme_desc = theme_data.get("description", "")
            update_video(video_id, theme=theme)
            progress_callback(2, f"テーマ決定: 「{theme}」— {theme_desc}")

        progress_callback(3, "ストーリーを生成中...")
        story_text = generate_story(theme, theme_desc, video_id=video_id, progress_callback=progress_callback)
        update_video(video_id, story=story_text)
        progress_callback(3, f"ストーリー生成完了: {len(story_text)}文字")

        full_narration = CHANNEL_INTRO + story_text
        progress_callback(4, "音声を生成中...")
        audio_path = generate_audio(full_narration, video_id, progress_callback=progress_callback)
        update_video(video_id, audio_url=audio_path)
        progress_callback(4, f"音声生成完了: {audio_path}")

        progress_callback(5, "動画を生成中（FFmpeg処理）...")
        if not os.path.exists(BACKGROUND_IMAGE):
            progress_callback(5, "デフォルト背景画像を作成中...")
            _create_default_background()
        video_path = generate_video(audio_path, BACKGROUND_IMAGE, video_id)
        update_video(video_id, video_url=video_path, status="ready")
        progress_callback(5, f"動画生成完了: {video_path}")

        if is_youtube_connected():
            progress_callback(6, "YouTubeにアップロード中...")
            description = (
                f"🌙 {theme}\n\n"
                "この動画は眠るための朗読です。\n"
                "ゆっくり目を閉じてお聞きください。\n\n"
                "#睡眠用 #朗読 #おやすみ物語 #眠れる朗読 #睡眠導入"
            )
            tags = ["睡眠用", "朗読", "おやすみ物語", "眠れる", "睡眠導入",
                    "リラックス", "ASMR", "睡眠用朗読", theme]

            thumbnail = THUMBNAIL_IMAGE if os.path.exists(THUMBNAIL_IMAGE) else None
            youtube_id = upload_video(video_path, title, description, tags, thumbnail)
            update_video(video_id, youtube_id=youtube_id, status="published")
            progress_callback(6, f"YouTube投稿完了: {youtube_id}")
        else:
            update_video(video_id, status="ready")
            progress_callback(6, "YouTube未接続 — ローカル保存のみ")

        logger.info("=== Pipeline complete ===")
        return {"success": True, "video_id": video_id, "title": title, "theme": theme}

    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        if 'video_id' in locals():
            try:
                update_video(video_id, status="error")
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def run_test_pipeline(progress_callback=None, custom_theme=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    logger.info("=== Starting TEST pipeline (short video) ===")
    progress_callback(0, "【テスト】短い動画でパイプラインをテスト中...")

    try:
        progress_callback(0, "YouTube分析データを収集中...")
        collect_all_analytics()
        progress_callback(0, "YouTube分析データの収集が完了しました")

        existing_videos = get_all_videos()
        previous_themes = [v["theme"] for v in existing_videos if v.get("theme")]

        video_number = get_next_video_number()
        title = f"【テスト】おやすみ物語 #{video_number}"
        video_id = create_video(title=title, theme="", story="", status="generating")
        progress_callback(0, f"動画レコード作成: ID={video_id}, #{video_number}")

        if custom_theme:
            progress_callback(1, "手動テーマが指定されています — AI分析スキップ")
            progress_callback(2, f"手動テーマを使用: 「{custom_theme}」")
            theme = custom_theme
            theme_desc = ""
            update_video(video_id, theme=theme)
        else:
            progress_callback(1, "AI分析をスキップ（テストモード）")

            progress_callback(2, "テーマを生成中...")
            theme_data = generate_theme(previous_themes, None, video_id=video_id)
            theme = theme_data.get("theme", "静かな夜")
            theme_desc = theme_data.get("description", "")
            update_video(video_id, theme=theme)
            progress_callback(2, f"テーマ決定: 「{theme}」— {theme_desc}")

        progress_callback(3, "【テスト】短いストーリーを生成中...")
        story_text = _generate_short_story(theme, theme_desc, video_id)
        update_video(video_id, story=story_text)
        progress_callback(3, f"ストーリー生成完了: {len(story_text)}文字")

        full_narration = CHANNEL_INTRO + story_text
        progress_callback(4, "音声を生成中...")
        audio_path = generate_audio(full_narration, video_id, progress_callback=progress_callback)
        update_video(video_id, audio_url=audio_path)
        progress_callback(4, f"音声生成完了: {audio_path}")

        progress_callback(5, "動画を生成中（FFmpeg処理）...")
        if not os.path.exists(BACKGROUND_IMAGE):
            progress_callback(5, "デフォルト背景画像を作成中...")
            _create_default_background()
        video_path = generate_video(audio_path, BACKGROUND_IMAGE, video_id)
        update_video(video_id, video_url=video_path, status="ready")
        progress_callback(5, f"動画生成完了: {video_path}")

        if is_youtube_connected():
            progress_callback(6, "YouTubeにアップロード中...")
            description = (
                f"🌙 {theme}\n\n"
                "この動画は眠るための朗読です。\n"
                "ゆっくり目を閉じてお聞きください。\n\n"
                "#睡眠用 #朗読 #おやすみ物語 #眠れる朗読 #睡眠導入"
            )
            tags = ["睡眠用", "朗読", "おやすみ物語", "眠れる", "睡眠導入",
                    "リラックス", "ASMR", "睡眠用朗読", theme]
            thumbnail = THUMBNAIL_IMAGE if os.path.exists(THUMBNAIL_IMAGE) else None
            youtube_id = upload_video(video_path, title, description, tags, thumbnail)
            update_video(video_id, youtube_id=youtube_id, status="published")
            progress_callback(6, f"YouTube投稿完了: {youtube_id}")
        else:
            update_video(video_id, status="ready")
            progress_callback(6, "YouTube未接続 — ローカル保存のみ")

        logger.info("=== TEST Pipeline complete ===")
        return {"success": True, "video_id": video_id, "title": title, "theme": theme}

    except Exception as e:
        logger.error(f"Test pipeline error: {e}", exc_info=True)
        if 'video_id' in locals():
            try:
                update_video(video_id, status="error")
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def _generate_short_story(theme, theme_desc, video_id):
    import time
    import anthropic
    from app.db.database import save_ai_log

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)
    prompt = f"""あなたは睡眠用朗読動画のストーリー作家です。

テーマ: {theme}
{f'説明: {theme_desc}' if theme_desc else ''}

約300文字の短いテスト用ストーリーを書いてください。
穏やかで眠くなるような内容にしてください。
ストーリー本文のみを出力してください。"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if attempt < 2:
                delay = 30 * (attempt + 1)
                logger.warning(f"Claude API error on test story, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                raise

    response_text = message.content[0].text
    if video_id:
        save_ai_log(video_id, "テストストーリー生成", prompt, response_text)
    return response_text


def generate_theme_only():
    existing_videos = get_all_videos()
    previous_themes = [v["theme"] for v in existing_videos if v.get("theme")]
    return generate_theme(previous_themes)


def generate_story_only(theme: str, theme_description: str = ""):
    return generate_story(theme, theme_description)


def _create_default_background():
    os.makedirs(os.path.dirname(BACKGROUND_IMAGE), exist_ok=True)
    try:
        import subprocess
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x0a0a2e:s=1920x1080:d=1",
            "-frames:v", "1",
            BACKGROUND_IMAGE
        ], capture_output=True, check=True)
        logger.info("Default background image created")
    except Exception as e:
        logger.error(f"Failed to create background: {e}")
        raise
