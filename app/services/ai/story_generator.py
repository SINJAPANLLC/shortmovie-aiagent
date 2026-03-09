import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log, get_active_series

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 30


def _noop(step, msg):
    pass


def generate_script(theme, genre, hook="", twist="", drama_id=None, progress_callback=None, series_info=None):
    if progress_callback is None:
        progress_callback = _noop

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    if series_info is None:
        series_info = get_active_series()

    series_context = ""
    if series_info:
        series_context = f"""
【シリーズ情報】
チャンネル: CEOの扉
シリーズ: {series_info.get('name', '')}
概要: {series_info.get('description', '')}
あらすじ: {series_info.get('synopsis', '')}
現在の話数: 第{series_info.get('current_episode', 0) + 1}話 / 全{series_info.get('total_episodes', 30)}話"""

    prompt = f"""あなたは「CEOの扉」チャンネルの脚本家です。

CEOと普通の女性の運命的な出会いを描くショートドラマの脚本を書いてください。
{series_context}

テーマ: {theme}
冒頭フック: {hook}
引き/どんでん返し: {twist}

【動画構造】
0〜2秒: 衝撃フック（視聴者を引き込む一言 — 感情的・衝撃的な台詞やナレーション）
2〜35秒: ストーリー展開（CEOと主人公の関係の変化を描く）
35〜45秒: どんでん返し/引き + 「続く...」

【ルール】
- 合計150〜200文字の脚本（ナレーション用）
- 6〜8シーンに分割できる構成
- 各シーンは1〜2文で表現
- 感情の起伏を入れる（緊張、ドキドキ、衝撃、切なさ）
- 最後は必ず「続く...」で終わる
- 視聴者が次の動画を見たくなる終わり方
- CEOドラマにふさわしい世界観（高級オフィス、高級車、レストラン等）

【映像の雰囲気】
- 高級感のある映像（ガラス張りオフィス、シティビュー、高級レストラン）
- ドラマチックなライティング（夕暮れ、夜景、逆光）
- 感情的な表情のクローズアップ
- 9:16 縦動画フォーマット（1080x1920）

以下のJSON形式で返してください:
{{
    "narration": "全体のナレーションテキスト（150〜200文字）",
    "scenes": [
        {{"scene_number": 1, "duration": 6, "description": "シーンの映像説明（英語・動画生成AI用）", "narration": "このシーンのナレーション"}},
        {{"scene_number": 2, "duration": 6, "description": "シーンの映像説明（英語・動画生成AI用）", "narration": "このシーンのナレーション"}},
        ...
    ]
}}

シーンのdescriptionは動画生成AIに送るため、英語で映像の説明を書いてください。
以下の修飾語を積極的に使ってください:
- cinematic, luxury office, city skyline, dramatic lighting
- beautiful Japanese woman, handsome CEO in suit
- emotional close-up, vertical 9:16 framing
- glass building, expensive restaurant, night city view"""

    message = None
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(f"Claude API error, retrying in {delay}s (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                progress_callback(3, f"API エラー — {delay}秒後にリトライ ({attempt+1}/{MAX_RETRIES})")
                time.sleep(delay)
            else:
                raise

    response_text = message.content[0].text

    if drama_id:
        save_ai_log(drama_id, "脚本生成", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except (json.JSONDecodeError, IndexError):
        pass

    return {
        "narration": "高層ビルのエレベーターで、彼女は見知らぬ男とぶつかった。「大丈夫ですか？」その声の主は、この会社のCEOだった。運命の歯車が、今動き始める。続く...",
        "scenes": [
            {"scene_number": 1, "duration": 6, "description": "Close-up of elevator doors opening in luxury glass building, cinematic dramatic lighting, vertical 9:16", "narration": "高層ビルのエレベーターで、"},
            {"scene_number": 2, "duration": 6, "description": "Beautiful Japanese woman bumping into handsome CEO in suit, luxury office lobby, dramatic angle, vertical framing", "narration": "彼女は見知らぬ男とぶつかった。"},
            {"scene_number": 3, "duration": 6, "description": "Close-up of CEO's face, warm smile, cinematic lighting, luxury background, emotional", "narration": "「大丈夫ですか？」"},
            {"scene_number": 4, "duration": 6, "description": "Woman's shocked expression seeing CEO nameplate on desk, dramatic reveal, glass office with city skyline", "narration": "その声の主は、この会社のCEOだった。"},
            {"scene_number": 5, "duration": 6, "description": "Split screen of woman and CEO, city night view background, dramatic lighting, fate concept", "narration": "運命の歯車が、今動き始める。"},
            {"scene_number": 6, "duration": 6, "description": "Dramatic cliffhanger shot, woman walking away with determined look, city lights, text overlay 'to be continued', vertical 9:16", "narration": "続く..."},
        ]
    }
