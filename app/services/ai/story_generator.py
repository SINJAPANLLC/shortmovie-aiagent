import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 30


def _noop(step, msg):
    pass


def generate_script(theme, genre, hook="", twist="", drama_id=None, progress_callback=None):
    if progress_callback is None:
        progress_callback = _noop

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    prompt = f"""あなたはAIショートドラマの脚本家です。

45秒のショートドラマの脚本を書いてください。

テーマ: {theme}
ジャンル: {genre}
冒頭フック: {hook}
どんでん返し: {twist}

【動画構造】
0〜2秒: 衝撃フック（視聴者を引き込む一言）
2〜35秒: ストーリー展開
35〜45秒: どんでん返し + 「続く...」

【ルール】
- 合計150〜200文字の脚本（ナレーション用）
- 6〜8シーンに分割できる構成
- 各シーンは1〜2文で表現
- 感情の起伏を入れる
- 最後は必ず「続く...」で終わる
- 視聴者が次の動画を見たくなる終わり方

以下のJSON形式で返してください:
{{
    "narration": "全体のナレーションテキスト（150〜200文字）",
    "scenes": [
        {{"scene_number": 1, "duration": 6, "description": "シーンの映像説明（英語）", "narration": "このシーンのナレーション"}},
        {{"scene_number": 2, "duration": 6, "description": "シーンの映像説明（英語）", "narration": "このシーンのナレーション"}},
        ...
    ]
}}

シーンのdescriptionは動画生成AIに送るため、英語で映像の説明を書いてください。
cinematic, dramatic lighting, emotional expression などの修飾語を含めてください。"""

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
        "narration": "ある日、彼女のスマホに見知らぬ番号からメッセージが届いた。それは彼氏の秘密を暴く内容だった。真実を知った彼女は、静かに復讐の計画を立て始める。続く...",
        "scenes": [
            {"scene_number": 1, "duration": 6, "description": "Close-up of phone screen with mysterious message, dramatic lighting", "narration": "ある日、彼女のスマホに見知らぬ番号からメッセージが届いた。"},
            {"scene_number": 2, "duration": 6, "description": "Woman reading phone with shocked expression, cinematic", "narration": "それは彼氏の秘密を暴く内容だった。"},
            {"scene_number": 3, "duration": 6, "description": "Flashback of couple together, warm lighting turning cold", "narration": "幸せだった日々が嘘のように崩れていく。"},
            {"scene_number": 4, "duration": 6, "description": "Woman with determined expression, dramatic shadows", "narration": "真実を知った彼女は、静かに復讐の計画を立て始める。"},
            {"scene_number": 5, "duration": 6, "description": "Mysterious smile, close-up, dramatic lighting", "narration": "その微笑みの裏に隠された本当の目的とは。"},
            {"scene_number": 6, "duration": 6, "description": "Cliffhanger shot, text overlay saying 'to be continued'", "narration": "続く..."},
        ]
    }
