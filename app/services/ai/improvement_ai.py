import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log

logger = logging.getLogger(__name__)


def analyze_and_improve(dramas_data, drama_id=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    dramas_summary = ""
    for d in dramas_data[:20]:
        dramas_summary += f"- {d.get('title', 'N/A')} | ジャンル: {d.get('genre', 'N/A')} | 再生数: {d.get('views', 0)} | いいね: {d.get('likes', 0)}\n"

    prompt = f"""あなたはYouTube Shorts / TikTokのAIショートドラマチャンネルのアナリストです。

以下の投稿データを分析し、改善提案をしてください。

【投稿データ】
{dramas_summary if dramas_summary else "まだ動画データがありません。"}

以下のJSON形式で回答してください:
{{
    "analysis": "分析結果の要約",
    "best_genre": "最も成績の良いジャンル",
    "improvement_suggestions": "改善提案",
    "next_theme_recommendation": "次の動画テーマの推奨方向性"
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
                logger.warning(f"Claude API error on improvement analysis, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                raise

    response_text = message.content[0].text

    if drama_id:
        save_ai_log(drama_id, "AI改善分析", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except (json.JSONDecodeError, IndexError):
        pass
    return {
        "analysis": "データ不足のため分析できません",
        "best_genre": "N/A",
        "improvement_suggestions": "より多くの動画を投稿してデータを蓄積してください",
        "next_theme_recommendation": "恋愛系のドラマチックなストーリー"
    }
