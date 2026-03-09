import os
import subprocess
import logging

logger = logging.getLogger(__name__)

OUTPUT_DIR = "app/static/videos"
os.makedirs(OUTPUT_DIR, exist_ok=True)

BGM_PATH = "app/static/bgm/ambient_sleep.mp3"


def generate_video(audio_path: str, image_path: str, video_id: int) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"video_{video_id}.mp4")

    if os.path.exists(BGM_PATH):
        mixed_audio = os.path.join(OUTPUT_DIR, f"mixed_audio_{video_id}.mp3")
        mix_cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-stream_loop", "-1", "-i", BGM_PATH,
            "-filter_complex",
            "[1]volume=0.15[bgm];[0][bgm]amix=inputs=2:duration=first:dropout_transition=3",
            "-c:a", "libmp3lame", "-b:a", "192k",
            mixed_audio
        ]
        logger.info(f"Mixing BGM: {' '.join(mix_cmd)}")
        mix_result = subprocess.run(mix_cmd, capture_output=True, text=True, timeout=600)
        if mix_result.returncode != 0:
            logger.warning(f"BGM mixing failed, using narration only: {mix_result.stderr[:300]}")
            mixed_audio = audio_path
        else:
            logger.info("BGM mixed successfully")
    else:
        mixed_audio = audio_path
        logger.info("No BGM file found, using narration only")

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-i", mixed_audio,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"FFmpeg command: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600
    )

    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[:500]}")

    if mixed_audio != audio_path and os.path.exists(mixed_audio):
        os.remove(mixed_audio)

    logger.info(f"Video generated: {output_path}")
    return output_path
