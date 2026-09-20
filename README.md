# Add Subtitle To Wistia Video

Downloads a Wistia, YouTube, or Google Drive video, transcribes Cantonese or Mandarin speech to Chinese subtitles by default, burns those subtitles into a final MP4, and can generate companion PDF/Markdown summaries focused on the core message and named themes from the transcript.

## Video URL → subtitled MP4

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL"
```

The same command also accepts YouTube URLs:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

It also accepts Google Drive file URLs and local video files:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "https://drive.google.com/file/d/FILE_ID/view"
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py ~/Downloads/video.mp4
```

The default output file is written to:

```bash
~/Downloads/JLaw Videos/<video-id>.subtitled.mp4
```

The PDF summary is written next to it:

```bash
~/Downloads/JLaw Videos/<video-id>.subtitled.summary.pdf
```

For Wistia iframe/media URLs, the script resolves the video to a direct MP4 rendition before downloading. This avoids slow HLS segment-by-segment downloads, which are especially painful on high-latency routes such as China.

After downloading, the script checks for existing subtitle tracks or subtitled-video metadata and samples the subtitle area with macOS Vision OCR. If the existing subtitles are clearly Simplified Chinese, the original video is kept unchanged. Traditional Chinese, Cantonese, and uncertain results continue through the normal translation and subtitle pipeline. Use `--force-subtitles` to bypass this detection and always generate new subtitles.

For YouTube URLs, the script uses `yt-dlp` to download an MP4 source before running the same transcription and subtitle burn-in pipeline. It first tries the best available MP4 rendition through the embedded web client, then falls back to 1080p H.264 and finally to a compatible 360p MP4 if YouTube rejects higher-quality streams. If Node.js is available, it is passed to `yt-dlp` as the JavaScript runtime. Install `yt-dlp` in the active Python environment if needed:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python -m pip install yt-dlp
```

For private Google Drive files, the most durable setup is Google Drive OAuth. Install the optional dependencies:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

Create a Google OAuth Desktop client, download its `client_secret_*.json`, and save it here:

```bash
mkdir -p ~/.config/jlaw_video
cp ~/Downloads/client_secret_*.json ~/.config/jlaw_video/google_drive_client_secret.json
```

Then run the normal command. The first run opens a browser authorization page and stores a reusable token at `~/.cache/jlaw_video/google_drive_token.json`; later private Drive links download through the Drive API automatically:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "https://drive.google.com/file/d/FILE_ID/view" --summary-pdf
```

You can also pass a client file explicitly:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "https://drive.google.com/file/d/FILE_ID/view" --drive-oauth-client ~/Downloads/client_secret.json
```

Without OAuth, the script still tries direct Drive download, the large-file virus-warning confirm URL, Chrome cookies through `yt-dlp`, and finally any local Drive cache. Some Google Vids or embedded-player files can reject non-OAuth direct download even when they play in the browser.

## Already-downloaded MP4 → subtitled MP4

Edit the `source`, `srt`, and `output` paths at the top of `burn_subs.py`, then run:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/burn_subs.py
```

The output is written to `~/Downloads/JLaw Videos/<name>.subtitled.mp4`.

## Accepted URL formats

```bash
https://fast.wistia.net/embed/iframe/rfgg73bjgf
```

```bash
https://fast.wistia.net/embed/medias/rfgg73bjgf.m3u8
```

```bash
https://www.youtube.com/watch?v=2-dKidjsu9I
```

```bash
https://youtu.be/2-dKidjsu9I
```

## Options

Write to a specific file:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" -o ~/Downloads/output.mp4
```

Translate to English instead of writing Chinese subtitles:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --task translate --language zh
```

Use a faster model:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --model turbo
```

Choose a lower Wistia video rendition for faster downloads:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --wistia-height 720
```

By default, Wistia downloads use the highest available direct MP4 rendition to preserve the original clarity.

Use a short clip for speed testing:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --start 00:01:00 --duration 00:00:20 --model turbo
```

Cover subtitles that are already hard-burned into the source video before adding the new subtitles:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_VIDEO_URL" --cover-existing-subtitles
```

If the old subtitle block is taller or shorter, adjust the covered bottom area:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_VIDEO_URL" --cover-existing-subtitles --subtitle-cover-height 0.35
```

Skip the PDF summary:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --skip-summary-pdf
```

Add representative frame pages to the PDF:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --include-summary-images
```

## Automatically process MYT - JL Gmail videos

`gmail_myt_watcher.py` checks Gmail for new messages whose subject contains
`[MYT - JL]`. When a new message contains a supported Wistia, YouTube, or
Google Drive video URL, it runs the normal subtitle pipeline. The first run
records existing matching messages as a baseline, so old inbox messages are
not processed accidentally.

Automatic outputs are organized under `~/Downloads/JLaw Videos/` and named
from the email subject, for example `2026-09-20_大盘.mp4`,
`2026-09-20_图表.mp4`, or `2026-09-15_Q&A.mp4`.

Authorize Gmail and initialize the baseline:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/gmail_myt_watcher.py
```

The Gmail token is stored at
`~/.cache/jlaw_video/google_gmail_token.json`, and processed Gmail message IDs
are stored at `~/.cache/jlaw_video/myt_mail_state.json`. The watcher requests
read-only Gmail access and does not mark, archive, delete, or send mail.

To run the watcher automatically once per hour on macOS, install the included
LaunchAgent:

```bash
mkdir -p ~/Library/LaunchAgents ~/.cache/jlaw_video
cp com.jlaw-video.myt-mail-watcher.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jlaw-video.myt-mail-watcher.plist
```

The LaunchAgent also runs once when it is loaded. Its output and error logs are
written under `~/.cache/jlaw_video/`.

To pass normal subtitle options to each automatic run, put them after `--`:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/gmail_myt_watcher.py -- --cover-existing-subtitles --summary-pdf
```
