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

    prompt = f"""あなたは日本のショートドラマ業界で大ヒットを連発する天才プロデューサーです。
TikTokとYouTube Shortsで数百万再生を叩き出すドラマを作ってきました。

チャンネル「CEOの扉」の新シリーズ（シリーズ{series_number}）を企画してください。
{prev_context}

【チャンネルの強み】
視聴者層は20〜35歳の女性。「推し」になれるCEOキャラと、感情移入できる等身大のヒロインが鍵。
視聴者は「自分だったらどうする？」と考えながら見ている。

【シリーズ設計の鉄則】
1. ヒロインに「共感できる弱さ」と「隠れた強さ」の両方を持たせる
2. CEOに「表の顔」と「本当の姿」のギャップを作る（冷徹だが実は深い傷を抱えている等）
3. 二人の間に「どうしても一緒にいられない理由」を設定する（これが30話を引っ張るエンジン）
4. サブキャラ（ライバル、親友、元カノ/元カレ）を最低2人は設定する
5. 中盤で「実はこうだった」という大きな秘密の暴露を仕込む
6. 序盤3話で視聴者を掴み、10話目と20話目に大きな転換点を置く

【ストーリーの黄金パターン】
序盤(1-5話): 運命的な出会い → 反発しながらも惹かれ合う → 最初の事件
前半(6-12話): 距離が縮まる → ライバル登場 → 主人公の成長
中盤(13-20話): 秘密の暴露 → 関係の危機 → それでも求め合う
後半(21-27話): 最大の障害 → 別れの危機 → 主人公の覚悟
終盤(28-30話): クライマックス → 感動の再会/決断 → 余韻のあるラスト

【設定のリアリティ】
- ヒロインの職業は具体的に（「OL」ではなく「渋谷のIT企業でSNSマーケティングを担当する26歳」等）
- CEOの会社も具体的に（「不動産テック」「AIスタートアップ」「老舗ホテルチェーン」等）
- 二人が出会う場所・状況も具体的でドラマチックに

以下のJSON形式で返してください:
{{
    "series_name": "シリーズのサブタイトル（感情に刺さる短いフレーズ。例: 嘘の温度、0時のシンデレラ）",
    "series_description": "シリーズ全体の概要（視聴者が思わずタップしたくなる2-3文）",
    "synopsis": "30話分のストーリーライン（序盤・前半・中盤・後半・終盤の流れを8-10文で）",
    "heroine_setting": "主人公の詳細設定（年齢、職業、性格、過去のトラウマ、夢、弱さと強さ）",
    "ceo_setting": "CEOの詳細設定（年齢、会社の業種、表の性格、本当の性格、秘密、弱さ）",
    "sub_characters": "サブキャラ2-3人の設定（関係性、役割、動機）",
    "main_conflict": "メインの葛藤（なぜ二人は簡単に一緒になれないのか — 具体的に）",
    "secret_reveal": "中盤で暴露される大きな秘密の内容"
}}"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
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
        "series_name": "嘘の温度",
        "series_description": "渋谷のカフェで出会った男は、翌日、彼女が転職した会社のCEOだった。「昨日の君の笑顔が忘れられない」——でもこの人には、誰にも言えない秘密がある。",
        "synopsis": "広告代理店を辞めたばかりの咲良(26)は、カフェで隣に座った男と意気投合する。翌日、転職先のAIスタートアップに出社すると、その男がCEOの霧島蓮(32)だった。蓮は社内では冷徹なカリスマとして知られるが、咲良の前では別の顔を見せ始める。だが蓮には、会社を守るために結んだ政略婚約という秘密があった。",
        "heroine_setting": "佐藤咲良、26歳。元広告代理店勤務。明るく行動力があるが、前の職場でのパワハラがトラウマ。人の善意を素直に受け取れない一面がある。",
        "ceo_setting": "霧島蓮、32歳。AIスタートアップCEO。表向きは冷徹で完璧主義。実は幼少期に母を亡くし、人に頼ることができない。会社のために犠牲を払い続けている。",
        "sub_characters": "白石美月(蓮の婚約者、財閥令嬢、実は蓮を本気で愛している)、田中陽太(咲良の幼馴染、密かに咲良を想っている)、黒田専務(蓮の右腕だが裏で会社を狙っている)",
        "main_conflict": "蓮は会社の資金調達のために財閥令嬢との婚約を解消できない。咲良は「また利用されるのではないか」という恐怖から踏み込めない。",
        "secret_reveal": "蓮の婚約は実は形だけのもので、美月は蓮の亡き姉の親友だった。蓮は姉の遺志を継いでこの会社を作った。"
    }


def generate_theme(previous_themes=None, genre=None, drama_id=None, series_info=None, previous_scripts=None, previous_episodes=None):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), timeout=120.0)

    if series_info is None:
        series_info = get_active_series()

    series_context = ""
    if series_info:
        series_context = f"""
【現在のシリーズ】
シリーズ名: {series_info.get('name', '')}
概要: {series_info.get('description', '')}
あらすじ: {series_info.get('synopsis', '')}
現在の話数: 第{series_info.get('current_episode', 0) + 1}話 / 全{series_info.get('total_episodes', 30)}話"""

    prev_episodes = ""
    if previous_episodes and len(previous_episodes) > 0:
        recent = previous_episodes[-5:]
        prev_episodes += "\n【直近のエピソード（時系列順 — この続きを作ってください）】\n"
        for ep in recent:
            prev_episodes += f"  第{ep.get('episode', '?')}話「{ep.get('title', '')}」: {ep.get('theme', '')[:100]}\n"
    elif previous_themes:
        prev_episodes += "\n【直近のエピソードテーマ】\n"
        for i, t in enumerate(previous_themes[-5:]):
            prev_episodes += f"  {i+1}. {t}\n"

    prev_script_context = ""
    if previous_scripts and len(previous_scripts) > 0:
        last_script = previous_scripts[-1]
        prev_script_context = f"\n【前回の脚本（ストーリーを自然に続けてください）】\n{last_script[:500]}"

    episode_num = (series_info.get('current_episode', 0) + 1) if series_info else 1
    total_eps = series_info.get('total_episodes', 30) if series_info else 30

    if episode_num == 1:
        arc_hint = """【第1話の鉄則】
- 最初の2秒で視聴者の心を鷲掴みにする衝撃シーンから始める
- ヒロインの日常→CEOとの運命的な出会い→「えっ!?」という衝撃のラスト
- 視聴者に「この二人の関係がどうなるのか見たい！」と思わせる
- 具体的な状況設定（場所、時間帯、天気まで指定）"""
    elif episode_num <= 3:
        arc_hint = """【序盤の鉄則（出会い～接近）】
- 二人の距離が少しずつ縮まる「ドキッ」とするシーン
- でも簡単にはいかない障害や誤解を入れる
- 視聴者が「もっと仲良くなって！」と応援したくなる展開
- 毎話「えっ、そうなの!?」という小さな驚きを入れる"""
    elif episode_num <= 10:
        arc_hint = """【前半の鉄則（葛藤と接近）】
- 仕事を通じて認め合う → でも素直になれない
- ライバルや邪魔者の登場で緊張感を上げる
- 二人きりになる特別なシーン（残業、出張、偶然の再会）
- 「え、この人そんな一面あったの？」というギャップ萌え"""
    elif episode_num <= 15:
        arc_hint = """【中盤前半の鉄則（関係深化と不穏な影）】
- 二人の関係が一気に深まるターニングポイント
- 同時に、秘密が少しずつ見え隠れし始める
- 視聴者が「嫌な予感がする...」とハラハラする展開
- 第三者の視点から二人の関係を揺さぶる"""
    elif episode_num <= 20:
        arc_hint = """【中盤後半の鉄則（秘密暴露と危機）】
- 隠されていた秘密が暴露される衝撃回
- 信頼が崩壊 → 視聴者が泣きたくなるような切ない展開
- 「嘘だったの？全部？」という絶望からの...でも忘れられない
- 感情の振り幅を最大にする（幸福の頂点→どん底）"""
    elif episode_num <= 25:
        arc_hint = """【後半の鉄則（試練と成長）】
- 離れてしまった二人がそれぞれの場所で成長する
- ヒロインが自分の力で立ち上がる姿を見せる
- CEOも変わろうとする決意を見せる
- 「もう会えないかもしれない」という切迫感"""
    elif episode_num <= 28:
        arc_hint = """【クライマックス前の鉄則（最大の障害）】
- 二人の前に立ちはだかる最大の障害
- すべてを失う覚悟で選択を迫られる
- 視聴者が手に汗握る緊迫感
- 「お願い、うまくいって！」と祈りたくなる展開"""
    else:
        arc_hint = """【終盤の鉄則（決着とカタルシス）】
- すべてが収束に向かう
- 視聴者が待ち望んだ瞬間（告白、再会、選択）
- 感動で涙が出るような演出
- 余韻が残り、「このシリーズ好きだった」と思える終わり方"""

    prompt = f"""あなたは、TikTokとYouTube Shortsで累計1億再生を超えるショートドラマを作り続けてきた天才脚本プロデューサーです。
視聴者の心理を完璧に理解し、「続きが気になって仕方ない」を科学的に設計できます。
{series_context}

第{episode_num}話（全{total_eps}話中）のテーマを作ってください。
{prev_episodes}
{prev_script_context}

{arc_hint}

【バズる45秒ドラマの方程式】
1. 冒頭フック（0-2秒）: 感情が動く一言。疑問を残す。「なぜ？」と思わせる。
   ダメな例: 「ある日、彼女は会社に行った」（退屈）
   良い例: 「目の前の男は、昨夜キスした相手だった」（衝撃＋謎）
   最高の例: 「『君を愛してる』——その言葉が、全部嘘だと知ったのは、3秒後だった」（感情＋衝撃＋時間制限）

2. ストーリー（2-35秒）: 1つの出来事に集中。「起承転」のみ。「結」は次回に持ち越す。
   感情のジェットコースター：安心→不安→衝撃、の繰り返し

3. 引き（35-45秒）: 視聴者が「えっ!? 次！次見たい！」と思わずにいられない終わり方
   ダメな例: 「続く...」だけ（弱い）
   良い例: 新情報の暴露直前で切る（「実は彼は——」で終わる）
   最高の例: 感情と新情報のダブルパンチ（涙を流しながら「嘘つき」と言った瞬間、背後にもう一人立っていた——）

以下のJSON形式で返してください:
{{
    "theme": "今話のテーマの概要（具体的な出来事とキャラの感情変化を3-4文で。あいまいな概要ではなく、何が起きて何を感じるかまで書く）",
    "title_base": "サブタイトル（感情に刺さる短いフレーズ。例: 嘘の理由、触れられない距離、0時の本音）",
    "hook": "冒頭2秒のセリフまたはナレーション（視聴者が立ち止まる一文。具体的に書く）",
    "twist": "ラスト10秒の引き（具体的に何が起きるか。感情の種類も指定：衝撃、切なさ、怒り、期待等）",
    "emotional_arc": "この話の感情の流れ（例: 期待→動揺→衝撃→切なさ）"
}}"""

    message = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
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

    return {"theme": "運命の出会い", "title_base": "運命の出会い", "hook": "「君、泣いてたよね」——エレベーターで隣に立った男が、そう言った。", "twist": "翌日、その男が新しい上司だと知る。しかも、彼女の机の上には一輪の花が置かれていた。（衝撃＋期待）", "emotional_arc": "孤独→驚き→ドキドキ→衝撃", "genre": "CEOドラマ"}
