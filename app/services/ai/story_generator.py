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

【画像description（日本語）の書き方】
各シーンのdescriptionは、画像生成AIへの指示です。日本語で書いてください。
各シーンで異なる構図・場所・アングルを使い、映像に変化をつけてください:
- シーンごとに異なるカメラアングル（クローズアップ、ワイドショット、肩越しショット、ローアングル等）
- シーンごとに異なる場所や背景（オフィス、カフェ、車内、雨の路上、エレベーター等）
- 照明の変化（温かい金色の光、冷たい青い光、夕日の逆光等）
- 表情や仕草の描写（涙、震える手、食いしばった歯等）
- 必ず「縦型9:16構図、1080x1920、シネマティック、フォトリアリスティック」を含める
- 40〜80文字

以下のJSON形式で返してください:
{{
    "narration": "全体のセリフテキスト（主人公「」CEO「」ナレーション「」形式。150-200文字）",
    "scenes": [
        {{
            "scene_number": 1,
            "duration": 6,
            "description": "日本語の画像プロンプト（40〜80文字、シーンごとに異なるカメラアングル+場所+照明+表情）",
            "narration": "このシーンのセリフ（主人公「セリフ」CEO「セリフ」等）",
            "speaker": "主人公 or CEO or 主人公+CEO or ナレーション or 主人公+ナレーション or CEO+ナレーション",
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
            {"scene_number": 1, "duration": 5, "description": "エレベーター内で話す男性の唇のクローズアップ、ドラマチックなサイドライト、都市の眺望が見える高級ガラスエレベーター、縦型9:16構図、1080x1920、シネマティック、浅い被写界深度", "narration": "CEO「君、泣いてたよね」", "speaker": "CEO", "emotion": "衝撃"},
            {"scene_number": 2, "duration": 5, "description": "スーツ姿の長身ハンサムな男性を見上げる美しい日本人女性の肩越しショット、温かい金色の照明のエレベーター内、驚きで見開いた目、縦型9:16構図、1080x1920、フォトリアリスティック", "narration": "ナレーション「エレベーターで隣に立った男が、そう言った」主人公「え...誰...？」", "speaker": "主人公+ナレーション", "emotion": "動揺"},
            {"scene_number": 3, "duration": 6, "description": "女性の顔のクローズアップ、涙が光る目、柔らかい拡散光、都市の夜景を背に下降するガラスエレベーター、感情的な脆さ、縦型9:16構図、1080x1920、シネマティック", "narration": "ナレーション「誰にも見せたことのない涙を、この人は見ていた」", "speaker": "ナレーション", "emotion": "切なさ"},
            {"scene_number": 4, "duration": 6, "description": "優しく微笑むCEOのミディアムショット、夕暮れの都市スカイラインからの温かい逆光、高級スーツ、目まで届く微笑み、ゆっくりドリーイン、縦型9:16構図、1080x1920、シネマティック", "narration": "CEO「大丈夫じゃなくていい」", "speaker": "CEO", "emotion": "温かさ"},
            {"scene_number": 5, "duration": 6, "description": "震える女性の手のクローズアップ、柔らかい朝の光、モダンなオフィスロビーの背景、縦型9:16構図、1080x1920、フォトリアリスティック、シネマティック", "narration": "主人公「...なんで、そんなこと言うんですか」", "speaker": "主人公", "emotion": "緊張"},
            {"scene_number": 6, "duration": 5, "description": "受付に立つCEOへのドラマチックなローアングル、冷たい青いオフィスの光、ガラス壁に映る女性の驚いた表情、CEO名札が見える、クリフハンガー構図、縦型9:16、1080x1920、シネマティック", "narration": "ナレーション「翌朝、出社した彼女の目の前に、昨日の男が立っていた。名札には『CEO』の文字」続く...", "speaker": "ナレーション", "emotion": "衝撃"},
        ]
    }
