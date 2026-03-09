# Short Movie AI AGENT SIN JAPAN

## Overview
睡眠用朗読動画をAIで自動生成しYouTubeへ投稿するシステム。
チャンネル名: おやすみ物語 (3分で眠れるおやすみ物語)

## Architecture
- **Backend**: Python + FastAPI
- **Database**: PostgreSQL (Neon - NEON_DATABASE_URL)
- **AI**: Claude API (テーマ/ストーリー生成, 改善分析)
- **Audio**: ElevenLabs API (音声生成, voice=Yui, model=eleven_v3)
- **Video**: FFmpeg (動画生成, BGM付き)
- **YouTube**: YouTube Data API v3 (投稿/分析)
- **Auth**: PBKDF2-SHA256 + JWT

## Project Structure
```
main.py                          # FastAPI app entry point (port 5000)
app/
  api/
    auth.py                      # Authentication (JWT + PBKDF2)
    routes.py                    # All web routes and API endpoints
  services/
    pipeline.py                  # Full video generation pipeline
    ai/
      theme_generator.py         # Claude AI theme generation
      story_generator.py         # Claude AI story generation
      improvement_ai.py          # Claude AI analytics improvement
    video/
      audio_generator.py         # ElevenLabs audio generation (chunked)
      video_generator.py         # FFmpeg video generation (BGM overlay)
    youtube/
      youtube_service.py         # YouTube upload and analytics
    analytics_collector.py       # YouTube analytics collection
  db/
    database.py                  # PostgreSQL database operations
  templates/                     # Jinja2 HTML templates
  static/
    css/style.css                # White/blue theme styling
    img/                         # Background/thumbnail images
    bgm/ambient_sleep.mp3        # Lullaby BGM
    audio/                       # Generated audio files
    videos/                      # Generated video files
```

## Environment Variables Required
- `SESSION_SECRET` - Session encryption key
- `ANTHROPIC_API_KEY` - Claude API key
- `ELEVENLABS_API_KEY` - ElevenLabs API key
- `YOUTUBE_OAUTH_CLIENT_ID` - Google OAuth Client ID
- `YOUTUBE_OAUTH_CLIENT_SECRET` - Google OAuth Client Secret
- `YOUTUBE_OAUTH_REFRESH_TOKEN` - OAuth refresh token
- `YOUTUBE_CHANNEL_ID` - YouTube channel ID
- `ADMIN_USERNAME` - Admin login username
- `ADMIN_PASSWORD` - Admin login password
- `NEON_DATABASE_URL` or `DATABASE_URL` - PostgreSQL connection string

## Workflow
- `Start application` runs `python main.py` on port 5000
