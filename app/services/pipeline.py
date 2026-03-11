import os
import logging
from app.db.database import (
    create_drama, update_drama, get_next_episode_number,
    get_dramas_with_analytics, get_all_dramas,
    get_active_series, create_series, update_series, get_next_series_number, get_all_series
)
from app.services.ai.theme_generator import generate_theme, generate_series_theme
from app.services.ai.story_generator import generate_script
from app.services.ai.improvement_ai import analyze_and_improve
from app.services.video.image_generator import generate_character_image, generate_thumbnail
from app.services.video.audio_generator import generate_voice
from app.services.video.video_generator import edit_video, create_placeholder_video
from app.services.video.scene_generator import generate_scene_video
from app.services.video.subtitle_generator import generate_subtitle
from app.services.youtube.youtube_service import upload_video, is_youtube_connected
from app.services.tiktok.tiktok_rpa import upload_to_tiktok_rpa_sync, is_tiktok_rpa_connected
from app.services.analytics_collector import collect_all_analytics

logger = logging.getLogger(__name__)

CHANNEL_NAME = "CEOの扉"
EPISODES_PER_SERIES = 30


def _resolve_image_path(path):
    if not path:
        return None
    if os.path.exists(path):
        return path
    if path.startswith("/static/"):
        alt = "app" + path
        if os.path.exists(alt):
            return alt
    if path.startswith("app/static/"):
        alt = path.replace("app/static/", "/static/", 1)
    return None


def _pick_character_image_for_scene(character, scene_description=""):
    desc_lower = scene_description.lower()
    face_keywords = ["クローズアップ", "表情", "顔", "目", "涙", "close-up", "closeup", "face", "eyes"]
    fullbody_keywords = ["全身", "歩く", "走る", "立つ", "振り返", "エレベーター", "ロビー", "full body", "walking", "standing", "elevator", "lobby"]

    if any(kw in desc_lower for kw in face_keywords):
        resolved = _resolve_image_path(character.get("image_face", ""))
        if resolved:
            return resolved

    if any(kw in desc_lower for kw in fullbody_keywords):
        resolved = _resolve_image_path(character.get("image_fullbody", ""))
        if resolved:
            return resolved

    for key in ["image_bust", "image_path", "image_face", "image_fullbody"]:
        resolved = _resolve_image_path(character.get(key, ""))
        if resolved:
            return resolved

    return None


def _noop_progress(step, message):
    pass


def _ensure_active_series(progress_callback=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    series = get_active_series()
    if series and series["current_episode"] < series["total_episodes"]:
        return series

    if series and series["current_episode"] >= series["total_episodes"]:
        update_series(series["id"], status="completed")
        progress_callback(1, f"シリーズ「{series['name']}」完結（{series['total_episodes']}話）")

    series_number = get_next_series_number()
    progress_callback(1, f"新シリーズ{series_number}を企画中...")

    all_series = get_all_series()
    previous_names = [s["name"] for s in all_series]

    series_data = generate_series_theme(series_number, previous_series=previous_names)

    series_id = create_series(
        series_number=series_number,
        name=series_data.get("series_name", f"シリーズ{series_number}"),
        description=series_data.get("series_description", ""),
        synopsis=series_data.get("synopsis", ""),
        total_episodes=EPISODES_PER_SERIES
    )
    progress_callback(1, f"新シリーズ作成: 「{series_data.get('series_name', '')}」")

    return {
        "id": series_id,
        "series_number": series_number,
        "name": series_data.get("series_name", f"シリーズ{series_number}"),
        "description": series_data.get("series_description", ""),
        "synopsis": series_data.get("synopsis", ""),
        "total_episodes": EPISODES_PER_SERIES,
        "current_episode": 0,
        "status": "active"
    }


def run_full_pipeline(progress_callback=None, custom_theme=None, custom_genre=None, max_scenes=None, target_episode=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    logger.info(f"=== Starting {CHANNEL_NAME} Pipeline ===")
    progress_callback(0, "パイプラインを開始しました")

    try:
        progress_callback(1, "シリーズ・テーマを確認中...")
        series = _ensure_active_series(progress_callback)

        if target_episode and 1 <= target_episode <= series["total_episodes"]:
            series_episode = target_episode
            progress_callback(1, f"手動指定: 第{series_episode}話を生成")
        else:
            series_episode = series["current_episode"] + 1

        existing_dramas = get_all_dramas()
        series_dramas = sorted(
            [d for d in existing_dramas if d.get("series_id") == series["id"]],
            key=lambda x: x.get("series_episode", 0)
        )
        previous_themes = [d["theme"] for d in series_dramas if d.get("theme")]
        previous_scripts = [d["script"] for d in series_dramas if d.get("script")]

        if custom_theme:
            theme_data = {
                "theme": custom_theme,
                "title_base": custom_theme,
                "hook": "",
                "twist": "",
                "genre": "CEOドラマ",
                "emotional_arc": ""
            }
            progress_callback(1, f"手動テーマ: 「{custom_theme}」")
        else:
            previous_episodes_data = [
                {"episode": d.get("series_episode", 0), "title": d.get("title", ""), "theme": d.get("theme", "")}
                for d in series_dramas
            ]
            theme_data = generate_theme(
                previous_themes, genre="CEOドラマ", series_info=series,
                previous_scripts=previous_scripts,
                previous_episodes=previous_episodes_data
            )
            progress_callback(1, f"テーマ決定: 「{theme_data.get('title_base', '')}」")

        genre = "CEOドラマ"
        episode_num = get_next_episode_number(genre)

        series_name = series.get("name", "")
        title = f"CEOの扉 | {series_name} 第{series_episode}話「{theme_data.get('title_base', '')}」"

        drama_id = create_drama(
            title=title,
            genre=genre,
            theme=theme_data.get("theme", ""),
            status="generating",
            episode_number=episode_num,
            series_id=series["id"],
            series_episode=series_episode
        )
        progress_callback(1, f"第{series_episode}話 作成開始: 「{title}」")

        progress_callback(2, "脚本を生成中...")
        last_script = previous_scripts[-1] if previous_scripts else None
        script_data = generate_script(
            theme=theme_data.get("theme", ""),
            genre=genre,
            hook=theme_data.get("hook", ""),
            twist=theme_data.get("twist", ""),
            drama_id=drama_id,
            progress_callback=progress_callback,
            series_info=series,
            previous_script=last_script,
            emotional_arc=theme_data.get("emotional_arc", "")
        )
        import json as json_mod
        narration = script_data.get("narration", "")
        scenes = script_data.get("scenes", [])
        if max_scenes and len(scenes) > max_scenes:
            scenes = scenes[:max_scenes]
            script_data["scenes"] = scenes
            scene_narrations = [s.get("narration", "") for s in scenes]
            narration = "".join(scene_narrations)
            script_data["narration"] = narration
            progress_callback(2, f"テストモード: {max_scenes}シーンに制限")
        update_drama(drama_id, script=json_mod.dumps(script_data, ensure_ascii=False), scene_count=len(scenes))
        progress_callback(2, f"脚本生成完了: {len(narration)}文字, {len(scenes)}シーン")

        progress_callback(3, "キャラクター・サムネイル画像を生成中 (Stable Diffusion)...")

        characters_list = []
        try:
            from app.db.database import get_characters_by_series
            characters_list = get_characters_by_series(series["id"]) if series else []
        except Exception:
            pass

        protagonist = None
        any_char = None
        for c in characters_list:
            has_img = any(_resolve_image_path(c.get(k, "")) for k in ["image_path", "image_face", "image_bust", "image_fullbody"])
            if has_img:
                any_char = c
                if c.get("role") == "主人公":
                    protagonist = c
                    break
        main_character = protagonist or any_char

        if main_character:
            character_image = _pick_character_image_for_scene(main_character) or ""
            progress_callback(3, f"キャラクター「{main_character['name']}」の画像を使用（複数バリエーション対応）")
        else:
            series_character = series.get("character_image", "")
            if series_character and _resolve_image_path(series_character):
                character_image = series_character
                progress_callback(3, f"シリーズ固定キャラクター画像を使用: {os.path.basename(character_image)}")
            else:
                first_scene_desc = scenes[0].get("description", "") if scenes else ""
                character_desc = (
                    f"beautiful Japanese woman, 26 years old, modern professional look, "
                    f"emotional cinematic portrait, luxury office background with city skyline, "
                    f"dramatic lighting, shallow depth of field, vertical 9:16 composition, "
                    f"photorealistic, film grain, {first_scene_desc[:100]}"
                )
                character_image = generate_character_image(
                    character_description=character_desc,
                    drama_id=drama_id,
                    progress_callback=progress_callback
                )
                import shutil
                series_char_path = f"app/static/characters/series_{series['id']}_character.png"
                shutil.copy2(character_image, series_char_path)
                update_series(series["id"], character_image=series_char_path)
                series["character_image"] = series_char_path
                progress_callback(3, f"キャラクター画像を生成 → シリーズ固定化")

        thumbnail_path = generate_thumbnail(
            title=title,
            genre=genre,
            drama_id=drama_id,
            character_image=character_image,
            progress_callback=progress_callback
        )
        update_drama(drama_id, thumbnail_url=thumbnail_path)
        progress_callback(3, "画像生成完了（キャラクター＋サムネイル）")

        progress_callback(4, "シーン分割...")
        for i, scene in enumerate(scenes):
            progress_callback(4, f"  シーン{scene.get('scene_number', i+1)}: {scene.get('narration', '')[:30]}...")

        char_by_name = {}
        for c in characters_list:
            char_by_name[c.get("name", "")] = c

        progress_callback(5, "動画シーンを生成中 (Kling AI V3 / Luma)...")
        scene_videos = []
        for i, scene in enumerate(scenes):
            progress_callback(5, f"シーン{i+1}/{len(scenes)}を生成中...")
            scene_desc = scene.get("description", "")
            scene_chars = scene.get("characters", [])

            ref_image = None
            if scene_chars and char_by_name:
                for cname in scene_chars:
                    matched_char = char_by_name.get(cname)
                    if matched_char:
                        ref_image = _pick_character_image_for_scene(matched_char, scene_desc)
                        if ref_image:
                            progress_callback(5, f"  キャラ「{cname}」の画像を参照")
                            break
            if not ref_image and main_character:
                ref_image = _pick_character_image_for_scene(main_character, scene_desc)
            if not ref_image and character_image and os.path.exists(character_image):
                ref_image = character_image

            scene_emotion = scene.get("emotion", "")
            scene_duration = float(scene.get("duration", 15))
            scene_narration = scene.get("narration", "")
            video_prompt = scene.get("video_prompt", "")
            image_prompt = scene.get("image_prompt", "")
            scene_path = generate_scene_video(
                scene_description=video_prompt or scene_desc,
                scene_number=scene.get("scene_number", i+1),
                drama_id=drama_id,
                reference_image=ref_image,
                progress_callback=progress_callback,
                emotion=scene_emotion,
                duration=scene_duration,
                narration=scene_narration,
                image_prompt=image_prompt or scene_desc
            )
            scene_videos.append(scene_path)
            progress_callback(5, f"シーン{i+1}/{len(scenes)}生成完了")

        progress_callback(6, "音声を生成中 (ElevenLabs マルチボイス)...")
        audio_path = generate_voice(narration, drama_id, progress_callback=progress_callback, scenes=scenes)
        progress_callback(6, f"音声生成完了")

        progress_callback(7, "字幕・動画を編集中（FFmpeg）...")
        subtitle_path = generate_subtitle(scenes, drama_id, progress_callback=progress_callback)
        final_video = edit_video(scene_videos, audio_path, drama_id, subtitle_path=subtitle_path)
        update_drama(drama_id, video_url=final_video, status="ready")
        progress_callback(7, f"動画編集完了（字幕付き）")

        youtube_id = None
        tiktok_id = None

        subtitle = theme_data.get('title_base', '')
        theme_summary = theme_data.get('theme', '')[:80]

        description = (
            f"【CEOの扉】{series_name} 第{series_episode}話「{subtitle}」\n\n"
            f"{theme_summary}\n\n"
            f"45秒で描く、運命のCEOドラマ。\n"
            f"全{series.get('total_episodes', 30)}話のシリーズ、毎日更新中。\n\n"
            f"次の話が気になる人はフォロー!\n"
            f"コメントで展開を予想してね\n\n"
            f"#CEOの扉 #ショートドラマ #社長ドラマ #恋愛 #CEOドラマ "
            f"#胸キュン #ドラマチック #Shorts #TikTok"
        )
        tags = [
            "CEOの扉", "ショートドラマ", "社長ドラマ", "恋愛ドラマ",
            "CEOドラマ", "胸キュン", "ドラマチック", "恋愛",
            "Shorts", "TikTok", series_name, subtitle
        ]

        if is_youtube_connected():
            progress_callback(8, "YouTubeにアップロード中...")
            youtube_id = upload_video(
                final_video,
                title=title,
                description=description,
                tags=tags,
                thumbnail_path=thumbnail_path if os.path.exists(thumbnail_path) else None
            )
            progress_callback(8, f"YouTube投稿完了: {youtube_id}")
        else:
            progress_callback(8, "YouTube未接続 — スキップ")

        if is_tiktok_rpa_connected():
            progress_callback(8, "TikTokにRPAアップロード中...")
            tiktok_id = upload_to_tiktok_rpa_sync(
                video_path=final_video,
                title=title,
                description=description,
                tags=tags
            )
            if tiktok_id:
                progress_callback(8, f"TikTok RPA投稿完了: {tiktok_id}")
            else:
                progress_callback(8, "TikTok RPA投稿失敗")
        else:
            progress_callback(8, "TikTok未接続 — スキップ")

        status = "published" if (youtube_id or tiktok_id) else "ready"
        update_data = {"status": status}
        if youtube_id:
            update_data["youtube_id"] = youtube_id
        if tiktok_id:
            update_data["tiktok_id"] = tiktok_id
        update_drama(drama_id, **update_data)

        update_series(series["id"], current_episode=series_episode)

        progress_callback(9, "パイプライン完了!")
        return {"success": True, "drama_id": drama_id, "title": title}

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        progress_callback(9, f"エラー: {str(e)}")
        return {"success": False, "error": str(e)}


def generate_theme_only(genre=None):
    existing_dramas = get_all_dramas()
    previous_themes = [d["theme"] for d in existing_dramas if d.get("theme")]
    return generate_theme(previous_themes, genre=genre)


def generate_script_only(progress_callback=None, custom_theme=None, custom_genre=None, target_episode=None, characters=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    progress_callback(0, "脚本生成を開始...")
    series = _ensure_active_series(progress_callback)

    if target_episode and 1 <= target_episode <= series["total_episodes"]:
        series_episode = target_episode
        progress_callback(1, f"手動指定: 第{series_episode}話を生成")
    else:
        series_episode = series["current_episode"] + 1

    existing_dramas = get_all_dramas()
    series_dramas = sorted(
        [d for d in existing_dramas if d.get("series_id") == series["id"]],
        key=lambda x: x.get("series_episode", 0)
    )
    previous_themes = [d["theme"] for d in series_dramas if d.get("theme")]
    previous_scripts = [d["script"] for d in series_dramas if d.get("script")]

    if custom_theme:
        theme_data = {
            "theme": custom_theme,
            "title_base": custom_theme,
            "hook": "",
            "twist": "",
            "genre": "CEOドラマ",
            "emotional_arc": ""
        }
        progress_callback(1, f"手動テーマ: 「{custom_theme}」")
    else:
        previous_episodes_data = [
            {"episode": d.get("series_episode", 0), "title": d.get("title", ""), "theme": d.get("theme", "")}
            for d in series_dramas
        ]
        theme_data = generate_theme(
            previous_themes, genre="CEOドラマ", series_info=series,
            previous_scripts=previous_scripts,
            previous_episodes=previous_episodes_data
        )
        progress_callback(1, f"テーマ決定: 「{theme_data.get('title_base', '')}」")

    genre = "CEOドラマ"
    episode_num = get_next_episode_number(genre)
    series_name = series.get("name", "")
    title = f"CEOの扉 | {series_name} 第{series_episode}話「{theme_data.get('title_base', '')}」"

    drama_id = create_drama(
        title=title,
        genre=genre,
        theme=theme_data.get("theme", ""),
        status="script_ready",
        episode_number=episode_num,
        series_id=series["id"],
        series_episode=series_episode
    )
    progress_callback(1, f"第{series_episode}話 作成: 「{title}」")

    progress_callback(2, "脚本を生成中...")
    last_script = previous_scripts[-1] if previous_scripts else None

    characters_context = ""
    if characters:
        char_lines = []
        for c in characters:
            char_lines.append(f"- {c['name']}（{c['role']}）: {c.get('description', '')}")
        characters_context = "\n【登場キャラクター】\n" + "\n".join(char_lines)

    script_data = generate_script(
        theme=theme_data.get("theme", ""),
        genre=genre,
        hook=theme_data.get("hook", ""),
        twist=theme_data.get("twist", ""),
        drama_id=drama_id,
        progress_callback=progress_callback,
        series_info=series,
        previous_script=last_script,
        emotional_arc=theme_data.get("emotional_arc", ""),
        characters_context=characters_context
    )

    narration = script_data.get("narration", "")
    scenes = script_data.get("scenes", [])

    import json
    update_drama(drama_id, script=json.dumps(script_data, ensure_ascii=False), scene_count=len(scenes), status="script_ready")
    progress_callback(2, f"脚本生成完了: {len(narration)}文字, {len(scenes)}シーン")

    return {
        "success": True,
        "drama_id": drama_id,
        "title": title,
        "script_data": script_data,
        "theme_data": theme_data,
        "series": series,
        "series_episode": series_episode
    }


def continue_pipeline_from_script(drama_id, progress_callback=None, max_scenes=None):
    if progress_callback is None:
        progress_callback = _noop_progress

    import json

    drama = None
    from app.db.database import get_drama_by_id
    drama = get_drama_by_id(drama_id)
    if not drama:
        raise RuntimeError(f"Drama {drama_id} not found")

    try:
        script_data = json.loads(drama["script"])
    except (json.JSONDecodeError, TypeError):
        script_data = {
            "narration": drama.get("script", ""),
            "scenes": []
        }

    narration = script_data.get("narration", "")
    scenes = script_data.get("scenes", [])
    if max_scenes and len(scenes) > max_scenes:
        scenes = scenes[:max_scenes]
        scene_narrations = [s.get("narration", "") for s in scenes]
        narration = "".join(scene_narrations)
        progress_callback(2, f"テストモード: {max_scenes}シーンに制限")

    update_drama(drama_id, scene_count=len(scenes), status="generating")
    progress_callback(2, f"脚本確認: {len(narration)}文字, {len(scenes)}シーン")

    series_id = drama.get("series_id")
    series = None
    if series_id:
        from app.db.database import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM series WHERE id = %s", (series_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            series = dict(row)
    if not series:
        series = get_active_series()
    if not series:
        series = {"id": series_id or 0, "name": "", "total_episodes": 30, "current_episode": 0}

    series_episode = drama.get("series_episode", 1)
    series_name = series.get("name", "")
    title = drama.get("title", "")

    progress_callback(3, "キャラクター・サムネイル画像を生成中 (Stable Diffusion)...")

    characters_list = []
    try:
        from app.db.database import get_characters_by_series
        characters_list = get_characters_by_series(series_id) if series_id else []
    except Exception:
        pass

    protagonist = None
    any_character = None
    for c in characters_list:
        has_any_image = any(_resolve_image_path(c.get(k, "")) for k in ["image_path", "image_face", "image_bust", "image_fullbody"])
        if has_any_image:
            any_character = c
            if c.get("role") == "主人公":
                protagonist = c
                break

    main_character = protagonist or any_character

    if main_character:
        character_image = _pick_character_image_for_scene(main_character) or ""
        progress_callback(3, f"キャラクター「{main_character['name']}」の画像を使用（複数バリエーション対応）")
    else:
        series_character = series.get("character_image", "")
        if series_character and (_resolve_image_path(series_character)):
            character_image = series_character
            progress_callback(3, f"シリーズ固定キャラクター画像を使用")
        else:
            first_scene_desc = scenes[0].get("description", "") if scenes else ""
            character_desc = (
                f"beautiful Japanese woman, 26 years old, modern professional look, "
                f"emotional cinematic portrait, luxury office background with city skyline, "
                f"dramatic lighting, shallow depth of field, vertical 9:16 composition, "
                f"photorealistic, film grain, {first_scene_desc[:100]}"
            )
            character_image = generate_character_image(
                character_description=character_desc,
                drama_id=drama_id,
                progress_callback=progress_callback
            )
            import shutil
            series_char_path = f"app/static/characters/series_{series['id']}_character.png"
            shutil.copy2(character_image, series_char_path)
            update_series(series["id"], character_image=series_char_path)
            progress_callback(3, f"キャラクター画像を生成 → シリーズ固定化")

    thumbnail_path = generate_thumbnail(
        title=title,
        genre="CEOドラマ",
        drama_id=drama_id,
        character_image=character_image,
        progress_callback=progress_callback
    )
    update_drama(drama_id, thumbnail_url=thumbnail_path)
    progress_callback(3, "画像生成完了")

    progress_callback(4, "シーン分割...")
    for i, scene in enumerate(scenes):
        progress_callback(4, f"  シーン{scene.get('scene_number', i+1)}: {scene.get('narration', '')[:30]}...")

    progress_callback(5, "動画シーンを生成中 (Kling AI / Luma)...")
    scene_videos = []
    for i, scene in enumerate(scenes):
        progress_callback(5, f"シーン{i+1}/{len(scenes)}を生成中...")
        scene_desc = scene.get("description", "")
        if main_character:
            ref_image = _pick_character_image_for_scene(main_character, scene_desc)
        elif character_image and os.path.exists(character_image):
            ref_image = character_image
        else:
            ref_image = None
        scene_emotion = scene.get("emotion", "")
        scene_duration = float(scene.get("duration", 6))
        scene_narration = scene.get("narration", "")
        scene_path = generate_scene_video(
            scene_description=scene_desc,
            scene_number=scene.get("scene_number", i+1),
            drama_id=drama_id,
            reference_image=ref_image,
            progress_callback=progress_callback,
            emotion=scene_emotion,
            duration=scene_duration,
            narration=scene_narration
        )
        scene_videos.append(scene_path)
        progress_callback(5, f"シーン{i+1}/{len(scenes)}生成完了")

    progress_callback(6, "音声を生成中 (ElevenLabs マルチボイス)...")
    audio_path = generate_voice(narration, drama_id, progress_callback=progress_callback, scenes=scenes)
    progress_callback(6, f"音声生成完了")

    progress_callback(7, "字幕・動画を編集中（FFmpeg）...")
    subtitle_path = generate_subtitle(scenes, drama_id, progress_callback=progress_callback)
    final_video = edit_video(scene_videos, audio_path, drama_id, subtitle_path=subtitle_path)
    update_drama(drama_id, video_url=final_video, status="ready")
    progress_callback(7, f"動画編集完了（字幕付き）")

    youtube_id = None
    tiktok_id = None
    theme_data_title = title.split("「")[-1].rstrip("」") if "「" in title else ""
    theme_summary = drama.get("theme", "")[:80]

    description = (
        f"【CEOの扉】{series_name} 第{series_episode}話「{theme_data_title}」\n\n"
        f"{theme_summary}\n\n"
        f"45秒で描く、運命のCEOドラマ。\n"
        f"全{series.get('total_episodes', 30)}話のシリーズ、毎日更新中。\n\n"
        f"次の話が気になる人はフォロー!\n"
        f"コメントで展開を予想してね\n\n"
        f"#CEOの扉 #ショートドラマ #社長ドラマ #恋愛 #CEOドラマ "
        f"#胸キュン #ドラマチック #Shorts #TikTok"
    )
    tags = [
        "CEOの扉", "ショートドラマ", "社長ドラマ", "恋愛ドラマ",
        "CEOドラマ", "胸キュン", "ドラマチック", "恋愛",
        "Shorts", "TikTok", series_name, theme_data_title
    ]

    if is_youtube_connected():
        progress_callback(8, "YouTubeにアップロード中...")
        youtube_id = upload_video(
            final_video,
            title=title,
            description=description,
            tags=tags,
            thumbnail_path=thumbnail_path if os.path.exists(thumbnail_path) else None
        )
        progress_callback(8, f"YouTube投稿完了: {youtube_id}")
    else:
        progress_callback(8, "YouTube未接続 — スキップ")

    if is_tiktok_rpa_connected():
        progress_callback(8, "TikTokにRPAアップロード中...")
        tiktok_id = upload_to_tiktok_rpa_sync(
            video_path=final_video,
            title=title,
            description=description,
            tags=tags
        )
        if tiktok_id:
            progress_callback(8, f"TikTok RPA投稿完了: {tiktok_id}")
        else:
            progress_callback(8, "TikTok RPA投稿失敗")
    else:
        progress_callback(8, "TikTok未接続 — スキップ")

    status = "published" if (youtube_id or tiktok_id) else "ready"
    update_data = {"status": status}
    if youtube_id:
        update_data["youtube_id"] = youtube_id
    if tiktok_id:
        update_data["tiktok_id"] = tiktok_id
    update_drama(drama_id, **update_data)

    update_series(series["id"], current_episode=series_episode)

    progress_callback(9, "パイプライン完了!")
    return {"success": True, "drama_id": drama_id, "title": title}
