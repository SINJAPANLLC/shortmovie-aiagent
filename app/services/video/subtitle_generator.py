import os
import logging
import re

logger = logging.getLogger(__name__)

SUBTITLE_DIR = "app/static/subtitle"
os.makedirs(SUBTITLE_DIR, exist_ok=True)


def generate_subtitle(scenes: list, drama_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = lambda s, m: None

    os.makedirs(SUBTITLE_DIR, exist_ok=True)
    output_path = os.path.join(SUBTITLE_DIR, f"drama_{drama_id}.srt")

    progress_callback(7, "字幕を生成中...")

    srt_entries = []
    current_time = 0.0

    for i, scene in enumerate(scenes):
        narration = scene.get("narration", "").strip()
        if not narration:
            narration = scene.get("description", "").strip()
        if not narration:
            continue

        try:
            duration = float(scene.get("duration", 5))
            if duration <= 0 or duration > 30:
                duration = 5.0
        except (ValueError, TypeError):
            duration = 5.0
        start_time = current_time
        end_time = current_time + duration

        chunks = _split_narration(narration, duration)

        chunk_duration = duration / len(chunks) if chunks else duration
        for j, chunk in enumerate(chunks):
            chunk_start = start_time + (j * chunk_duration)
            chunk_end = chunk_start + chunk_duration

            chunk_start = round(chunk_start, 3)
            chunk_end = round(chunk_end, 3)

            srt_entries.append({
                "index": len(srt_entries) + 1,
                "start": _format_srt_time(chunk_start),
                "end": _format_srt_time(chunk_end),
                "text": chunk
            })

        current_time = end_time

    srt_content = ""
    for entry in srt_entries:
        srt_content += f"{entry['index']}\n"
        srt_content += f"{entry['start']} --> {entry['end']}\n"
        srt_content += f"{entry['text']}\n\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    progress_callback(7, f"字幕生成完了: {len(srt_entries)}ブロック")
    logger.info(f"Subtitle generated: {output_path} ({len(srt_entries)} entries)")
    return output_path


def _split_narration(text: str, duration: float, max_chars_per_line: int = 18) -> list:
    text = text.strip()

    sentences = re.split(r'(?<=[。！？\n])', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [text]

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(sentence) > max_chars_per_line * 2:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            for k in range(0, len(sentence), max_chars_per_line):
                chunks.append(sentence[k:k + max_chars_per_line])
        elif len(current_chunk) + len(sentence) > max_chars_per_line * 2:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
        else:
            current_chunk += sentence

    if current_chunk:
        chunks.append(current_chunk)

    if not chunks:
        chunks = [text]

    return chunks


def _format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
