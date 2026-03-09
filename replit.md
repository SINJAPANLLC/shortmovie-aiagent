# Short Movie AI AGENT SIN JAPAN

## Overview
AIショートドラマ自動生成システム。45秒の縦動画（9:16, 1080x1920）をYouTube Shorts/TikTok向けに自動生成。
ジャンル: 恋愛, 浮気, 復讐, CEOドラマ, 怖い話。

## Architecture
- **Backend**: Python + FastAPI
- **Database**: PostgreSQL (Neon - NEON_DATABASE_URL)
- **AI Script**: Claude API (テーマ生成, 脚本生成, 改善分析)
- **Image Generation**: Stability AI / Stable Diffusion SD3 (キャラクター画像 + サムネイル)
- **Video Generation**: Kling API (各シーン動画生成) + FFmpeg (編集・結合)
- **Audio**: ElevenLabs API (ナレーション音声生成)
- **YouTube**: YouTube Data API v3 (Shorts投稿/分析)
- **TikTok**: TikTok Content Posting API v2 (動画投稿/分析)
- **Auth**: PBKDF2-SHA256 + JWT
- **Color Theme**: Turquoise (#00897b)
- **Admin**: info@sinjapan.jp / Kazuya8008

## Pipeline (10 Steps)
0. 初期化 → 1. テーマ生成(Claude) → 2. 脚本生成(Claude) → 3. 画像生成(Stable Diffusion) → 4. シーン分割 → 5. 動画シーン生成(Kling) → 6. 音声生成(ElevenLabs) → 7. 動画編集(FFmpeg) → 8. 投稿(YouTube/TikTok) → 9. 完了

## Video Specs
- Aspect: 9:16 vertical
- Resolution: 1080x1920
- Duration: 45 seconds
- Scenes: 6-8 scenes × ~6 seconds each
- Auto-schedule: 10:00, 15:00, 21:00 (3 videos/day)

## Project Structure
```
main.py                          # FastAPI entry point (port 5000)
app/
  api/
    auth.py                      # Authentication (JWT + PBKDF2)
    routes.py                    # Web routes and API endpoints
  services/
    pipeline.py                  # Full drama generation pipeline
    ai/
      theme_generator.py         # Claude AI theme generation
      story_generator.py         # Claude AI script generation (6-8 scenes)
      improvement_ai.py          # Claude AI analytics improvement
    video/
      kling_service.py           # Kling API scene video generation
      audio_generator.py         # ElevenLabs narration generation
      video_generator.py         # FFmpeg video editing/concat
      image_generator.py         # Stability AI SD3 character + thumbnail generation
    youtube/
      youtube_service.py         # YouTube upload and analytics
    tiktok/
      tiktok_service.py          # TikTok Content Posting API v2 upload + analytics
    analytics_collector.py       # YouTube analytics collection
  db/
    database.py                  # PostgreSQL (dramas, ai_logs, admin_users, settings)
  templates/
    base.html                    # Base layout with navbar
    login.html                   # Login page
    dashboard.html               # Dashboard with stats + pipeline + 4 tabs
    dramas.html                  # Drama list
    drama_detail.html            # Drama detail view
    generate.html                # Manual generation page (8-step flow)
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
- `dramas` - id, title, genre, theme, script, scene_count, video_url, thumbnail_url, youtube_id, tiktok_id, views, likes, status, episode_number, created_at
- `ai_logs` - id, drama_id, step, prompt, response, created_at
- `admin_users` - id, username, password_hash, created_at
- `settings` - key, value

## Environment Variables Required
- `SESSION_SECRET` - Session encryption key
- `ANTHROPIC_API_KEY` - Claude API key
- `ELEVENLABS_API_KEY` - ElevenLabs API key
- `KLING_API_KEY` - Kling AI API key (scene video generation)
- `STABILITY_API_KEY` - Stability AI API key (character images + thumbnails)
- `TIKTOK_ACCESS_TOKEN` - TikTok Content Posting API access token
- `YOUTUBE_OAUTH_CLIENT_ID` - Google OAuth Client ID
- `YOUTUBE_OAUTH_CLIENT_SECRET` - Google OAuth Client Secret
- `YOUTUBE_OAUTH_REFRESH_TOKEN` - OAuth refresh token
- `ADMIN_USERNAME` - Admin login (info@sinjapan.jp)
- `ADMIN_PASSWORD` - Admin password (Kazuya8008)
- `NEON_DATABASE_URL` or `DATABASE_URL` - PostgreSQL connection string

## Workflow
- `Start application` runs `python main.py` on port 5000
