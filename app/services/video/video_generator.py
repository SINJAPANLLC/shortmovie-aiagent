import os
import subprocess
import logging

logger = logging.getLogger(__name__)

OUTPUT_DIR = "app/static/videos"
os.makedirs(OUTPUT_DIR, exist_ok=True)


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

    filter_complex = ""
    inputs = ["-i", concat_output]
    if audio_path and os.path.exists(audio_path):
        inputs += ["-i", audio_path]
        filter_complex = "-map 0:v -map 1:a -shortest"

    cmd_final = ["ffmpeg", "-y"] + inputs
    if filter_complex:
        cmd_final += filter_complex.split()
    cmd_final += [
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        output_path
    ]

    result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"FFmpeg final edit error: {result.stderr}")
        raise RuntimeError(f"Final video edit failed: {result.stderr[:500]}")

    for f in [concat_list, concat_output]:
        if os.path.exists(f):
            os.remove(f)

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
