#!/usr/bin/env python3
"""Burn an existing SRT into a local MP4."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def ffmpeg_subtitles_arg(path: Path) -> str:
    raw = str(path.resolve())
    raw = raw.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
    style = (
        "FontName=PingFang SC,"
        "FontSize=20,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H00000000,"
        "BorderStyle=3,"
        "Outline=1,"
        "Shadow=0,"
        "MarginV=15,"
        "Alignment=2"
    )
    return f"subtitles=filename='{raw}':force_style='{style}'"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    source = Path("/Users/welsnake/jlaw_video/3.7_largev3.subtitled.source.mp4")
    srt    = Path("/Users/welsnake/jlaw_video/3.7圖表教學.retry.srt")
    output = Path.home() / "Downloads" / "3.7圖表教學.subtitled.mp4"

    if not source.exists():
        print(f"Source not found: {source}", file=sys.stderr)
        return 1
    if not srt.exists():
        print(f"SRT not found: {srt}", file=sys.stderr)
        return 1

    run([
        FFMPEG, "-y",
        "-i", str(source),
        "-vf", ffmpeg_subtitles_arg(srt),
        "-c:a", "copy",
        str(output),
    ])
    print(f"\nDone → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
