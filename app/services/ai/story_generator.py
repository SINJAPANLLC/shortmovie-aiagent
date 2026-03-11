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


def _load_saved_templates():
    templates = {"script": "", "image_prompt": "", "video_prompt": "", "character_prompt": ""}
    save_dir = "app/static/saved_contents"
    if not os.path.isdir(save_dir):
        return templates
    files_by_type = {}
    for f in sorted(os.listdir(save_dir), reverse=True):
        if not f.endswith(".txt"):
            continue
        for ttype in ["script", "image_prompt", "video_prompt", "character_prompt"]:
            if f"_{ttype}_" in f and ttype not in files_by_type:
                try:
                    with open(os.path.join(save_dir, f), "r", encoding="utf-8") as fh:
                        content = fh.read().strip()
                        lines = content.split("\n")
                        body_lines = [l for l in lines if not l.startswith("# ")]
                        files_by_type[ttype] = "\n".join(body_lines).strip()
                except Exception:
                    pass
    for k, v in files_by_type.items():
        templates[k] = v
    return templates


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

    templates = _load_saved_templates()
    template_context = ""
    if templates["script"]:
        template_context += f"""
【参考：保存済み脚本テンプレート】
以下の脚本フォーマットを参考にして、同じ形式・品質で新しいエピソードを書いてください：
{templates['script'][:800]}
"""
    if templates["video_prompt"]:
        template_context += f"""
【参考：保存済み動画プロンプトテンプレート】
各シーンの動画プロンプト(video_prompt)は以下のような超詳細な英語プロンプトを生成してください。
シーンごとに1200〜1500文字の詳細な映像指示を含めてください：
{templates['video_prompt'][:1500]}
"""
    if templates["image_prompt"]:
        template_context += f"""
【参考：保存済み画像プロンプトテンプレート】
各シーンの画像プロンプト(image_prompt)は以下の形式で生成してください：
{templates['image_prompt'][:600]}
"""
    if templates["character_prompt"]:
        template_context += f"""
【参考：保存済みキャラクタープロンプト】
キャラクターの外見描写は以下を参考にしてください：
{templates['character_prompt'][:400]}
"""

    prompt = f"""あなたは、日本のショートドラマ業界でトップの脚本家です。
TikTokやYouTube Shortsで何百万回も再生される45秒ドラマを書いてください。

【あなたの脚本の特徴】
1. セリフが自然で生き生きしている（棒読みにならない、感情が込もったセリフ）
2. 1話の中に必ず「ドキッとする瞬間」がある
3. 見終わった後「次どうなるの!?」と思わせる
{series_context}
{prev_script_context}
{characters_context}
{template_context}

テーマ: {theme}
冒頭フック: {hook}
引き/どんでん返し: {twist}
{f'感情の流れ: {emotional_arc}' if emotional_arc else ''}

【45秒の構造 — 3シーン × 15秒】
■ シーン1（0-15秒）: フック＋状況設定 — スクロールを止める一言から始まり、何が起きてるか見せる
■ シーン2（15-30秒）: ドラマの核心 — 感情がぶつかる対話。短い鋭いセリフの応酬、予想外の展開
■ シーン3（30-45秒）: 転換＋引き — 新事実や予想外の展開 + 「えっ!?」で終わる + 続く...

【絶対ルール】
- 必ず3シーンに分割（1シーン = 15秒）
- セリフ中心（全体の80%以上がセリフ）。ナレーションは1〜2文だけ
- 合計150〜200文字
- セリフ形式: 主人公「セリフ」 CEO「セリフ」 ナレーション「状況説明」
- 自然な日本語の会話（「...」や「！」を効果的に使う）
- 各シーンのセリフは15秒分（40〜70文字）
- 最後は必ず「続く...」

【絶対禁止】
- キャラの実名（翔子、蓮、美咲、涼介など）は使用禁止
- 必ず「主人公」「CEO」「ナレーション」の役割名のみ使用
- 説明的な文章（「彼は悲しんだ」→ ×）。代わりに行動で見せる
- 長いナレーション（映像で見せる。言葉で説明しない）

【各シーンのdescription — 日本語の画像プロンプト】
各シーンのdescriptionは、画像生成AI（Luma Photon）への直接指示です。日本語で書いてください。
実写映画のようなリアルな画像を生成するため、以下を必ず含めてください:

■ カメラワーク（シーンごとに必ず変える）
■ レンズ・被写界深度
■ ライティング（シーンごとに必ず変える）
■ 場所・環境の具体的描写
■ 人物の具体的描写
■ 必須タグ: 「フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920」

description は60〜120文字で書いてください。

【各シーンのvideo_prompt — 英語の動画プロンプト（超重要）】
各シーンにvideo_promptフィールドを追加してください。
これはKling AI V3の動画生成に直接渡すプロンプトです。英語で、1200〜1500文字の超詳細な映像指示を書いてください。
以下の要素を必ず含めてください:
- カメラの動き（tracking shot, dolly-in, pan, tilt等）
- キャラクターのアクション・動き・表情の変化
- 環境音・BGM・効果音の指示
- 日本語のセリフ（必ず「」で囲んで含める）例: she says 「セリフ」
- ライティングの変化
- 色彩・グレーディング

【各シーンのimage_prompt — 英語の画像プロンプト】
各シーンにimage_promptフィールドを追加してください。
Luma Photon画像生成に直接渡す英語プロンプトです。1-2文で簡潔に。

【各シーンのcharacters — 登場キャラクター名リスト】
各シーンにcharactersフィールドを追加してください。
そのシーンに登場するキャラクター名のリストです。例: ["田中美咲", "神崎亮"]

以下のJSON形式で返してください:
{{
    "narration": "全体のセリフテキスト（主人公「」CEO「」ナレーション「」形式。150-200文字）",
    "scenes": [
        {{
            "scene_number": 1,
            "duration": 15,
            "description": "日本語の画像プロンプト（60〜120文字）",
            "image_prompt": "English image prompt for Luma Photon (1-2 sentences)",
            "video_prompt": "English video prompt for Kling AI V3 (1200-1500 chars, ultra detailed)",
            "narration": "このシーンのセリフ（主人公「セリフ」CEO「セリフ」等）",
            "speaker": "主人公 or CEO or 主人公+CEO or ナレーション",
            "emotion": "感情（例: 緊張、衝撃、切なさ）",
            "characters": ["キャラ名1", "キャラ名2"]
        }}
    ]
}}"""

    message = None
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8000,
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
            {"scene_number": 1, "duration": 15, "description": "85mmレンズ、エレベーター内でスーツの男性が涙目の女性に話しかけるミディアムショット、琥珀色の間接照明、東京タワーが見えるガラスエレベーター、浅い被写界深度f/1.4、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "CEO「君、泣いてたよね」ナレーション「エレベーターで隣に立った男が、そう言った」主人公「え...誰...？」", "speaker": "CEO+主人公", "emotion": "衝撃", "characters": ["田中美咲", "神崎亮"], "image_prompt": "A man in a suit speaking to a teary-eyed woman inside a glass elevator with Tokyo Tower visible, amber lighting, medium shot, 85mm lens, photorealistic", "video_prompt": "Cinematic medium shot inside a glass elevator..."},
            {"scene_number": 2, "duration": 15, "description": "女性の顔クローズアップ、涙が頬を一筋つたう、窓光がソフトに包む、背景に東京の夜景ボケ、唇が微かに震える、85mmレンズf/1.2、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "ナレーション「誰にも見せたことのない涙を、この人は見ていた」CEO「大丈夫じゃなくていい」主人公「...なんで、そんなこと言うんですか」", "speaker": "CEO+主人公", "emotion": "切なさ", "characters": ["田中美咲", "神崎亮"], "image_prompt": "Close-up of a young Japanese woman with a single tear on her cheek, soft window light, Tokyo night cityscape bokeh background, photorealistic", "video_prompt": "Extreme close-up of woman's face..."},
            {"scene_number": 3, "duration": 15, "description": "ローアングル、オフィスロビーでCEO名札の男性が立つシルエット、青白い蛍光灯、ガラスパーティション越しに驚く女性の反射、24mm広角レンズ、フォトリアリスティック、フィルムグレイン、自然な肌質感、縦型9:16構図、1080x1920", "narration": "ナレーション「翌朝、出社した彼女の目の前に、昨日の男が立っていた。名札には『CEO』の文字」続く...", "speaker": "ナレーション", "emotion": "衝撃", "characters": ["田中美咲", "神崎亮"], "image_prompt": "Low angle shot of a businessman silhouette with CEO name tag in an office lobby, cold fluorescent lighting, surprised woman reflected in glass partition, photorealistic", "video_prompt": "Low angle cinematic shot..."},
        ]
    }
