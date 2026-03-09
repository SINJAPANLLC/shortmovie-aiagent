import os
import time
import logging
import subprocess
from elevenlabs import ElevenLabs

logger = logging.getLogger(__name__)

AUDIO_DIR = "app/static/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

CHUNK_SIZE = 4500
MAX_RETRIES = 3
RETRY_DELAY = 15


def _noop(step, msg):
    pass


def split_text_into_chunks(text: str, max_chars: int = CHUNK_SIZE) -> list:
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 > max_chars and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n"
        else:
            current_chunk += para + "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def generate_audio(text: str, video_id: int, progress_callback=None) -> str:
    if progress_callback is None:
        progress_callback = _noop

    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_path = os.path.join(AUDIO_DIR, f"audio_{video_id}.mp3")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    client = ElevenLabs(api_key=api_key)
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "fUjY9K2nAIwlALOwSiwc")

    chunks = split_text_into_chunks(text)
    total_chunks = len(chunks)
    progress_callback(4, f"テキストを{total_chunks}チャンクに分割（合計{len(text)}文字）")

    def _generate_chunk(client, voice_id, text_content, out_path, label=""):
        for attempt in range(MAX_RETRIES):
            try:
                audio_generator = client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text_content,
                    model_id="eleven_v3",
                    voice_settings={
                        "stability": 0.75,
                        "similarity_boost": 0.75,
                        "style": 0.0,
                        "speed": 0.85
                    }
                )
                with open(out_path, "wb") as f:
                    for chunk in audio_generator:
                        f.write(chunk)

                if os.path.getsize(out_path) < 1000:
                    raise RuntimeError(f"Generated audio file too small: {os.path.getsize(out_path)} bytes")
                return
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"ElevenLabs error{label}, retrying in {RETRY_DELAY}s (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                    time.sleep(RETRY_DELAY)
                else:
                    raise

    if total_chunks == 1:
        progress_callback(4, "音声を生成中（1/1チャンク）...")
        _generate_chunk(client, voice_id, text, output_path)
        progress_callback(4, "音声生成完了")
    else:
        chunk_paths = []
        for i, chunk_text in enumerate(chunks):
            chunk_path = os.path.join(AUDIO_DIR, f"audio_{video_id}_chunk_{i}.mp3")
            progress_callback(4, f"音声チャンク生成中: {i+1}/{total_chunks}（{len(chunk_text)}文字）")
            _generate_chunk(client, voice_id, chunk_text, chunk_path, label=f" chunk {i+1}/{total_chunks}")
            chunk_paths.append(chunk_path)

        progress_callback(4, f"全{total_chunks}チャンクの音声生成完了、結合中...")
        concat_list_path = os.path.join(AUDIO_DIR, f"concat_{video_id}.txt")
        with open(concat_list_path, "w") as f:
            for cp in chunk_paths:
                f.write(f"file '{os.path.abspath(cp)}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"FFmpeg concat error: {result.stderr}")
            raise RuntimeError(f"Audio concat failed: {result.stderr[:500]}")

        for cp in chunk_paths:
            os.remove(cp)
        os.remove(concat_list_path)
        progress_callback(4, "音声ファイル結合完了")

    logger.info(f"Audio generated: {output_path}")
    return output_path
