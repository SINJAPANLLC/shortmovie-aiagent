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


def _noop(step, msg):
    pass


def _clean_narration_for_tts(text: str) -> str:
    cleaned = re.sub(r'(CEO|主人公|ナレーション|美咲|涼介|[ぁ-んァ-ヶー一-龥a-zA-Z]+)「', '「', text)
    cleaned = re.sub(r'「([^」]*)」', r'\1。', cleaned)
    cleaned = re.sub(r'[。。]{2,}', '。', cleaned)
    cleaned = cleaned.strip()
    return cleaned


def generate_voice(text: str, drama_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_path = os.path.join(AUDIO_DIR, f"drama_{drama_id}.mp3")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    client = ElevenLabs(api_key=api_key)
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "fUjY9K2nAIwlALOwSiwc")

    clean_text = _clean_narration_for_tts(text)
    logger.info(f"TTS original ({len(text)} chars) -> cleaned ({len(clean_text)} chars)")

    progress_callback(6, f"音声生成中（{len(clean_text)}文字）...")

    for attempt in range(MAX_RETRIES):
        try:
            audio_generator = client.text_to_speech.convert(
                voice_id=voice_id,
                text=clean_text,
                model_id="eleven_v3",
                voice_settings={
                    "stability": 0.75,
                    "similarity_boost": 0.75,
                    "style": 0.3,
                    "speed": 1.0
                }
            )
            with open(output_path, "wb") as f:
                for chunk in audio_generator:
                    f.write(chunk)

            if os.path.getsize(output_path) < 1000:
                raise RuntimeError(f"Generated audio file too small: {os.path.getsize(output_path)} bytes")

            progress_callback(6, "音声生成完了")
            logger.info(f"Audio generated: {output_path}")
            return output_path
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"ElevenLabs error, retrying in {RETRY_DELAY}s (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                progress_callback(6, f"音声生成リトライ ({attempt+1}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
            else:
                raise
