import os
import re
import time
import logging
import subprocess
from elevenlabs import ElevenLabs

logger = logging.getLogger(__name__)

AUDIO_DIR = "app/static/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

MAX_RETRIES = 3
RETRY_DELAY = 15

VOICE_PROFILES = {
    "美咲": {
        "voice_id": "pFZP5JQG7iQjIQuC4Bku",
        "label": "Lily (女性・表現力豊か)",
        "settings": {
            "stability": 0.35,
            "similarity_boost": 0.80,
            "style": 0.65,
            "speed": 0.95
        }
    },
    "涼介": {
        "voice_id": "JBFqnCBsd6RMkjVDRZzb",
        "label": "George (男性・魅力的)",
        "settings": {
            "stability": 0.40,
            "similarity_boost": 0.80,
            "style": 0.55,
            "speed": 0.90
        }
    },
    "ナレーション": {
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "label": "Sarah (ナレーター・落ち着き)",
        "settings": {
            "stability": 0.55,
            "similarity_boost": 0.75,
            "style": 0.40,
            "speed": 0.95
        }
    },
}

SPEAKER_ALIASES = {
    "主人公": "美咲",
    "CEO": "涼介",
    "社長": "涼介",
}

DEFAULT_SPEAKER = "ナレーション"


def _noop(step, msg):
    pass


def _parse_speaker_segments(text: str) -> list:
    pattern = re.compile(r'((?:CEO|主人公|ナレーション|美咲|涼介|社長|[ぁ-んァ-ヶー]{1,10}))「([^」]+)」')
    segments = []
    last_end = 0

    for match in pattern.finditer(text):
        start = match.start()
        if start > last_end:
            between = text[last_end:start].strip()
            if between:
                segments.append({"speaker": DEFAULT_SPEAKER, "text": between})

        raw_speaker = match.group(1).strip()
        dialogue = match.group(2).strip()

        speaker = SPEAKER_ALIASES.get(raw_speaker, raw_speaker)
        if speaker not in VOICE_PROFILES:
            speaker = DEFAULT_SPEAKER

        segments.append({"speaker": speaker, "text": dialogue})
        last_end = match.end()

    if last_end < len(text):
        remaining = text[last_end:].strip()
        if remaining:
            segments.append({"speaker": DEFAULT_SPEAKER, "text": remaining})

    if not segments and text.strip():
        segments.append({"speaker": DEFAULT_SPEAKER, "text": text.strip()})

    return segments


def _generate_segment_audio(client, segment: dict, segment_path: str, default_voice_id: str) -> str:
    speaker = segment["speaker"]
    text = segment["text"]

    profile = VOICE_PROFILES.get(speaker, VOICE_PROFILES[DEFAULT_SPEAKER])
    voice_id = profile["voice_id"] or default_voice_id

    voice_settings = profile["settings"]

    for attempt in range(MAX_RETRIES):
        try:
            audio_gen = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_v3",
                voice_settings=voice_settings
            )
            with open(segment_path, "wb") as f:
                for chunk in audio_gen:
                    f.write(chunk)

            if os.path.getsize(segment_path) < 500:
                raise RuntimeError(f"Segment audio too small: {os.path.getsize(segment_path)} bytes")

            return segment_path
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"Segment TTS retry {attempt+1}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                raise


def _concatenate_audio_segments(segment_paths: list, output_path: str) -> str:
    if len(segment_paths) == 1:
        os.rename(segment_paths[0], output_path)
        return output_path

    concat_list = output_path + ".txt"
    with open(concat_list, "w") as f:
        for sp in segment_paths:
            f.write(f"file '{os.path.abspath(sp)}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if os.path.exists(concat_list):
        os.remove(concat_list)
    for sp in segment_paths:
        if os.path.exists(sp):
            os.remove(sp)

    if result.returncode != 0:
        logger.error(f"Audio concat failed: {result.stderr[:300]}")
        raise RuntimeError(f"Audio concatenation failed: {result.stderr[:300]}")

    return output_path


def generate_voice(text: str, drama_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_path = os.path.join(AUDIO_DIR, f"drama_{drama_id}.mp3")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    client = ElevenLabs(api_key=api_key)
    default_voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "fUjY9K2nAIwlALOwSiwc")

    segments = _parse_speaker_segments(text)
    speaker_counts = {}
    for seg in segments:
        speaker_counts[seg["speaker"]] = speaker_counts.get(seg["speaker"], 0) + 1

    speaker_summary = ", ".join(f"{k}:{v}回" for k, v in speaker_counts.items())
    logger.info(f"Multi-voice TTS: {len(segments)} segments ({speaker_summary})")
    progress_callback(6, f"音声生成中（{len(segments)}セグメント: {speaker_summary}）...")

    segment_paths = []
    try:
        for i, segment in enumerate(segments):
            seg_path = os.path.join(AUDIO_DIR, f"drama_{drama_id}_seg{i:03d}.mp3")
            profile = VOICE_PROFILES.get(segment["speaker"], VOICE_PROFILES[DEFAULT_SPEAKER])
            logger.info(f"  Seg {i+1}/{len(segments)}: {segment['speaker']} ({profile['label']}) - {segment['text'][:30]}...")
            progress_callback(6, f"音声 {i+1}/{len(segments)}: {segment['speaker']}")

            _generate_segment_audio(client, segment, seg_path, default_voice_id)
            segment_paths.append(seg_path)

        progress_callback(6, "音声セグメント結合中...")
        _concatenate_audio_segments(segment_paths, output_path)

        if os.path.getsize(output_path) < 1000:
            raise RuntimeError(f"Final audio too small: {os.path.getsize(output_path)} bytes")

        progress_callback(6, "音声生成完了（マルチボイス）")
        logger.info(f"Multi-voice audio generated: {output_path}")
        return output_path

    except Exception as e:
        for sp in segment_paths:
            if os.path.exists(sp):
                os.remove(sp)
        raise
