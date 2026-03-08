# Add Subtitle To Wistia Video

Downloads a Wistia video, transcribes Cantonese or Mandarin speech to Chinese subtitles by default, and burns those subtitles into a final MP4.

## Wistia URL → subtitled MP4

```bash
/Users/welsnake/jlaw_video/.venv/bin/python /Users/welsnake/jlaw_video/wistia_srt.py "YOUR_WISTIA_URL"
```

The default output file is written to:

```bash
~/Downloads/<video-id>.subtitled.mp4
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
