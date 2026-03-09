import os
import subprocess
import logging

logger = logging.getLogger(__name__)

OUTPUT_DIR = "app/static/videos"
BGM_DIR = "app/static/bgm"
os.makedirs(OUTPUT_DIR, exist_ok=True)

BGM_PATH = os.path.join(BGM_DIR, "ambient_sleep.mp3")


def edit_video(scene_videos: list, audio_path: str, drama_id: int, subtitle_path: str = None) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"drama_{drama_id}.mp4")

    if not scene_videos:
        raise RuntimeError("No scene videos to edit")

    concat_list = os.path.join(OUTPUT_DIR, f"concat_{drama_id}.txt")
    with open(concat_list, "w") as f:
        for vp in scene_videos:
            f.write(f"file '{os.path.abspath(vp)}'\n")

    concat_output = os.path.join(OUTPUT_DIR, f"concat_{drama_id}.mp4")
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        concat_output
    ]
    result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error(f"FFmpeg concat error: {result.stderr}")
        raise RuntimeError(f"Video concat failed: {result.stderr[:500]}")

    has_narration = audio_path and os.path.exists(audio_path)
    has_bgm = os.path.exists(BGM_PATH)

    if has_narration and has_bgm:
        mixed_audio = os.path.join(OUTPUT_DIR, f"mixed_audio_{drama_id}.mp3")
        cmd_mix = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-stream_loop", "-1", "-i", BGM_PATH,
            "-filter_complex",
            "[0:a]volume=1.0[narration];[1:a]volume=0.15[bgm];[narration][bgm]amix=inputs=2:duration=first:dropout_transition=3[out]",
            "-map", "[out]",
            "-ac", "2",
            "-ar", "44100",
            mixed_audio
        ]
        mix_result = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120)
        if mix_result.returncode == 0:
            logger.info("BGM mixed with narration successfully")
            final_audio = mixed_audio
        else:
            logger.warning(f"BGM mix failed, using narration only: {mix_result.stderr[:200]}")
            final_audio = audio_path
            mixed_audio = None
    elif has_narration:
        final_audio = audio_path
        mixed_audio = None
    elif has_bgm:
        final_audio = BGM_PATH
        mixed_audio = None
    else:
        final_audio = None
        mixed_audio = None

    inputs = ["-i", concat_output]
    map_args = []
    if final_audio:
        inputs += ["-i", final_audio]
        map_args = ["-map", "0:v", "-map", "1:a", "-shortest"]

    vf_filters = ["scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"]

    if subtitle_path and os.path.exists(subtitle_path):
        safe_sub_path = os.path.abspath(subtitle_path).replace("\\", "/").replace(":", "\\:")
        sub_style = (
            "FontName=Noto Serif CJK JP,"
            "FontSize=16,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "BackColour=&H80000000,"
            "BorderStyle=3,"
            "Outline=2,"
            "Shadow=0,"
            "MarginV=100,"
            "MarginL=80,"
            "MarginR=80,"
            "Alignment=2,"
            "Bold=0"
        )
        vf_filters.append(f"subtitles='{safe_sub_path}':force_style='{sub_style}'")
        logger.info(f"Burning subtitles from: {subtitle_path}")

    vf_string = ",".join(vf_filters)

    cmd_final = ["ffmpeg", "-y"] + inputs + map_args + [
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-vf", vf_string,
        output_path
    ]

    result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"FFmpeg final edit error: {result.stderr}")
        raise RuntimeError(f"Final video edit failed: {result.stderr[:500]}")

    for f_path in [concat_list, concat_output]:
        if os.path.exists(f_path):
            os.remove(f_path)
    if mixed_audio and os.path.exists(mixed_audio):
        os.remove(mixed_audio)

    logger.info(f"Final video generated: {output_path}")
    return output_path


def create_placeholder_video(drama_id: int, duration: float = 45) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"drama_{drama_id}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1a2e2e:s=1080x1920:d={duration}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Placeholder video creation failed: {result.stderr[:300]}")

    return output_path
