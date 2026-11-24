# Local AI Clipping Agent (Clipping.net Automation)

A production-ready local AI Clipping Agent in Python that automates the entire workflow for **Clipping.net** campaigns.

It ingests videos (YouTube, Twitch, Kick, or local `.mp4`), extracts high-accuracy audio, performs word-level speech transcription using **faster-whisper**, uses **Antigravity CLI** (`agy -p`) in headless mode for AI viral hook detection and metadata generation, edits and crops to **9:16 vertical video (1080x1920)** with dynamic high-contrast `.ass` captions, overlays campaign watermarks, normalizes audio to TikTok/Reels/Shorts broadcast loudness (`loudnorm`), and exports ready-to-post packages with metadata or uploads private drafts to YouTube Data API v3.

---

## 1. Project Directory Structure

```
├── config.json                     # Campaign rules, durations, watermarks, hashtags
├── requirements.txt                # Python dependencies
├── main.py                         # CLI orchestrator
├── README.md                       # Comprehensive guide
├── client_secrets.json             # (Optional) YouTube Data API v3 OAuth2 credentials
├── token.json                      # (Auto-generated) Cached YouTube OAuth token
├── modules/
│   ├── __init__.py
│   ├── ingest.py                   # URL downloader (yt-dlp) & 16kHz audio extractor (ffmpeg)
│   ├── transcribe.py               # Word-level speech transcription (faster-whisper)
│   ├── analyze.py                  # AI viral hook selection via Antigravity CLI (agy -p)
│   ├── edit.py                     # 9:16 crop, dynamic .ass captions, watermark, loudnorm
│   └── upload.py                   # YouTube Data API v3 draft uploader & export summary
├── campaigns/
│   └── logo.png                    # Campaign-specific watermark logo (PNG with transparency)
├── workspace/                      # Temporary files (raw video, audio.wav, transcripts, .ass)
└── output/                         # Final ready-to-post .mp4 clips & .txt metadata
```

---

## 2. Prerequisites & Installation

### A. Python & FFmpeg
- **Python 3.11+** installed and available on PATH.
- **FFmpeg 7+ / 8+ / 9+** with `libass` and `libx264` support installed and available on PATH.
- **Antigravity CLI (`agy`)** installed and authenticated.

### B. Python Dependencies
All required packages are in `requirements.txt`:
```bash
pip install -r requirements.txt
```

---

## 3. Configuration (`config.json`)

Configure campaign presets to match your Clipping.net campaign requirements:

```json
{
  "active_campaign": "gaming_highlights",
  "campaigns": {
    "gaming_highlights": {
      "campaign_name": "gaming_highlights",
      "min_duration_sec": 20,
      "max_duration_sec": 55,
      "num_clips": 5,
      "required_hashtags": ["#gaming", "#clips", "#viral", "#shorts", "#fyp"],
      "prohibited_words": ["sponsor", "ad", "patreon", "twitch.tv", "use code"],
      "watermark_path": "campaigns/logo.png",
      "watermark_position": "top_right",
      "auto_upload_youtube_draft": false
    },
    "podcast_clips": {
      "campaign_name": "podcast_clips",
      "min_duration_sec": 25,
      "max_duration_sec": 50,
      "num_clips": 3,
      "required_hashtags": ["#podcast", "#interview", "#inspiration", "#shorts"],
      "prohibited_words": ["sponsor", "merch", "ad", "patreon"],
      "watermark_path": "campaigns/logo.png",
      "watermark_position": "bottom_right",
      "auto_upload_youtube_draft": false
    }
  }
}
```

### Config Options:
- `min_duration_sec`: Minimum duration of each clip in seconds (e.g. 20).
- `max_duration_sec`: Maximum duration of each clip in seconds (e.g. 55 for Shorts/Reels/TikTok).
- `num_clips`: Number of top viral moments to extract from the stream/video.
- `required_hashtags`: Hashtags strictly appended to every clip package.
- `prohibited_words`: Negative keyword filter. AI will avoid selecting clips mentioning these words, and excludes them from titles/captions.
- `watermark_path`: Relative or absolute path to a PNG watermark badge (or `null` for none).
- `watermark_position`: `"top_right"`, `"top_left"`, `"bottom_right"`, or `"bottom_left"`.
- `auto_upload_youtube_draft`: If `true` and `client_secrets.json` exists, uploads clips as **PRIVATE drafts** on YouTube for human review before publishing.

---

## 4. How It Works (Pipeline Architecture)

1. **Ingest (`modules/ingest.py`)**:
   - If a URL (YouTube, Twitch, Kick, etc.) is provided, `yt-dlp` downloads the best MP4 video+audio up to 1080p.
   - If a local file path is provided, it is copied directly into `workspace/raw_video.mp4`.
   - FFmpeg extracts a clean `16kHz`, `mono`, `pcm_s16le` `.wav` audio track (`workspace/audio.wav`).

2. **Transcribe (`modules/transcribe.py`)**:
   - Loads `faster-whisper` (default `"base"` model, auto-detecting CUDA `float16` or CPU `int8`).
   - Runs with `word_timestamps=True` and VAD filtering.
   - Generates `workspace/transcript_segments.json` (sentence segments with start/end for AI reasoning) and `workspace/word_timestamps.json` (precise per-word timestamps for subtitles).

3. **Viral AI Hook Analysis (`modules/analyze.py`)**:
   - Calls the local **Antigravity CLI** in headless mode: `agy -p "<prompt>"` via Python `subprocess`.
   - Instructs `agy` to act as an elite short-form retention editor, finding the top moments starting with high-retention hooks and ending cleanly on complete thoughts.
   - Strictly enforces duration bounds, schema constraints, and prohibited word blacklists.
   - Outputs structured JSON parsed directly into clip specifications.

4. **Editing & Subtitle Burning (`modules/edit.py`)**:
   - For each clip:
     - Slices video from `start_time` to `end_time`.
     - Center-crops 16:9 landscape footage to **9:16 vertical video (`1080x1920`)**: `crop=ih*9/16:ih,scale=1080:1920`.
     - Generates an **Advanced SubStation Alpha (`.ass`)** subtitle file with dynamic 2–4 word chunks, styled with bold font, thick black outline, drop shadow, and positioned at 75% screen height.
     - Burns subtitles directly into video.
     - Overlays the campaign logo watermark at 70% opacity in the configured corner with 20px padding.
     - Applies the EBU R128 `loudnorm` filter to normalize audio volume for TikTok/Reels/Shorts.
     - Exports final video as `output/clip_{id}.mp4` (`libx264`, `crf 20`, `preset fast`, `aac 192k`).
     - Saves ready-to-post metadata in `output/clip_{id}_metadata.txt`.

5. **Upload & Export (`modules/upload.py`)**:
   - If `auto_upload_youtube_draft` is enabled and `client_secrets.json` is provided, uploads each clip to YouTube with `privacyStatus: "private"` (Draft).
   - Generates YouTube Studio direct review links.
   - Otherwise, prints the local export directory path for manual posting.

---

## 5. Usage Commands

### Run with a YouTube / Twitch URL:
```bash
python main.py --input "https://www.youtube.com/watch?v=EXAMPLE_ID" --campaign gaming_highlights
```

### Run with a Local Video File:
```bash
python main.py --input "my_gameplay.mp4" --campaign gaming_highlights
```

### Switch Campaigns:
```bash
python main.py --input "interview.mp4" --campaign podcast_clips
```

### Choose Whisper Model & Device:
```bash
# High accuracy on GPU
python main.py --input "stream.mp4" --campaign gaming_highlights --model small --device cuda

# Fast CPU execution
python main.py --input "stream.mp4" --campaign gaming_highlights --model base --device cpu
```

### Auto-Clean Temporary Workspace Files:
```bash
python main.py --input "stream.mp4" --campaign gaming_highlights --clean
```

---

## 6. YouTube Draft Upload Setup (Optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **YouTube Data API v3**.
3. Create an **OAuth 2.0 Client ID** (Desktop Application).
4. Download the credentials JSON and save it as `client_secrets.json` in this directory (`e:\vid\client_secrets.json`).
5. Set `"auto_upload_youtube_draft": true` in `config.json`.
6. On the first run, a browser window will open asking you to authorize the application. The token is cached in `token.json` for all future automated runs.
7. **Every upload is set to `private` (Draft)** so you can inspect the video and title in YouTube Studio before publishing and submitting the link to Clipping.net!
