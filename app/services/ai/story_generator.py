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

【画像description（日本語）の書き方 — 超重要】
各シーンのdescriptionは、画像生成AI（Luma Photon）への直接指示です。日本語で書いてください。
実写映画のようなリアルな画像を生成するため、以下を必ず含めてください:

■ カメラワーク（シーンごとに必ず変える）:
- クローズアップ（顔）、エクストリームクローズアップ（目や手）、ミディアムショット（腰上）
- ワイドショット（全体）、肩越しショット（OTS）、ローアングル（威圧感）
- ハイアングル（俯瞰・脆弱さ）、ダッチアングル（不安定感）、POVショット

■ レンズ・被写界深度:
- 「浅い被写界深度、背景ぼかし」「85mmレンズ」「望遠圧縮」「広角歪み」等

■ ライティング（シーンごとに必ず変える）:
- 「窓からの自然光」「夕日の逆光でシルエット」「ネオンの青い光」
- 「三点照明」「リムライト」「ローキー照明（影多め）」「ハイキー（明るい）」

■ 場所・環境の具体的描写:
- 「高層オフィス38階の会議室、ガラス壁越しに東京タワーが見える」
- 「雨に濡れた渋谷のスクランブル交差点、ネオンが地面に反射」
- 「薄暗いバーカウンター、琥珀色のウイスキーグラス、間接照明」

■ 人物の具体的描写:
- 「涙が頬を一筋流れる」「唇を噛みしめる」「拳を握りしめる」
- 「目を伏せる」「驚きで目を見開く」「冷たい微笑」

■ 必須タグ（全シーンに含める）:
「フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920」

■ 禁止（AIっぽくなるため）:
- 「美しい」「かわいい」「きれい」等の抽象的な美的形容詞
- 「ドラマチック」だけの曖昧な指示（具体的にどうドラマチックか書く）
- 完璧すぎる左右対称の構図

description は60〜120文字で書いてください。具体的であるほど良い画像が生成されます。

以下のJSON形式で返してください:
{{
    "narration": "全体のセリフテキスト（主人公「」CEO「」ナレーション「」形式。150-200文字）",
    "scenes": [
        {{
            "scene_number": 1,
            "duration": 6,
            "description": "日本語の画像プロンプト（60〜120文字、カメラワーク+レンズ+ライティング+場所+人物の表情仕草+必須タグ）",
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
            {"scene_number": 1, "duration": 5, "description": "85mmレンズ、男性の唇から顎にかけてのエクストリームクローズアップ、左サイドからのタングステンキーライト、背景に東京タワーが見える高級ガラスエレベーター内、浅い被写界深度f/1.4、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "CEO「君、泣いてたよね」", "speaker": "CEO", "emotion": "衝撃"},
            {"scene_number": 2, "duration": 5, "description": "肩越しショット（OTS）、女性がスーツ姿の長身男性を見上げる、エレベーター天井の暖色LEDが琥珀色に二人を照らす、女性の目が驚きで見開かれピントが合う、男性は背中のみ、50mmレンズ、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "ナレーション「エレベーターで隣に立った男が、そう言った」主人公「え...誰...？」", "speaker": "主人公+ナレーション", "emotion": "動揺"},
            {"scene_number": 3, "duration": 6, "description": "女性の顔クローズアップ、涙が右頬を一筋つたう、拡散した窓光がソフトに包む、背景に東京の夜景ボケ、下降するガラスエレベーター、唇が微かに震える、85mmレンズf/1.2、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "ナレーション「誰にも見せたことのない涙を、この人は見ていた」", "speaker": "ナレーション", "emotion": "切なさ"},
            {"scene_number": 4, "duration": 6, "description": "ミディアムショット、男性が微かに口角を上げて微笑む、夕暮れの都市スカイラインからの逆光がリムライトとして髪を縁取る、ダークネイビーの三つ揃えスーツ、目元に温かさ、35mmレンズ、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "CEO「大丈夫じゃなくていい」", "speaker": "CEO", "emotion": "温かさ"},
            {"scene_number": 5, "duration": 6, "description": "エクストリームクローズアップ、女性の細い手が微かに震える、マクロレンズ、朝の柔らかい自然光が左から差し込む、背景にモダンなオフィスロビーの大理石床がボケる、指先の緊張感、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "主人公「...なんで、そんなこと言うんですか」", "speaker": "主人公", "emotion": "緊張"},
            {"scene_number": 6, "duration": 5, "description": "ローアングルから見上げる、受付前に立つ男性のシルエット、冷たい青白い蛍光灯のオフィス照明、ガラスパーティションに映る女性の驚いた表情、胸元のCEO名札にピントが合う、24mm広角レンズ、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "ナレーション「翌朝、出社した彼女の目の前に、昨日の男が立っていた。名札には『CEO』の文字」続く...", "speaker": "ナレーション", "emotion": "衝撃"},
        ]
    }
