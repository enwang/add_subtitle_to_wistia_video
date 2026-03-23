# Add Subtitle To Wistia Video

Downloads a Wistia video, transcribes Cantonese or Mandarin speech to Chinese subtitles by default, burns those subtitles into a final MP4, and generates a companion PDF summary focused on the core message and named themes from the transcript.

## Wistia URL → subtitled MP4

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL"
```

The default output file is written to:

```bash
~/Downloads/<video-id>.subtitled.mp4
```

The PDF summary is written next to it:

```bash
~/Downloads/<video-id>.subtitled.summary.pdf
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
