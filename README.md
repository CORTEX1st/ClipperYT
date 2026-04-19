# ProjectAI - YouTube Shorts AI Assistant

Flask-based local app for:
- Upload video (file / YouTube URL)
- Detect viral moments
- Auto-generate Shorts clips
- Add word-by-word captions
- Optional intro freeze-frame + title voice
- Auto upload to YouTube (with review fallback)

## Main Features

- `Upload source`: local file or YouTube URL
- `Mode`: auto / clip / film
- `Viral recommendations`: score + hook per moment
- `Clip pipeline`:
  - smart crop 9:16
  - smoother camera movement
  - full auto caption per word
  - intro freeze-frame with stylized title
- `SEO metadata`: title, description, tags auto-normalized
- `YouTube upload`:
  - OAuth login
  - auto upload selected clips
  - fallback to manual review if auto upload fails
- `Thumbnail`: auto-generated and auto-set when channel permission is available

## Tech Stack

- Python + Flask
- FFmpeg / FFprobe
- Groq API (LLM + Whisper transcription)
- yt-dlp (YouTube source download + metadata)
- Pillow (thumbnail and image text rendering)
- Google YouTube Data API v3 (upload)

## Project Structure

- `app.py` - API routes and job orchestration
- `clip_extractor.py` - viral detection, clip render, captions, intro
- `uploader.py` - YouTube OAuth/upload/thumbnail
- `video_editor.py` - editor pipeline
- `ai_video_generator.py` - AI storyboard + render pipeline
- `ui/index.html` - frontend UI
- `ffmpeg_helper.py` - ffmpeg/ffprobe wrapper

## Setup

1. Create Python env and install deps:
   ```bash
   pip install -r requirements.txt
   ```
2. Ensure FFmpeg available:
   - Option A: put `ffmpeg.exe` and `ffprobe.exe` in project root
   - Option B: install FFmpeg in system PATH
3. Run app:
   ```bash
   python app.py
   ```
4. Open:
   - `http://localhost:5000`

## Required API Keys

Set from UI (`API Keys`) or in `.env.json`:
- `GROQ_API_KEY` (required for AI features)
- `YOUTUBE_CLIENT_ID` + `YOUTUBE_CLIENT_SECRET` (for YouTube upload)
- Optional image providers:
  - `GEMINI_API_KEY`
  - `TOGETHER_API_KEY`
  - `CF_ACCOUNT_ID`, `CF_API_TOKEN`
  - `HF_API_KEY`

Optional:
- `YOUTUBE_REDIRECT_PORT` (default `8090`)

## Upload Flow

1. Input source (upload or YouTube URL)
2. Choose mode
3. Fetch viral recommendations
4. Select one or multiple moments
5. Review metadata + schedule
6. Start upload

If automatic upload fails, app stores generated clips and switches to review mode for manual retry.

## Git Safety (Important)

Do **not** commit:
- `.env.json`
- `youtube_token.pkl`
- `jobs.json`
- all generated media folders (`uploads`, `output_clips`, `thumbnails`, etc.)
- local credential files outside this folder (example: `../credential.txt`)

Use `.gitignore` in this repo (already added).

## Troubleshooting

- `invalid_grant` on upload:
  - token expired/revoked; relogin OAuth
- `thumbnail forbidden 403`:
  - channel does not have custom thumbnail permission yet
- `ResumableUploadError`:
  - network/session issue; retry from review/manual upload
- no intro generated:
  - check job logs for `Intro skip` details

## Notes

- This is a local-first app and persists runtime state in local files.
- Some AI/network operations depend on provider quota/rate limits.

