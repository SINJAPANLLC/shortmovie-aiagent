import os
import time
import logging
import anthropic
from app.db.database import save_ai_log, get_setting

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 30

DEFAULT_VIDEO_MINUTES = 10
CHARS_PER_MINUTE = 350

MAX_CHARS_PER_PART = 4000

STYLE_RULES = """【絶対に守るルール】
- 全文を日本語のみで書くこと。英語やカタカナ語（外来語）は一切使わないでください
- 「」（カギカッコ）によるセリフや会話は一切入れないでください。すべて地の文（ナレーション）で書いてください
- 物語の最初から最後まで、同じ文体・同じトーンを維持してください。後半で文体が変わったり、説明的になったりしないでください
- 箇条書き、番号付きリスト、見出し、タイトルは一切不要です。本文のみを出力してください
- 「あなた」という二人称は使わないでください。一人称の視点か、三人称の淡々とした語りで統一してください

【文体と品質】
- 刺激の少ない穏やかな内容
- 安心感と静けさを重視した語り口
- ゆっくりとした、やわらかい文章のリズム
- 自然の音、匂い、温度、手触りなど五感の描写を丁寧に
- 段落ごとに適度な間を設け、急がない
- 難しい漢字は避け、ひらがなを多めに使う
- 同じ表現や同じ文末の繰り返しを避け、表現に変化をつける
- 感嘆文（「なんと〜でしょう」等）は使わない"""


def _noop(step, msg):
    pass


def _call_claude(client, prompt, progress_callback, step_label=""):
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
                progress_callback(3, f"API エラー{step_label} — {delay}秒後にリトライ ({attempt+1}/{MAX_RETRIES})")
                time.sleep(delay)
            else:
                raise
    return message.content[0].text


def _generate_single(client, theme, theme_description, target_chars, video_minutes, progress_callback):
    prompt = f"""あなたは睡眠用朗読動画のストーリー作家です。リスナーが聴きながら自然に眠りに落ちる、やさしい物語を書いてください。

テーマ: {theme}
{f'説明: {theme_description}' if theme_description else ''}

{STYLE_RULES}

【構成（全体を通して均一なペースで）】
① 導入 — 穏やかな場面設定。場所の雰囲気、空気感、静けさを丁寧に描写する
② 情景描写 — 美しい風景をゆったりと描く。光、風、水、草木などの自然を細かく描写する
③ 小さな出来事 — 些細だが心温まるエピソード。派手な展開は不要
④ さらなる情景 — 場面を少しずつ移しながら、新しい風景や感覚を丁寧に描く
⑤ 眠りへの誘導 — 呼吸がゆっくりになり、体の力が抜けていく描写。意識がやわらかく遠のいていく自然な終わり方

【文字数】
必ず{target_chars}文字以上で書いてください。各セクションを急がず、ひとつひとつの情景をじっくり丁寧に描写して文字数を確保してください。無理に内容を詰め込むのではなく、ひとつの場面を深く掘り下げてください。"""

    progress_callback(3, f"ストーリーを生成中（目標: 約{video_minutes}分/{target_chars}文字）...")
    return _call_claude(client, prompt, progress_callback), prompt


def _generate_multipart(client, theme, theme_description, target_chars, video_minutes, num_parts, progress_callback):
    chars_per_part = target_chars // num_parts
    parts = []
    full_prompt_log = ""

    for i in range(num_parts):
        part_num = i + 1
        is_first = (i == 0)
        is_last = (i == num_parts - 1)

        if is_first:
            structure = """【このパートの構成】
① 導入 — 穏やかな場面設定。場所の雰囲気、空気感、静けさを丁寧に描写する
② 情景描写 — 美しい風景をゆったりと描く。光、風、水、草木などの自然を細かく描写する
③ 小さな出来事の始まり — 些細だが心温まるエピソードの導入

物語は途中で終わる形にしてください。この後に続きがあります。結末や眠りへの誘導は書かないでください。"""
        elif is_last:
            structure = """【このパートの構成】
前のパートの続きとして自然につながる形で始めてください。
① さらなる情景 — 新しい風景や感覚を丁寧に描く
② 眠りへの誘導 — 呼吸がゆっくりになり、体の力が抜けていく描写。意識がやわらかく遠のいていく自然な終わり方

物語の最終パートです。穏やかに完結させてください。"""
        else:
            structure = """【このパートの構成】
前のパートの続きとして自然につながる形で始めてください。
① 情景の展開 — 場面を少しずつ移しながら、新しい風景や感覚を丁寧に描く
② 小さな出来事 — 穏やかなエピソードを静かに展開する

物語は途中で終わる形にしてください。この後に続きがあります。結末や眠りへの誘導は書かないでください。"""

        previous_text = ""
        if parts:
            last_part = parts[-1]
            last_500 = last_part[-500:]
            previous_text = f"\n【前のパートの末尾（これに自然につなげてください）】\n{last_500}\n"

        prompt = f"""あなたは睡眠用朗読動画のストーリー作家です。長編の睡眠物語のパート{part_num}/{num_parts}を書いてください。

テーマ: {theme}
{f'説明: {theme_description}' if theme_description else ''}

{STYLE_RULES}
{previous_text}
{structure}

【文字数】
このパートは必ず{chars_per_part}文字以上で書いてください。急がず、ひとつひとつの情景をじっくり丁寧に描写してください。
前のパートと同じ文体・トーンを必ず維持してください。"""

        progress_callback(3, f"ストーリー生成中（パート {part_num}/{num_parts}、目標: 約{video_minutes}分）...")
        logger.info(f"Generating story part {part_num}/{num_parts} (target: {chars_per_part} chars)")

        part_text = _call_claude(client, prompt, progress_callback, step_label=f"（パート{part_num}）")
        parts.append(part_text)

        if part_num == 1:
            full_prompt_log = prompt

        progress_callback(3, f"パート {part_num}/{num_parts} 完了: {len(part_text)}文字")
        logger.info(f"Part {part_num}/{num_parts} generated: {len(part_text)} characters")

        if not is_last:
            time.sleep(2)

    story_text = "\n\n".join(parts)
    return story_text, full_prompt_log


def generate_story(theme: str, theme_description: str = "", video_id=None, progress_callback=None):
    if progress_callback is None:
        progress_callback = _noop

    video_minutes = int(get_setting("video_minutes", DEFAULT_VIDEO_MINUTES))
    target_chars = video_minutes * CHARS_PER_MINUTE

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=300.0)

    logger.info(f"Generating story for theme: {theme} (target: {target_chars} chars, {video_minutes} min)")

    if target_chars <= MAX_CHARS_PER_PART:
        story_text, prompt_log = _generate_single(client, theme, theme_description, target_chars, video_minutes, progress_callback)
    else:
        num_parts = (target_chars + MAX_CHARS_PER_PART - 1) // MAX_CHARS_PER_PART
        num_parts = max(2, min(num_parts, 8))
        logger.info(f"Using multi-part generation: {num_parts} parts")
        progress_callback(3, f"長編モード: {num_parts}パートに分けて生成します")
        story_text, prompt_log = _generate_multipart(client, theme, theme_description, target_chars, video_minutes, num_parts, progress_callback)

    if video_id:
        save_ai_log(video_id, "ストーリー生成", prompt_log, story_text[:1000] + "..." if len(story_text) > 1000 else story_text)

    progress_callback(3, f"ストーリー生成完了: {len(story_text)}文字")
    logger.info(f"Story generated: {len(story_text)} characters")
    return story_text
