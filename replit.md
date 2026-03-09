# CEOの扉 - AI Short Drama Generator

## Overview
「CEOの扉」は、人生を変える出会いを描くショートドラマチャンネル。
普通の女性が謎のCEOと出会い、仕事・恋愛・成長・成功・運命が動き出す。
1話45秒の縦動画（9:16, 1080x1920）をYouTube Shorts/TikTok向けに自動生成。
1シリーズ30話構成 × シリーズ形式で公開。毎日3本自動生成。

## Architecture
- **Backend**: Python + FastAPI
- **Database**: PostgreSQL (Neon - NEON_DATABASE_URL)
- **AI Script**: Claude API (シリーズテーマ生成, エピソードテーマ生成, 脚本生成, 改善分析)
- **Image Generation**: Luma Photon → Stability AI SD3 → Pollinations.ai（フォールバック順） (キャラクター画像 + サムネイル + シーン画像)
- **Video Generation**: シーンごとにLuma Photon/Pollinationsで固有画像生成 → Kling AI image2video → Luma Dream Machine Ray-2（フォールバック）→ Ken Burns → FFmpeg（編集・結合）
- **Audio**: ElevenLabs API eleven_v3 (マルチボイス・高感情表現 - stability低め+style高め)
- **YouTube**: YouTube Data API v3 (Shorts投稿/分析)
- **TikTok**: Playwright RPA (ブラウザ自動操作で動画投稿、Cookie認証)
- **Auth**: PBKDF2-SHA256 + JWT
- **Color Theme**: Turquoise (#00897b)
- **Admin**: info@sinjapan.jp / Kazuya8008
- **Scheduler**: APScheduler with JST timezone (日本時間)

## Channel Concept
- チャンネル名: CEOの扉
- コンセプト: 普通の女性が謎のCEOと出会い、仕事・恋愛・成長・成功・運命が動き出す
- 1シリーズ30話（30話完結したら自動で新シリーズ作成）
- ジャンル: CEOドラマ（固定）
- タイトル形式: `CEOの扉 | {シリーズ名} 第{話数}話「{サブタイトル}」`
- 投稿説明文: チャンネル名 + シリーズ名 + サブタイトル + CTA + ハッシュタグ

## Pipeline (2-Step Mode + Full Auto)
- **脚本だけ生成**: テーマ+脚本生成 → 脚本編集(手動) → 動画生成
- **全自動生成**: テーマ→脚本→画像→Kling→音声→FFmpeg→投稿 の全自動
- 新API: POST /api/generate-script (脚本のみ), POST /api/generate-video/{id} (動画のみ), PUT /api/dramas/{id}/script (脚本編集)
- パイプラインはスレッドロック(pipeline_lock)で保護。手動実行とスケジューラの同時実行を防止
- 字幕: SRT形式で各シーンのナレーションから自動生成 → FFmpegで動画に焼き込み（Noto Serif CJK JP明朝体, FontSize=18, 半透明黒背景, MarginV=80, max 18文字/チャンク）
- BGM: ambient_sleep.mp3 をナレーションと15%ボリュームでミックス（ループ再生）
- Kling API: 各シーンごとにシーン描写から固有画像を生成(Luma Photon→Pollinations)→その画像をimage2videoに送信。失敗時はtext2video→Luma動画→Ken Burnsとフォールバック
- 字幕: narrationフィールドのみ使用（descriptionフォールバック廃止）。英語テキストは自動除去
- サムネイル: 温かい雰囲気のロマンスドラマ風（ホラー風を防止）

## Script Format
- セリフ中心の対話形式（ナレーションは最小限）
- 形式: 話者名「セリフ」（例: CEO「君、泣いてたよね」）
- 各シーンにspeakerフィールドあり（主人公/CEO/ナレーション等）
- キャラクター情報がある場合、脚本プロンプトに【登場キャラクター】として反映

## Series System
- `series` テーブルでシリーズを管理
- 1シリーズ30話。30話完結時に自動で新シリーズ生成
- 新シリーズ作成時にClaudeでシリーズテーマ（名前、概要、あらすじ、主人公設定、CEO設定）を自動生成
- ストーリーアーク: 序盤(1-3話) → 前半(4-10話) → 中盤(11-20話) → 後半(21-27話) → 終盤(28-30話)

## AI Prompt Design
- シリーズテーマ: キャラ心理の深掘り + 30話エンジン + サブキャラ設計 + 秘密の仕込み + ストーリーアーク設計
- エピソードテーマ: 10段階のアークヒント + フックの方程式(ダメ例→良い例→最高例) + 感情アーク + 前回脚本の文脈
- 脚本: 映画脚本技法(show-don't-tell) + カメラワーク指示 + ライティング + 表情演技指示 + 感情ビートマッピング
- 改善AI: データアナリスト兼クリエイティブディレクター視点の分析(6項目: 分析/高パフォーマンスパターン/フック改善/ストーリー改善/タイトル改善/エンゲージメント施策)
- 投稿に前回エピソードのデータを時系列順で渡して連続性を確保

## Video Specs
- Aspect: 9:16 vertical
- Resolution: 1080x1920
- Duration: 45 seconds
- Structure: 0-2s hook → 2-35s story → 35-45s twist + "続く..."
- Scenes: 6-8 scenes × ~6 seconds each
- Auto-schedule (JST): 10:00, 15:00, 21:00 (3 videos/day)

## Project Structure
```
main.py                          # FastAPI entry point (port 5000, JST scheduler)
app/
  api/
    auth.py                      # Authentication (JWT + PBKDF2)
    routes.py                    # Web routes and API endpoints
  services/
    pipeline.py                  # Full drama generation pipeline with series management
    ai/
      theme_generator.py         # Claude AI series + episode theme generation
      story_generator.py         # Claude AI script generation (CEO drama focused)
      improvement_ai.py          # Claude AI analytics improvement
    video/
      kling_service.py           # Kling API scene video generation (image2video + text2video, JWT auth, 10min timeout)
      luma_service.py            # Luma Labs Dream Machine API (Photon画像/Ray-2動画)
      scene_generator.py         # Luma → Pollinations.ai + FFmpeg Ken Burns scene video generation
      audio_generator.py         # ElevenLabs multi-voice generation (美咲=Lily/涼介=George/ナレーション=Sarah)
      video_generator.py         # FFmpeg video editing/concat
      image_generator.py         # Stability AI SD3 character + thumbnail generation
    youtube/
      youtube_service.py         # YouTube upload and analytics
    tiktok/
      tiktok_rpa.py              # TikTok Playwright RPA upload (Cookie-based browser automation)
      tiktok_service.py          # TikTok API service (legacy, replaced by RPA)
    analytics_collector.py       # YouTube analytics collection
  db/
    database.py                  # PostgreSQL (series, dramas, ai_logs, admin_users, settings)
  templates/
    base.html                    # Base layout with navbar (CEOの扉 branding)
    login.html                   # Login page
    dashboard.html               # Dashboard with series progress + stats + 4 tabs
    dramas.html                  # Drama list
    drama_detail.html            # Drama detail view
    generate.html                # 2-step generation: script-only + edit → video, or full auto
    characters.html              # Character management (CRUD + image upload + voice ID)
    settings.html                # API settings + usage (6 service cards)
  static/
    css/style.css                # Turquoise theme
    scenes/                      # Generated scene videos
    audio/                       # Generated narration audio
    videos/                      # Final drama videos
    characters/                  # Character reference images
    thumbnail/                   # Drama thumbnails
```

## Database Tables
- `series` - id, series_number, name, description, synopsis, total_episodes(30), current_episode, status(active/completed), created_at
- `dramas` - id, title, genre, theme, script, scene_count, video_url, thumbnail_url, youtube_id, tiktok_id, views, likes, status(draft/script_ready/generating/ready/published), episode_number, series_id, series_episode, created_at
- `characters` - id, name, role(主人公/CEO/サブキャラ/ライバル/ナレーション), description, voice_id, image_path, series_id, created_at
- `ai_logs` - id, drama_id, step, prompt, response, created_at
- `admin_users` - id, username, password_hash, created_at
- `settings` - key, value

## Environment Variables Required
- `SESSION_SECRET` - Session encryption key
- `ANTHROPIC_API_KEY` - Claude API key
- `ELEVENLABS_API_KEY` - ElevenLabs API key
- `KLING_API_KEY` - Kling AI API key (scene video generation)
- `STABILITY_API_KEY` - Stability AI API key (character images + thumbnails)
- `TIKTOK_CLIENT_KEY` - TikTok Client Key (参考用、RPA方式では不要)
- `TIKTOK_CLIENT_SECRET` - TikTok Client Secret (参考用、RPA方式では不要)
- `YOUTUBE_OAUTH_CLIENT_ID` - Google OAuth Client ID
- `YOUTUBE_OAUTH_CLIENT_SECRET` - Google OAuth Client Secret
- `YOUTUBE_OAUTH_REFRESH_TOKEN` - OAuth refresh token
- `ADMIN_USERNAME` - Admin login (info@sinjapan.jp)
- `ADMIN_PASSWORD` - Admin password (Kazuya8008)
- `NEON_DATABASE_URL` or `DATABASE_URL` - PostgreSQL connection string

## Workflow
- `Start application` runs `python main.py` on port 5000
