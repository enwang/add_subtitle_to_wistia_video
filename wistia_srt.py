#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from faster_whisper import WhisperModel
from summary_pdf import build_summary_pdf

# Load .env from the same directory as this script
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            if _k.strip() and _v.strip() and _k.strip() not in os.environ:
                os.environ[_k.strip()] = _v.strip()


@dataclass
class SubtitleSegment:
    start: float
    end: float
    text: str


def ffmpeg_binary() -> str | None:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if preferred.exists() and os.access(preferred, os.X_OK):
        return str(preferred)
    return shutil_which("ffmpeg")


def ffprobe_binary() -> str | None:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if preferred.exists() and os.access(preferred, os.X_OK):
        return str(preferred)
    return shutil_which("ffprobe")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def clock_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def elapsed_label(seconds: float) -> str:
    return f"{seconds:.1f}s"


def ffmpeg_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def clip_args(start: str | None, duration: str | None) -> list[str]:
    args: list[str] = []
    if start:
        args.extend(["-ss", start])
    if duration:
        args.extend(["-t", duration])
    return args


def safe_stem(url: str) -> str:
    google_drive_id = extract_google_drive_file_id(url)
    if google_drive_id:
        return google_drive_id
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


def extract_google_drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"drive.google.com", "www.drive.google.com", "drive.usercontent.google.com"}:
        return None

    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)

    query = parse_qs(parsed.query)
    values = query.get("id")
    if values:
        return values[0]
    return None


def parse_hidden_inputs(html_body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(
        r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
        html_body,
        flags=re.IGNORECASE,
    ):
        fields[html.unescape(match.group(1))] = html.unescape(match.group(2))
    return fields


def resolve_google_drive_download_url(url: str) -> str | None:
    file_id = extract_google_drive_file_id(url)
    if not file_id:
        return None

    probe_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = subprocess.check_output(["curl", "-fsSL", probe_url], text=True)

    form_match = re.search(r'<form[^>]+action="([^"]+/download)"', response, flags=re.IGNORECASE)
    if form_match:
        params = parse_hidden_inputs(response)
        params.setdefault("id", file_id)
        params.setdefault("export", "download")
        return f"{html.unescape(form_match.group(1))}?{urlencode(params)}"

    direct_match = re.search(
        r'https://drive\.usercontent\.google\.com/(?:download|uc)\?[^"\']+',
        response,
    )
    if direct_match:
        return html.unescape(direct_match.group(0).replace("\\u003d", "=").replace("\\u0026", "&"))

    if "Google Drive" in response:
        return probe_url

    return probe_url


def download_google_drive_file(url: str, destination: Path) -> None:
    resolved = resolve_google_drive_download_url(url)
    if not resolved:
        raise ValueError("Could not resolve Google Drive download URL.")
    run(["curl", "-fL", resolved, "-o", str(destination)])


def is_google_drive_url(url: str) -> bool:
    return extract_google_drive_file_id(url) is not None


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


def media_duration_seconds(path: Path) -> float | None:
    ffprobe = ffprobe_binary()
    if not ffprobe:
        return None

    try:
        output = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None

    try:
        duration = float(output)
    except ValueError:
        return None
    return duration if duration > 0 else None


def wrap_text_block(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        wrapped = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
        )
        lines.extend(wrapped or [""])
    return lines


def write_srt(
    audio_path: Path,
    srt_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    task: str,
    language: str | None,
) -> tuple[int, dict[str, float], list[SubtitleSegment], str | None]:
    model_load_started = time.monotonic()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    model_load_elapsed = time.monotonic() - model_load_started
    transcribe_args = {"task": task, "beam_size": 5, "vad_filter": True}
    if language:
        transcribe_args["language"] = language
    transcribe_started = time.monotonic()
    segments, info = model.transcribe(str(audio_path), **transcribe_args)
    duration = media_duration_seconds(audio_path)
    start_time = time.monotonic()
    last_logged_at = 0.0
    print(f"Detected language: {info.language} (p={info.language_probability:.2f})", flush=True)
    if duration:
        print(
            f"Transcribing audio: 00% (00:00/{clock_timestamp(duration)}), ETA unknown",
            flush=True,
        )

    written = 0
    subtitle_segments: list[SubtitleSegment] = []
    with srt_path.open("w", encoding="utf-8") as handle:
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            written += 1
            subtitle_segments.append(
                SubtitleSegment(start=segment.start, end=segment.end, text=text)
            )
            handle.write(
                f"{written}\n"
                f"{timestamp(segment.start)} --> {timestamp(segment.end)}\n"
                f"{text}\n\n"
            )

            if duration:
                processed = min(segment.end, duration)
                now = time.monotonic()
                should_log = (
                    processed >= duration
                    or processed - last_logged_at >= 30
                    or now - start_time < 1
                )
                if should_log and processed > 0:
                    elapsed = now - start_time
                    remaining_audio = max(duration - processed, 0.0)
                    eta_seconds = remaining_audio * (elapsed / processed)
                    percent = min(100, int((processed / duration) * 100))
                    print(
                        "Transcribing audio: "
                        f"{percent:02}% ({clock_timestamp(processed)}/{clock_timestamp(duration)}), "
                        f"ETA {clock_timestamp(eta_seconds)}",
                        flush=True,
                    )
                    last_logged_at = processed

    if duration:
        total_elapsed = time.monotonic() - start_time
        print(
            f"Transcribing audio: 100% ({clock_timestamp(duration)}/{clock_timestamp(duration)}), "
            f"ETA 00:00:00, elapsed {clock_timestamp(total_elapsed)}",
            flush=True,
        )
    return written, {
        "model_load": model_load_elapsed,
        "transcribe": time.monotonic() - transcribe_started,
    }, subtitle_segments, info.language


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a Wistia or Google Drive video, transcribe or translate audio, and burn subtitles into a new MP4."
    )
    parser.add_argument("url", help="Wistia or Google Drive video URL")
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
    parser.add_argument(
        "--start",
        help="Optional clip start time for short test runs, for example 00:01:00.",
    )
    parser.add_argument(
        "--duration",
        help="Optional clip duration for short test runs, for example 00:00:20.",
    )
    parser.add_argument(
        "--skip-summary-pdf",
        action="store_true",
        help="Skip generating the companion PDF summary file.",
    )
    parser.add_argument(
        "--include-summary-images",
        action="store_true",
        help="Add representative frame pages to the PDF summary.",
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
    pdf_summary_path = output_path.with_suffix(".summary.pdf")
    timings: dict[str, float] = {}
    total_started = time.monotonic()
    subtitle_segments: list[SubtitleSegment] = []
    detected_language: str | None = None

    try:
        stage_started = time.monotonic()
        if is_google_drive_url(input_url):
            download_google_drive_file(input_url, source_path)
        else:
            run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    input_url,
                    *clip_args(args.start, args.duration),
                    "-c",
                    "copy",
                    str(source_path),
                ]
            )
        timings["download"] = time.monotonic() - stage_started

        if is_google_drive_url(input_url) and (args.start or args.duration):
            clipped_source_path = source_path.with_name(f"{source_path.stem}.clip.mp4")
            stage_started = time.monotonic()
            run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source_path),
                    *clip_args(args.start, args.duration),
                    "-c",
                    "copy",
                    str(clipped_source_path),
                ]
            )
            source_path.unlink(missing_ok=True)
            clipped_source_path.replace(source_path)
            timings["download"] += time.monotonic() - stage_started

        stage_started = time.monotonic()
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
        timings["audio_extract"] = time.monotonic() - stage_started

        subtitle_count, transcription_timings, subtitle_segments, detected_language = write_srt(
            audio_path,
            srt_path,
            args.model,
            args.device,
            args.compute_type,
            args.task,
            args.language,
        )
        timings.update(transcription_timings)
        if subtitle_count:
            stage_started = time.monotonic()
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
            timings["subtitle_burn"] = time.monotonic() - stage_started
        else:
            print("No subtitle segments were generated; writing the downloaded video without burned subtitles.")
            stage_started = time.monotonic()
            run([ffmpeg, "-y", "-i", str(source_path), "-c", "copy", str(output_path)])
            timings["copy_output"] = time.monotonic() - stage_started

        if not args.skip_summary_pdf:
            stage_started = time.monotonic()
            print("Generating PDF summary...", flush=True)
            build_summary_pdf(
                ffmpeg,
                output_path,
                pdf_summary_path,
                subtitle_segments,
                subtitle_count,
                detected_language,
                input_url,
                args.include_summary_images,
                ffprobe_binary(),
            )
            timings["summary_pdf"] = time.monotonic() - stage_started
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

    timings["total"] = time.monotonic() - total_started
    print("Stage timings:", flush=True)
    for label in (
        "download",
        "audio_extract",
        "model_load",
        "transcribe",
        "subtitle_burn",
        "copy_output",
        "summary_pdf",
        "total",
    ):
        if label in timings:
            print(f"  {label}: {elapsed_label(timings[label])}", flush=True)

    print(f"Wrote {output_path}")
    if not args.skip_summary_pdf:
        print(f"Wrote {pdf_summary_path}")
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
