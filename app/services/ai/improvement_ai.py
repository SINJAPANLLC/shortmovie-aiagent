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
        dramas_summary += f"- {d.get('title', 'N/A')} | 再生数: {d.get('views', 0)} | いいね: {d.get('likes', 0)} | シリーズ: {d.get('series_id', 'N/A')}\n"

    prompt = f"""あなたは、YouTube Shorts / TikTokのショートドラマチャンネル「CEOの扉」の専属データアナリスト兼クリエイティブディレクターです。

チャンネルコンセプト: 普通の女性が謎のCEOと出会い、仕事・恋愛・成長・運命が動き出すショートドラマ。
1話45秒、1シリーズ30話構成。

【投稿データ】
{dramas_summary if dramas_summary else "まだ動画データがありません。"}

以下の観点から分析し、具体的な改善提案をしてください:

1. 視聴維持率に影響する要素（冒頭フックの強さ、ストーリーテンポ）
2. エンゲージメントパターン（どんな展開がいいねを集めるか）
3. シリーズ構成の改善点（視聴者が離脱するポイントはどこか）
4. サムネイル・タイトルの最適化
5. 投稿時間・頻度の最適化

以下のJSON形式で回答してください:
{{
    "analysis": "データに基づく分析結果の要約（具体的な数値や傾向に言及）",
    "top_performing_pattern": "最もパフォーマンスが良いエピソードの共通パターン",
    "hook_improvement": "冒頭フックの具体的な改善提案（例文つき）",
    "story_improvement": "ストーリー構成の改善提案",
    "title_improvement": "タイトル・サムネイルの改善提案（具体例つき）",
    "next_theme_recommendation": "次のエピソード/シリーズで試すべきテーマや展開",
    "engagement_tips": "いいね・コメント・シェアを増やすための具体的な施策"
}}"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
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
        "top_performing_pattern": "N/A",
        "hook_improvement": "冒頭2秒で感情が動くセリフを入れる",
        "story_improvement": "45秒の中で感情の落差を大きくする",
        "title_improvement": "「CEOの扉 | シリーズ名 第X話」の形式を維持",
        "next_theme_recommendation": "恋愛×ビジネスの葛藤を描く展開",
        "engagement_tips": "コメント欄で次回の展開予想を促す"
    }
