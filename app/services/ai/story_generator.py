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


def generate_script(theme, genre, hook="", twist="", drama_id=None, progress_callback=None, series_info=None, previous_script=None, emotional_arc="", characters_context=""):
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
現在: 第{series_info.get('current_episode', 0) + 1}話 / 全{series_info.get('total_episodes', 30)}話"""

    prev_script_context = ""
    if previous_script:
        prev_script_context = f"""
【前回の脚本】（この続きとして自然につなげてください）
{previous_script[:600]}"""

    prompt = f"""あなたは、日本のショートドラマ業界でトップの脚本家です。
TikTokやYouTube Shortsで何百万回も再生される45秒ドラマを書いてください。

【あなたの脚本の特徴】
1. セリフが自然で生き生きしている（棒読みにならない、感情が込もったセリフ）
2. 1話の中に必ず「ドキッとする瞬間」がある
3. 見終わった後「次どうなるの!?」と思わせる
{series_context}
{prev_script_context}
{characters_context}

テーマ: {theme}
冒頭フック: {hook}
引き/どんでん返し: {twist}
{f'感情の流れ: {emotional_arc}' if emotional_arc else ''}

【45秒の構造】
■ 0〜3秒: フック — スクロールを止める一言（セリフで始める）
■ 3〜10秒: 状況 — 何が起きてるか1シーンで見せる
■ 10〜30秒: ドラマの核心 — 感情がぶつかる対話。短い鋭いセリフの応酬
■ 30〜40秒: 転換 — 予想外の展開や新事実
■ 40〜45秒: 引き — 「えっ!?」で終わる + 続く...

【絶対ルール】
- セリフ中心（全体の80%以上がセリフ）。ナレーションは1〜2文だけ
- 合計150〜200文字
- 6〜8シーンに分割（1シーン = 5〜7秒）
- セリフ形式: 主人公「セリフ」 CEO「セリフ」 ナレーション「状況説明」
- 自然な日本語の会話（「...」や「！」を効果的に使う）
- 各シーンのセリフは短く（1シーン15〜30文字）
- 最後は必ず「続く...」

【絶対禁止】
- キャラの実名（翔子、蓮、美咲、涼介など）は使用禁止
- 必ず「主人公」「CEO」「ナレーション」の役割名のみ使用
- 説明的な文章（「彼は悲しんだ」→ ×）。代わりに行動で見せる
- 長いナレーション（映像で見せる。言葉で説明しない）

【映像description（英語）の書き方】
各シーンのdescriptionは、動画生成AIへの指示です。
各シーンで異なる構図・場所・アングルを使い、映像に変化をつけてください:
- シーンごとに異なるカメラアングル（close-up, wide shot, over-shoulder, low angle等）
- シーンごとに異なる場所や背景（オフィス, カフェ, 車内, 雨の路上, エレベーター等）
- 照明の変化（warm golden light, cold blue light, sunset backlight等）
- 表情や仕草の描写（tears, trembling hands, clenched jaw等）
- 必ず "vertical 9:16 composition, 1080x1920, cinematic, photorealistic" を含める
- 40〜60 words

以下のJSON形式で返してください:
{{
    "narration": "全体のセリフテキスト（主人公「」CEO「」ナレーション「」形式。150-200文字）",
    "scenes": [
        {{
            "scene_number": 1,
            "duration": 6,
            "description": "English visual description for AI video generation (40-60 words, unique camera angle + location + lighting + expression for each scene)",
            "narration": "このシーンのセリフ（主人公「セリフ」CEO「セリフ」等）",
            "speaker": "主人公 or CEO or ナレーション",
            "emotion": "感情（例: 緊張、衝撃、切なさ）"
        }}
    ]
}}"""

    message = None
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=3000,
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
        "narration": "CEO「君、泣いてたよね」ナレーション「エレベーターで隣に立った男が、そう言った」主人公「え...誰...？」ナレーション「誰にも見せたことのない涙を、この人は見ていた」CEO「大丈夫じゃなくていい」主人公「...なんで、そんなこと言うんですか」ナレーション「翌朝、出社した彼女の目の前に、昨日の男が立っていた。名札には『CEO』の文字」続く...",
        "scenes": [
            {"scene_number": 1, "duration": 5, "description": "Extreme close-up of a man's lips speaking in elevator, dramatic side lighting, luxury glass elevator with city view, vertical 9:16 composition, 1080x1920, cinematic shallow depth of field", "narration": "CEO「君、泣いてたよね」", "speaker": "CEO", "emotion": "衝撃"},
            {"scene_number": 2, "duration": 5, "description": "Over-the-shoulder shot of beautiful Japanese woman looking up at tall handsome man in suit, elevator interior with warm golden lighting, eyes wide with surprise, vertical 9:16 composition, 1080x1920", "narration": "ナレーション「エレベーターで隣に立った男が、そう言った」主人公「え...誰...？」", "speaker": "主人公", "emotion": "動揺"},
            {"scene_number": 3, "duration": 6, "description": "Close-up of woman's face, eyes glistening with unshed tears, soft diffused lighting, glass elevator descending with city lights behind, emotional vulnerability, vertical 9:16 composition, 1080x1920", "narration": "ナレーション「誰にも見せたことのない涙を、この人は見ていた」", "speaker": "ナレーション", "emotion": "切なさ"},
            {"scene_number": 4, "duration": 6, "description": "Medium shot of CEO gently smiling, warm backlight from city skyline at dusk, luxury suit, slight smile that reaches his eyes, slow dolly in, vertical 9:16 composition, 1080x1920", "narration": "CEO「大丈夫じゃなくていい」", "speaker": "CEO", "emotion": "温かさ"},
            {"scene_number": 5, "duration": 6, "description": "Close-up of woman's hands trembling, soft morning light, modern office lobby background, vertical 9:16 composition, 1080x1920", "narration": "主人公「...なんで、そんなこと言うんですか」", "speaker": "主人公", "emotion": "緊張"},
            {"scene_number": 6, "duration": 5, "description": "Dramatic slow dolly in on CEO standing at reception, low angle looking up, cold blue office light, woman's shocked reflection in glass wall, name plate reading CEO visible, cliffhanger composition, vertical 9:16, 1080x1920", "narration": "ナレーション「翌朝、出社した彼女の目の前に、昨日の男が立っていた。名札には『CEO』の文字」続く...", "speaker": "ナレーション", "emotion": "衝撃"},
        ]
    }
