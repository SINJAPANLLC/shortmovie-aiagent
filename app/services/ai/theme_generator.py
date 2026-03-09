import os
import json
import time
import random
import logging
import anthropic
from app.db.database import save_ai_log

logger = logging.getLogger(__name__)

GENRES = ["恋愛", "浮気", "復讐", "CEOドラマ", "怖い話"]


def generate_theme(previous_themes=None, genre=None, drama_id=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    if not genre:
        genre = random.choice(GENRES)

    context = ""
    if previous_themes:
        context += f"\n過去に使用したテーマ（重複しないでください）:\n{', '.join(previous_themes[-20:])}"

    prompt = f"""あなたはAIショートドラマの企画プロデューサーです。

ジャンル: {genre}

以下の条件で45秒ショートドラマのテーマを1つ生成してください：

- YouTube Shorts / TikTok向けの縦動画ドラマ
- 衝撃的なフックで始まり、どんでん返しで終わる
- 視聴者が続きを見たくなる構成
- 日本語で短いフレーズのタイトル
{context}

タイトル例:
- 社長と秘密の恋
- 彼氏の浮気
- AI彼女の秘密

以下のJSON形式で返してください:
{{"theme": "テーマの概要", "title_base": "タイトルのベース部分", "hook": "冒頭2秒の衝撃フック内容", "twist": "最後のどんでん返し内容"}}"""

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
            result["genre"] = genre
            return result
    except (json.JSONDecodeError, IndexError):
        pass
    return {"theme": "秘密の恋", "title_base": "秘密の恋", "hook": "突然の告白", "twist": "衝撃の真実", "genre": genre}
