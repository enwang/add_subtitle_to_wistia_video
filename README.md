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

The default output file is written to:

```bash
~/Downloads/<video-id>.subtitled.mp4
```

The PDF summary is written next to it:

```bash
~/Downloads/<video-id>.subtitled.summary.pdf
```

For Wistia iframe/media URLs, the script resolves the video to a direct MP4 rendition before downloading. This avoids slow HLS segment-by-segment downloads, which are especially painful on high-latency routes such as China.

For YouTube URLs, the script uses `yt-dlp` to download an MP4 source before running the same transcription and subtitle burn-in pipeline. If Node.js is available, it is passed to `yt-dlp` as the JavaScript runtime, and the script enables the `mweb` YouTube client plus the EJS solver for more reliable extraction. Install `yt-dlp` in the active Python environment if needed:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python -m pip install yt-dlp
```

## Already-downloaded MP4 → subtitled MP4

Edit the `source`, `srt`, and `output` paths at the top of `burn_subs.py`, then run:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/burn_subs.py
```

The output is written to `~/Downloads/<name>.subtitled.mp4`.

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

Skip the PDF summary:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --skip-summary-pdf
```

Add representative frame pages to the PDF:

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL" --include-summary-images
```
