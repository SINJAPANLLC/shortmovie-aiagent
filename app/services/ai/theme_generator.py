import os
import json
import time
import logging
import anthropic
from app.db.database import save_ai_log

logger = logging.getLogger(__name__)


def generate_theme(previous_themes=None, analytics_feedback=None, video_id=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    context = ""
    if previous_themes:
        context += f"\n過去に使用したテーマ（重複しないでください）:\n{', '.join(previous_themes)}"
    if analytics_feedback:
        context += f"\n\n分析フィードバック:\n{analytics_feedback}"

    prompt = f"""あなたは睡眠用朗読動画のテーマ生成AIです。

以下の条件でテーマを1つ生成してください：

- 睡眠に適した穏やかなテーマ
- 自然や静かな場所に関連するもの
- リラックスできる雰囲気
- 日本語で短いフレーズ（2〜5語）

例: 夜の森、月の丘、静かな海、星空の村、雨の夜
{context}

以下のJSON形式で返してください:
{{"theme": "テーマ名", "description": "テーマの簡単な説明"}}"""

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

    if video_id:
        save_ai_log(video_id, "テーマ生成", prompt, response_text)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except (json.JSONDecodeError, IndexError):
        pass
    return {"theme": "静かな夜の湖", "description": "月明かりに照らされた静かな湖畔の物語"}
