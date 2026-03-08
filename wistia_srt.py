#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from faster_whisper import WhisperModel


def ffmpeg_binary() -> str | None:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if preferred.exists() and os.access(preferred, os.X_OK):
        return str(preferred)
    return shutil_which("ffmpeg")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def safe_stem(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    candidate = Path(path).name or "wistia"
    candidate = re.sub(r"\.[a-zA-Z0-9]+$", "", candidate)
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._-")
    return candidate or "wistia"


def normalize_wistia_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc != "fast.wistia.net":
        return url

    match = re.match(r"^/embed/iframe/([A-Za-z0-9]+)$", parsed.path)
    if match:
        return f"https://fast.wistia.net/embed/medias/{match.group(1)}.m3u8"

    return url


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


def write_srt(
    audio_path: Path,
    srt_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    task: str,
    language: str | None,
) -> int:
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    transcribe_args = {"task": task, "beam_size": 5, "vad_filter": True}
    if language:
        transcribe_args["language"] = language
    segments, info = model.transcribe(str(audio_path), **transcribe_args)
    print(f"Detected language: {info.language} (p={info.language_probability:.2f})", flush=True)

    written = 0
    with srt_path.open("w", encoding="utf-8") as handle:
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            written += 1
            handle.write(
                f"{written}\n"
                f"{timestamp(segment.start)} --> {timestamp(segment.end)}\n"
                f"{text}\n\n"
            )
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a Wistia stream, translate audio to English subtitles, and burn them into a new MP4."
    )
    parser.add_argument("url", help="Wistia stream URL, typically an m3u8 link")
    parser.add_argument(
        "-o",
        "--output",
        help="Output MP4 path. Defaults to <stream-id>.subtitled.mp4 in the current directory.",
    )
    parser.add_argument(
        "--model",
        default="large-v3",
        help="Whisper model size to use. Default: large-v3",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Whisper device selection. Default: auto",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="Whisper compute type. Default: int8",
    )
    parser.add_argument(
        "--task",
        choices=["transcribe", "translate"],
        default="transcribe",
        help="Use 'transcribe' for same-language subtitles or 'translate' for English subtitles. Default: transcribe",
    )
    parser.add_argument(
        "--language",
        default="zh",
        help="Language hint for Whisper. Use zh for Cantonese/Mandarin to get Chinese text. Default: zh",
    )
    parser.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="Keep the downloaded source MP4, extracted audio, and generated SRT file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        print("ffmpeg is required but was not found in PATH.", file=sys.stderr)
        return 1

    input_url = normalize_wistia_url(args.url)
    stem = safe_stem(input_url)
    default_output_dir = Path.home() / "Downloads"
    output_path = Path(args.output) if args.output else default_output_dir / f"{stem}.subtitled.mp4"
    source_path = output_path.with_name(f"{output_path.stem}.source.mp4")
    audio_path = output_path.with_suffix(".m4a")
    srt_path = output_path.with_suffix(".srt")

    try:
        run(
            [
                ffmpeg,
                "-y",
                "-i",
                input_url,
                "-c",
                "copy",
                str(source_path),
            ]
        )
        run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "aac",
                str(audio_path),
            ]
        )
        subtitle_count = write_srt(
            audio_path,
            srt_path,
            args.model,
            args.device,
            args.compute_type,
            args.task,
            args.language,
        )
        if subtitle_count:
            run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source_path),
                    "-vf",
                    ffmpeg_subtitles_arg(srt_path),
                    "-c:a",
                    "copy",
                    str(output_path),
                ]
            )
        else:
            print("No subtitle segments were generated; writing the downloaded video without burned subtitles.")
            run([ffmpeg, "-y", "-i", str(source_path), "-c", "copy", str(output_path)])
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode
    except Exception as exc:  # pragma: no cover
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_intermediate:
            for path in (source_path, audio_path, srt_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    print(f"Wrote {output_path}")
    return 0


def shutil_which(binary: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
