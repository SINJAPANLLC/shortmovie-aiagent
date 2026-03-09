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
あなたの書く45秒ドラマは、視聴者が「もう一回見たい」「続き見なきゃ」と中毒になる力を持っています。

その秘密は3つ:
1. 「見せる」脚本を書く（「彼女は悲しかった」ではなく「彼女の手が震えていた」）
2. セリフに人間味がある（「愛してる」ではなく「君がいないと、コーヒーの味がしない」）
3. 感情の落差が激しい（幸せな瞬間→一瞬で崩壊、を45秒で作る）
{series_context}
{prev_script_context}
{characters_context}

テーマ: {theme}
冒頭フック: {hook}
引き/どんでん返し: {twist}
{f'感情の流れ: {emotional_arc}' if emotional_arc else ''}

【45秒ドラマの黄金構造】

■ 0〜2秒: 衝撃フック
- 視聴者がスクロールの手を止める一文
- 感情が動く + 「なぜ？」が生まれる
- セリフかナレーションか映像描写で
例: 「『もう会わない方がいい』——そう言ったのは、彼の方だった」
例: 雨の中、高級車から降りてきた男の目が、彼女を捉えた

■ 2〜8秒: 状況設定（何が起きているか一瞬で分からせる）
- 前回の続きか、新しい場面の導入
- 場所・人物・状況を映像で伝える

■ 8〜25秒: 感情の山を作る（ここがドラマの核心）
- 1つの出来事・1つのシーンに集中する
- 表情、仕草、声のトーンで感情を伝える
- セリフは短く、でも刺さる言葉を選ぶ
- 「心臓が止まりそうな瞬間」を1つ入れる

■ 25〜35秒: 転換点（予想を裏切る）
- 視聴者が「そうなると思ってた」の逆を行く
- 新しい情報、意外な行動、告白、暴露

■ 35〜45秒: 引き（次回への中毒性を最大化）
- 最後の1文で新しい謎を残す
- 感情のピークで「続く...」
- 視聴者が「えっ、ここで終わるの!?」と思う瞬間

【脚本ルール】
- 合計150〜200文字の対話（セリフ中心、ナレーションは最小限）
- 6〜8シーンに分割
- セリフ中心で構成。ナレーションは状況説明の最小限のみ
- セリフは「話者名「セリフ」」の形式で書く（例: 美咲「なんで...ここにいるの？」）
- 説明的な文は書かない（「彼は怒った」→「机を叩いた」）
- 五感で伝える（視覚、聴覚、触覚を使う）
- 最後は必ず「続く...」で終わる
- 1シーンにつき1つの感情に集中する

【映像演出の指示】
各シーンのdescriptionは、動画生成AIが最高の映像を作れるように、
以下を必ず含めてください：

カメラワーク指示:
- "extreme close-up" (感情シーン: 目、唇、手の震え)
- "over-the-shoulder shot" (対話シーン)
- "wide establishing shot" (場面設定)
- "slow dolly in" (緊張が高まるシーン)
- "low angle looking up" (CEOの権力・オーラ)

ライティング指示:
- "golden hour warm backlight" (ロマンティック)
- "cold blue fluorescent office light" (ビジネス・緊張)
- "rain on window with city lights bokeh" (切ないシーン)
- "dramatic side lighting with deep shadows" (秘密・葛藤)
- "soft diffused morning light" (希望・新しい始まり)

表情・演技指示:
- "eyes glistening with unshed tears" (泣きそうだけど我慢)
- "jaw clenched, looking away" (怒りを押し殺す)
- "slight smile that doesn't reach the eyes" (嘘の笑顔)
- "hands trembling while holding phone" (衝撃・動揺)

世界観:
- "luxury penthouse office, floor-to-ceiling windows, city skyline at dusk"
- "rainy Tokyo street, neon reflections on wet asphalt"
- "intimate Italian restaurant, candlelight, wine glasses"
- "modern minimalist apartment, single lamp light"

必ず "vertical 9:16 composition, 1080x1920" を含めてください。

以下のJSON形式で返してください:
{{
    "narration": "全体の対話テキスト（150〜200文字。話者名「セリフ」形式。ナレーションは最小限）",
    "scenes": [
        {{
            "scene_number": 1,
            "duration": 6,
            "description": "映像の詳細説明（英語。カメラワーク+ライティング+表情+場所を含む。40-60 words）",
            "narration": "このシーンの対話/セリフ（話者名「セリフ」形式）",
            "speaker": "話者名（例: 主人公, CEO, ナレーション）",
            "emotion": "このシーンの核心感情（例: 緊張、切なさ、衝撃、期待）"
        }},
        ...
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
