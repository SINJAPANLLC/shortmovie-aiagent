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
    "female": {
        "voice_id": "pFZP5JQG7iQjIQuC4Bku",
        "label": "Lily (女性・ドラマティック)",
        "settings": {
            "stability": 0.25,
            "similarity_boost": 0.85,
            "style": 0.78,
            "speed": 0.92
        }
    },
    "male": {
        "voice_id": "JBFqnCBsd6RMkjVDRZzb",
        "label": "George (男性・低音)",
        "settings": {
            "stability": 0.30,
            "similarity_boost": 0.85,
            "style": 0.70,
            "speed": 0.88
        }
    },
    "narrator": {
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "label": "Sarah (ナレーター・物語)",
        "settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.55,
            "speed": 0.90
        }
    },
}

SPEAKER_ROLE_MAP = {
    "主人公": "female",
    "ヒロイン": "female",
    "彼女": "female",
    "妻": "female",
    "CEO": "male",
    "社長": "male",
    "上司": "male",
    "彼": "male",
    "夫": "male",
    "部長": "male",
    "専務": "male",
    "ナレーション": "narrator",
    "ナレーター": "narrator",
    "NA": "narrator",
}


def _noop(step, msg):
    pass


def _classify_speaker_role(speaker: str) -> str:
    if speaker in SPEAKER_ROLE_MAP:
        return SPEAKER_ROLE_MAP[speaker]
    return "narrator"


def _strip_speaker_names(text: str) -> str:
    cleaned = re.sub(r'[^\s「」、。！？…]+「', '「', text)
    cleaned = re.sub(r'「([^」]*)」', r'\1', cleaned)
    return cleaned.strip()


def _split_scene_narration(narration: str, scene_speaker: str) -> list:
    pattern = re.compile(r'([^「」\s、。！？…]+?)「([^」]+)」')

    speakers_found = []
    for match in pattern.finditer(narration):
        speakers_found.append(match.group(1).strip())

    known_speakers = [s for s in speakers_found if _classify_speaker_role(s) != "narrator"]
    unknown_speakers = [s for s in speakers_found if _classify_speaker_role(s) == "narrator" and s != "ナレーション" and s != "ナレーター"]

    name_to_role = {}
    for s in known_speakers:
        name_to_role[s] = _classify_speaker_role(s)

    if unknown_speakers:
        unique_unknowns = list(dict.fromkeys(unknown_speakers))
        scene_role = _classify_speaker_role(scene_speaker)
        if scene_role == "narrator":
            scene_role = "female"
        opposite = "male" if scene_role == "female" else "female"

        if len(unique_unknowns) == 1:
            name_to_role[unique_unknowns[0]] = scene_role
        elif len(unique_unknowns) >= 2:
            name_to_role[unique_unknowns[0]] = scene_role
            for u in unique_unknowns[1:]:
                name_to_role[u] = opposite

    segments = []
    last_end = 0

    for match in pattern.finditer(narration):
        start = match.start()
        if start > last_end:
            between = narration[last_end:start].strip()
            if between:
                segments.append({"role": "narrator", "text": between})

        raw_speaker = match.group(1).strip()
        dialogue = match.group(2).strip()

        if raw_speaker in name_to_role:
            role = name_to_role[raw_speaker]
        else:
            role = _classify_speaker_role(raw_speaker)
            if role == "narrator" and raw_speaker not in ("ナレーション", "ナレーター"):
                role = _classify_speaker_role(scene_speaker)

        segments.append({"role": role, "text": dialogue})
        last_end = match.end()

    if last_end < len(narration):
        remaining = narration[last_end:].strip()
        if remaining:
            segments.append({"role": "narrator", "text": remaining})

    if not segments and narration.strip():
        role = _classify_speaker_role(scene_speaker)
        segments.append({"role": role, "text": narration.strip()})

    return segments


def _generate_segment_audio(client, role: str, text: str, segment_path: str) -> str:
    profile = VOICE_PROFILES.get(role, VOICE_PROFILES["narrator"])

    for attempt in range(MAX_RETRIES):
        try:
            audio_gen = client.text_to_speech.convert(
                voice_id=profile["voice_id"],
                text=text,
                model_id="eleven_v3",
                voice_settings=profile["settings"]
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


def generate_voice(narration: str, drama_id: int, progress_callback=None, scenes: list = None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_path = os.path.join(AUDIO_DIR, f"drama_{drama_id}.mp3")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    client = ElevenLabs(api_key=api_key)

    all_segments = []

    if scenes:
        for scene in scenes:
            scene_narration = scene.get("narration", "").strip()
            if not scene_narration:
                continue
            scene_speaker = scene.get("speaker", "ナレーション")
            scene_segments = _split_scene_narration(scene_narration, scene_speaker)
            all_segments.extend(scene_segments)
    else:
        all_segments = _split_scene_narration(narration, "ナレーション")

    if not all_segments:
        all_segments = [{"role": "narrator", "text": narration.strip() or "..."}]

    role_counts = {}
    for seg in all_segments:
        role_counts[seg["role"]] = role_counts.get(seg["role"], 0) + 1

    role_labels = {"female": "女性", "male": "男性", "narrator": "ナレーション"}
    summary = ", ".join(f"{role_labels.get(k, k)}:{v}回" for k, v in role_counts.items())
    logger.info(f"Multi-voice TTS: {len(all_segments)} segments ({summary})")
    progress_callback(6, f"音声生成中（{len(all_segments)}セグメント: {summary}）...")

    segment_paths = []
    try:
        for i, segment in enumerate(all_segments):
            seg_path = os.path.join(AUDIO_DIR, f"drama_{drama_id}_seg{i:03d}.mp3")
            profile = VOICE_PROFILES.get(segment["role"], VOICE_PROFILES["narrator"])
            logger.info(f"  Seg {i+1}/{len(all_segments)}: {segment['role']} ({profile['label']}) - \"{segment['text'][:40]}...\"")
            progress_callback(6, f"音声 {i+1}/{len(all_segments)}: {role_labels.get(segment['role'], segment['role'])}")

            _generate_segment_audio(client, segment["role"], segment["text"], seg_path)
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
