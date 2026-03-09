import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log

logger = logging.getLogger(__name__)


def analyze_and_improve(videos_data: list, video_id=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    videos_summary = ""
    for v in videos_data:
        videos_summary += f"- タイトル: {v.get('title', 'N/A')}, テーマ: {v.get('theme', 'N/A')}, "
        videos_summary += f"再生数: {v.get('views', 0)}, CTR: {v.get('ctr', 0)}%, "
        videos_summary += f"平均視聴時間: {v.get('watch_time', 0)}分\n"

    prompt = f"""あなたはYouTube睡眠用朗読チャンネルの分析AIです。

以下の動画パフォーマンスデータを分析し、改善提案をしてください：

{videos_summary if videos_summary else "まだ動画データがありません。"}

目標指標:
- CTR: 6%以上
- 平均視聴時間: 15分以上

以下のJSON形式で回答してください:
{{
    "analysis": "分析結果の要約",
    "best_performing_theme": "最も成績の良いテーマ",
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

    if video_id:
        save_ai_log(video_id, "AI改善分析", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except (json.JSONDecodeError, IndexError):
        pass
    return {
        "analysis": "データ不足のため分析できません",
        "best_performing_theme": "N/A",
        "improvement_suggestions": "より多くの動画を投稿してデータを蓄積してください",
        "next_theme_recommendation": "自然をテーマにした穏やかなストーリー"
    }
