import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log, get_active_series

logger = logging.getLogger(__name__)

CHANNEL_NAME = "CEOの扉"
CHANNEL_CONCEPT = """「CEOの扉」は、人生を変える出会いを描くショートドラマチャンネルです。
ある日、普通の女性が謎のCEOと出会う。
そこから始まる仕事、恋愛、成長、成功、そして運命。
1話45秒のショートドラマをシリーズ形式で公開。"""


def generate_series_theme(series_number, previous_series=None, drama_id=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    prev_context = ""
    if previous_series:
        prev_context = f"\n過去のシリーズ（重複しないでください）:\n{', '.join(previous_series[-10:])}"

    prompt = f"""あなたは「CEOの扉」チャンネルの企画プロデューサーです。

【チャンネルコンセプト】
{CHANNEL_CONCEPT}

新しいシリーズ（シリーズ{series_number}）の企画を作成してください。
1シリーズ30話構成で、以下の要素を含めてください。
{prev_context}

【シリーズの要件】
- 主人公は普通の女性（毎シリーズ違う設定・背景）
- 謎めいたCEOとの出会いから始まる
- 仕事、恋愛、成長、成功、運命のいずれかを軸にしたストーリー
- 30話で完結する大きなストーリーアーク
- 各話は45秒で、必ず続きが気になる「引き」で終わる
- YouTube Shorts / TikTok向けの縦動画ドラマ

以下のJSON形式で返してください:
{{
    "series_name": "シリーズのサブタイトル（例: 運命の再会、秘密の契約）",
    "series_description": "シリーズ全体の概要（2-3文）",
    "synopsis": "30話分の大まかなストーリーライン（5-8文）",
    "heroine_setting": "主人公の設定（職業、性格、背景）",
    "ceo_setting": "CEOの設定（性格、秘密、特徴）",
    "main_conflict": "メインの葛藤・障害"
}}"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if attempt < 2:
                delay = 30 * (attempt + 1)
                logger.warning(f"Claude API error on series theme generation, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                raise

    response_text = message.content[0].text

    if drama_id:
        save_ai_log(drama_id, "シリーズテーマ生成", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except (json.JSONDecodeError, IndexError):
        pass

    return {
        "series_name": "運命の再会",
        "series_description": "カフェで働く普通のOLが、偶然出会った謎のCEOに人生を変えられていく。",
        "synopsis": "主人公はカフェで働くOL。ある日、常連客が大企業のCEOだと知る。仕事のチャンスをもらい、やがて恋に落ちる。しかしCEOには秘密があった。",
        "heroine_setting": "カフェ勤務の26歳OL、明るく前向き",
        "ceo_setting": "30代の若きCEO、冷徹に見えて実は優しい",
        "main_conflict": "身分の違いと、CEOの隠された過去"
    }


def generate_theme(previous_themes=None, genre=None, drama_id=None, series_info=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    if series_info is None:
        series_info = get_active_series()

    series_context = ""
    if series_info:
        series_context = f"""
【現在のシリーズ情報】
シリーズ名: {series_info.get('name', '')}
概要: {series_info.get('description', '')}
あらすじ: {series_info.get('synopsis', '')}
現在の話数: 第{series_info.get('current_episode', 0) + 1}話 / 全{series_info.get('total_episodes', 30)}話"""

    context = ""
    if previous_themes:
        context += f"\n直近のエピソードテーマ（ストーリーの流れを意識して続きを作ってください）:\n"
        for t in previous_themes[-5:]:
            context += f"- {t}\n"

    episode_num = (series_info.get('current_episode', 0) + 1) if series_info else 1
    total_eps = series_info.get('total_episodes', 30) if series_info else 30

    if episode_num <= 3:
        arc_hint = "序盤: 出会いと関係の始まり。視聴者を引き込む衝撃的な展開を。"
    elif episode_num <= 10:
        arc_hint = "前半: 関係が深まりつつ、障害や葛藤が生まれる展開。"
    elif episode_num <= 20:
        arc_hint = "中盤: クライマックスに向けて緊張感が高まる展開。秘密の暴露や裏切りなど。"
    elif episode_num <= 27:
        arc_hint = "後半: 最大の危機。別れの危機や大きな試練。"
    else:
        arc_hint = "終盤: すべてが明らかになり、感動的な結末に向かう展開。"

    prompt = f"""あなたは「CEOの扉」チャンネルの企画プロデューサーです。

【チャンネルコンセプト】
{CHANNEL_CONCEPT}
{series_context}

第{episode_num}話のテーマを生成してください。

【ストーリーアーク】
{arc_hint}
{context}

【ルール】
- 前回の続きとして自然につながるストーリー
- 45秒で衝撃フックから始まり、「続く...」で終わる
- 視聴者が次の話を見たくなる引きの強い展開
- CEOと主人公の関係性の変化を描く

以下のJSON形式で返してください:
{{"theme": "今話のテーマの概要（2-3文）", "title_base": "サブタイトル（短いフレーズ）", "hook": "冒頭2秒の衝撃フック内容", "twist": "最後の引き・どんでん返し内容"}}"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if attempt < 2:
                delay = 30 * (attempt + 1)
                logger.warning(f"Claude API error on theme generation, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                raise

    response_text = message.content[0].text

    if drama_id:
        save_ai_log(drama_id, "テーマ生成", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(response_text[start:end])
            result["genre"] = "CEOドラマ"
            return result
    except (json.JSONDecodeError, IndexError):
        pass

    return {"theme": "運命の出会い", "title_base": "運命の出会い", "hook": "突然の告白", "twist": "衝撃の正体", "genre": "CEOドラマ"}
