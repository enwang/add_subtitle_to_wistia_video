#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from faster_whisper import WhisperModel
from summary_pdf import build_summary_pdf

try:
    import mlx_whisper as _mlx_whisper
    _MLX_AVAILABLE = True
except ImportError:
    _MLX_AVAILABLE = False

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Primer sentence in simplified Chinese — biases Whisper toward simplified characters
_SIMPLIFIED_CHINESE_PROMPT = "以下是普通话或粤语财经视频的字幕内容。"

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


def extract_audio_clip(audio_path: Path, start: float, end: float, dest: Path) -> None:
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    subprocess.run(
        [
            ffmpeg, "-y",
            "-ss", str(start),
            "-t", str(end - start),
            "-i", str(audio_path),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(dest),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def fill_gaps(
    segments: list[SubtitleSegment],
    audio_path: Path,
    audio_duration: float,
    gap_threshold: float,
    transcribe_clip: Callable[[Path], list[SubtitleSegment]],
) -> list[SubtitleSegment]:
    """Detect silent gaps between segments and re-transcribe them to recover skipped words."""
    if not segments:
        return segments

    gaps: list[tuple[float, float]] = []

    # Leading gap: audio before the first segment
    if segments[0].start > gap_threshold:
        gaps.append((0.0, segments[0].start))

    # Gaps between consecutive segments
    for i in range(len(segments) - 1):
        gap_start = segments[i].end
        gap_end = segments[i + 1].start
        if gap_end - gap_start > gap_threshold:
            gaps.append((gap_start, gap_end))

    if not gaps:
        return segments

    print(f"\nGap fill: found {len(gaps)} gap(s) exceeding {gap_threshold:.1f}s — re-transcribing...", flush=True)
    recovered: list[SubtitleSegment] = []

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, (gap_start, gap_end) in enumerate(gaps):
                clip_path = Path(tmpdir) / f"gap_{i}.m4a"
                print(
                    f"  Gap {i + 1}/{len(gaps)}: {timestamp(gap_start)} → {timestamp(gap_end)} "
                    f"({gap_end - gap_start:.1f}s)",
                    flush=True,
                )
                try:
                    extract_audio_clip(audio_path, gap_start, gap_end, clip_path)
                    clip_segments = transcribe_clip(clip_path)
                    if clip_segments:
                        print(f"    Recovered {len(clip_segments)} segment(s)", flush=True)
                        for seg in clip_segments:
                            recovered.append(
                                SubtitleSegment(
                                    start=seg.start + gap_start,
                                    end=seg.end + gap_start,
                                    text=seg.text,
                                )
                            )
                    else:
                        print(f"    No speech found (genuine silence)", flush=True)
                except Exception as exc:
                    print(f"    Warning: gap re-transcription failed: {exc}", flush=True)
    except Exception as exc:
        print(f"Gap fill: setup failed ({exc}), continuing without gap recovery.", flush=True)
        return segments

    if not recovered:
        return segments

    merged = sorted(segments + recovered, key=lambda s: s.start)
    print(f"Gap fill: inserted {len(recovered)} recovered segment(s).", flush=True)
    return merged


def _call_coherence_tool(lines: list[str], api_key: str, language: str | None) -> dict:
    """Call Claude to flag incoherent subtitle segments. Returns tool input dict or {} on failure."""
    if not _ANTHROPIC_AVAILABLE:
        return {}
    lang_hint = f" The video is in language code '{language}'." if language else ""
    hallucination_examples = "感谢收看, 感谢观看, 感谢您的观看, 请订阅, 请记得订阅, 别忘了点赞, 字幕提供, 敬请期待, 如果你觉得有用请点赞, 谢谢收看, 多谢收看"
    prompt = (
        f"You are reviewing auto-generated subtitles for a Cantonese/Mandarin financial video.{lang_hint}\n"
        "Each line is formatted as INDEX: [HH:MM:SS] subtitle_text.\n\n"
        "Flag any segment that is LIKELY WRONG due to:\n"
        "1. English finance terms mis-transcribed as phonetically similar Chinese characters "
        "(e.g. 'draw down' → '阻挡', 'cut loss' → '卡罗斯', 'support level' → '撑位' when clearly English was spoken)\n"
        "2. Known Whisper hallucination phrases such as: " + hallucination_examples + "\n"
        "3. Text that completely breaks the logical flow of the surrounding financial discussion\n"
        "4. Gibberish or random characters unrelated to speech\n\n"
        "Be CONSERVATIVE: only flag segments you are CONFIDENT are wrong. "
        "Do NOT flag segments that are merely unusual. "
        "Mixed Chinese/English is completely normal in Cantonese financial speech.\n\n"
        "Segments to review:\n"
        + "\n".join(f"{i}: {line}" for i, line in enumerate(lines))
    )
    tool = {
        "name": "flag_incoherent_segments",
        "description": "Flag subtitle segments that appear incorrect or hallucinatory given surrounding context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flagged": {
                    "type": "array",
                    "description": "Segments to re-transcribe. Empty array if none.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "description": "0-based index within the provided list"},
                            "reason": {"type": "string", "description": "Brief reason this segment appears wrong"},
                        },
                        "required": ["index", "reason"],
                    },
                }
            },
            "required": ["flagged"],
        },
    }
    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        tools=[tool],
        tool_choice={"type": "tool", "name": "flag_incoherent_segments"},
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "flag_incoherent_segments":
            return block.input
    return {}


def check_coherence(
    segments: list[SubtitleSegment],
    api_key: str,
    language: str | None,
) -> list[tuple[int, str]]:
    """Slide a window over segments and ask Claude to flag incoherent ones.
    Returns list of (global_index, reason) sorted ascending. Returns [] on any failure.
    """
    if not segments:
        return []
    try:
        WINDOW_SIZE = 20
        STEP = 15
        flagged_map: dict[int, str] = {}

        for window_start in range(0, len(segments), STEP):
            window = segments[window_start: window_start + WINDOW_SIZE]
            lines = [f"[{timestamp(seg.start)}] {seg.text}" for seg in window]
            result = _call_coherence_tool(lines, api_key, language)
            for item in result.get("flagged") or []:
                local_idx = item.get("index")
                reason = item.get("reason", "")
                if isinstance(local_idx, int) and 0 <= local_idx < len(window):
                    global_idx = window_start + local_idx
                    if global_idx not in flagged_map:
                        flagged_map[global_idx] = reason

        return sorted(flagged_map.items())
    except Exception as exc:
        print(f"\nLLM coherence check failed ({exc}), skipping verification.", flush=True)
        return []


def verify_and_retry(
    segments: list[SubtitleSegment],
    audio_path: Path,
    transcribe_clip: Callable[[Path], list[SubtitleSegment]],
    api_key: str,
    language: str | None,
) -> list[SubtitleSegment]:
    """Ask Claude to flag incoherent segments, then re-transcribe those audio windows."""
    if not segments:
        return segments

    print(f"\nLLM verification: checking {len(segments)} segment(s) for coherence...", flush=True)
    flagged = check_coherence(segments, api_key, language)

    if not flagged:
        print("LLM verification: no issues found.", flush=True)
        return segments

    print(f"LLM verification: {len(flagged)} segment(s) flagged for re-transcription.", flush=True)
    result: list[SubtitleSegment] = list(segments)
    offset = 0

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for original_idx, reason in flagged:
                current_idx = original_idx + offset
                if current_idx >= len(result):
                    continue
                seg = result[current_idx]
                clip_path = Path(tmpdir) / f"verify_{original_idx}.m4a"
                print(
                    f"  Re-transcribing [{timestamp(seg.start)} → {timestamp(seg.end)}] "
                    f"was: {seg.text!r} — reason: {reason}",
                    flush=True,
                )
                try:
                    extract_audio_clip(audio_path, seg.start, seg.end, clip_path)
                    new_segs = transcribe_clip(clip_path)
                except Exception as exc:
                    print(f"    Warning: re-transcription failed ({exc}), keeping original.", flush=True)
                    continue

                if not new_segs:
                    if is_hallucination(seg.text):
                        print(f"    No speech found; original was hallucination — dropped.", flush=True)
                        result = result[:current_idx] + result[current_idx + 1:]
                        offset -= 1
                    else:
                        print(f"    No speech found in clip; keeping original.", flush=True)
                    continue

                # Filter hallucinations out of re-transcription result
                clean_segs = [s for s in new_segs if not is_hallucination(s.text)]
                if not clean_segs:
                    print(f"    Re-transcription also hallucination — dropped.", flush=True)
                    result = result[:current_idx] + result[current_idx + 1:]
                    offset -= 1
                    continue

                adjusted = [
                    SubtitleSegment(
                        start=s.start + seg.start,
                        end=s.end + seg.start,
                        text=s.text,
                    )
                    for s in clean_segs
                ]
                new_text = " / ".join(s.text for s in adjusted)
                print(f"    Fixed: {seg.text!r} → {new_text!r}", flush=True)
                result = result[:current_idx] + adjusted + result[current_idx + 1:]
                offset += len(adjusted) - 1

    except Exception as exc:
        print(f"LLM verification: setup failed ({exc}), returning segments as-is.", flush=True)
        return segments

    print(f"LLM verification: complete. {len(segments)} → {len(result)} segment(s).", flush=True)
    return result


# Known Whisper hallucination phrases to strip unconditionally.
# These are Whisper's common "filler" outputs when it encounters silence or low-confidence audio.
_HALLUCINATION_SUBSTRINGS: tuple[str, ...] = (
    "感谢收看",
    "感谢观看",
    "感谢您的观看",
    "谢谢收看",
    "多谢收看",
    "请订阅",
    "记得订阅",
    "别忘了点赞",
    "请点赞",
    "不吝点赞",
    "欢迎点赞",
    "欢迎订阅",
    "请不吝",
    "打赏",
    "转发支持",
    "字幕提供",
    "敬请期待",
    "Thank you for watching",
    "Thanks for watching",
    "Please subscribe",
    "Don't forget to like",
)


def is_hallucination(text: str) -> bool:
    return any(phrase in text for phrase in _HALLUCINATION_SUBSTRINGS)


def retranscribe_hallucinations(
    segments: list[SubtitleSegment],
    audio_path: Path,
    transcribe_clip: Callable[[Path], list[SubtitleSegment]],
) -> list[SubtitleSegment]:
    """Find segments containing known hallucination phrases, re-transcribe their audio windows.
    Replaces with clean result if found; otherwise drops the segment entirely."""
    flagged_indices = [i for i, seg in enumerate(segments) if is_hallucination(seg.text)]
    if not flagged_indices:
        return segments

    print(f"\nHallucination retranscribe: {len(flagged_indices)} segment(s) to retry...", flush=True)
    result: list[SubtitleSegment] = list(segments)
    offset = 0

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for original_idx in flagged_indices:
                current_idx = original_idx + offset
                if current_idx >= len(result):
                    continue
                seg = result[current_idx]
                clip_path = Path(tmpdir) / f"halluc_{original_idx}.m4a"
                print(
                    f"  Retrying [{timestamp(seg.start)} → {timestamp(seg.end)}] {seg.text!r}",
                    flush=True,
                )
                try:
                    extract_audio_clip(audio_path, seg.start, seg.end, clip_path)
                    new_segs = transcribe_clip(clip_path)
                except Exception as exc:
                    print(f"    Re-transcription failed ({exc}), dropping segment.", flush=True)
                    result = result[:current_idx] + result[current_idx + 1:]
                    offset -= 1
                    continue

                # Filter hallucinations out of new results too
                clean_segs = [s for s in new_segs if not is_hallucination(s.text)]
                if not clean_segs:
                    print(f"    Still hallucination or no speech — dropped.", flush=True)
                    result = result[:current_idx] + result[current_idx + 1:]
                    offset -= 1
                else:
                    adjusted = [
                        SubtitleSegment(
                            start=s.start + seg.start,
                            end=s.end + seg.start,
                            text=s.text,
                        )
                        for s in clean_segs
                    ]
                    new_text = " / ".join(s.text for s in adjusted)
                    print(f"    Recovered: {new_text!r}", flush=True)
                    result = result[:current_idx] + adjusted + result[current_idx + 1:]
                    offset += len(adjusted) - 1
    except Exception as exc:
        print(f"Hallucination retranscribe: failed ({exc}), falling back to drop-only filter.", flush=True)
        return [seg for seg in segments if not is_hallucination(seg.text)]

    print(f"Hallucination retranscribe: complete. {len(segments)} → {len(result)} segment(s).", flush=True)
    return result


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


_MLX_MODEL_MAP = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}


def sanitize_segments(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    """Remove duplicate, overlapping, and near-duplicate subtitle segments and log issues."""
    if not segments:
        return segments

    issues: list[str] = []
    cleaned: list[SubtitleSegment] = []

    for seg in segments:
        if not cleaned:
            cleaned.append(seg)
            continue

        prev = cleaned[-1]

        # Exact consecutive duplicate text — extend the previous segment's end time
        if seg.text == prev.text:
            merged_end = max(prev.end, seg.end)
            issues.append(
                f"exact duplicate at {timestamp(seg.start)}: extended end from "
                f"{timestamp(prev.end)} to {timestamp(merged_end)}: {seg.text!r}"
            )
            cleaned[-1] = SubtitleSegment(start=prev.start, end=merged_end, text=prev.text)
            continue

        # Overlapping timestamps: seg starts before prev ends
        if seg.start < prev.end:
            fixed_start = prev.end
            fixed_end = max(seg.end, prev.end + 0.1)
            issues.append(
                f"overlap at {timestamp(seg.start)} (prev ends {timestamp(prev.end)}): "
                f"adjusted start to {timestamp(fixed_start)}"
            )
            seg = SubtitleSegment(start=fixed_start, end=fixed_end, text=seg.text)

        # Near-duplicate: one text fully contained in the other
        if seg.text in prev.text:
            issues.append(f"redundant segment (contained in previous) at {timestamp(seg.start)}: {seg.text!r}")
            continue
        if prev.text in seg.text:
            issues.append(f"redundant segment (contains previous) at {timestamp(seg.start)}: replacing previous with longer text")
            cleaned[-1] = SubtitleSegment(start=prev.start, end=seg.end, text=seg.text)
            continue

        cleaned.append(seg)

    total = len(segments)
    removed = total - len(cleaned)
    if issues:
        print(f"\nSubtitle QA: fixed {len(issues)} issue(s) ({removed} segment(s) removed or merged):", flush=True)
        for issue in issues:
            print(f"  - {issue}", flush=True)
    else:
        print(f"Subtitle QA: {total} segment(s) checked — no issues found.", flush=True)

    return cleaned


def strip_known_hallucinations(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    """Remove segments whose text contains a known Whisper hallucination phrase."""
    kept = []
    removed = 0
    for seg in segments:
        if any(phrase in seg.text for phrase in _HALLUCINATION_SUBSTRINGS):
            print(f"  Hallucination filter: removed [{timestamp(seg.start)}] {seg.text!r}", flush=True)
            removed += 1
        else:
            kept.append(seg)
    if removed:
        print(f"Hallucination filter: removed {removed} segment(s).", flush=True)
    return kept


def write_srt_from_segments(segments: list[SubtitleSegment], srt_path: Path) -> int:
    """Write sanitized segments to an SRT file. Returns the number of segments written."""
    clean = sanitize_segments(segments)
    clean = strip_known_hallucinations(clean)
    with srt_path.open("w", encoding="utf-8") as handle:
        for i, seg in enumerate(clean, 1):
            handle.write(f"{i}\n{timestamp(seg.start)} --> {timestamp(seg.end)}\n{seg.text}\n\n")
    return len(clean)


def write_srt(
    audio_path: Path,
    srt_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    task: str,
    language: str | None,
    gap_fill: bool = True,
    gap_threshold: float = 3.0,
    condition_on_previous_text: bool = False,
    verify: bool = True,
    traditional: bool = False,
) -> tuple[int, dict[str, float], list[SubtitleSegment], str | None]:
    mlx_repo = _MLX_MODEL_MAP.get(model_name) if _MLX_AVAILABLE and device in ("auto", "cpu") else None
    if mlx_repo:
        return _write_srt_mlx(
            audio_path, srt_path, mlx_repo, task, language,
            gap_fill, gap_threshold, condition_on_previous_text, verify, traditional,
        )
    return _write_srt_faster_whisper(
        audio_path, srt_path, model_name, device, compute_type, task, language,
        gap_fill, gap_threshold, condition_on_previous_text, verify, traditional,
    )


def _write_srt_mlx(
    audio_path: Path,
    srt_path: Path,
    mlx_repo: str,
    task: str,
    language: str | None,
    gap_fill: bool = True,
    gap_threshold: float = 3.0,
    condition_on_previous_text: bool = False,
    verify: bool = True,
    traditional: bool = False,
) -> tuple[int, dict[str, float], list[SubtitleSegment], str | None]:
    print(f"Using mlx-whisper ({mlx_repo}) on Apple Silicon GPU", flush=True)
    load_started = time.monotonic()
    transcribe_kwargs: dict = {
        "path_or_hf_repo": mlx_repo,
        "task": task,
        "verbose": False,
        "condition_on_previous_text": condition_on_previous_text,
    }
    if language:
        transcribe_kwargs["language"] = language
    if not traditional and (not language or language.startswith("zh")):
        transcribe_kwargs["initial_prompt"] = _SIMPLIFIED_CHINESE_PROMPT
    load_elapsed = time.monotonic() - load_started
    duration = media_duration_seconds(audio_path)
    print(f"Transcribing audio with mlx-whisper...", flush=True)
    transcribe_started = time.monotonic()
    result = _mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
    transcribe_elapsed = time.monotonic() - transcribe_started
    detected_language = result.get("language")
    print(f"Detected language: {detected_language}", flush=True)
    raw_segments = result.get("segments") or []
    subtitle_segments: list[SubtitleSegment] = []
    for seg in raw_segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        subtitle_segments.append(SubtitleSegment(start=seg["start"], end=seg["end"], text=text))
    if duration:
        print(f"Transcribing audio: 100% ({clock_timestamp(duration)}/{clock_timestamp(duration)}), elapsed {clock_timestamp(transcribe_elapsed)}", flush=True)

    # Clip transcriber — used by both fill_gaps and verify_and_retry
    def _mlx_transcribe_clip(clip_path: Path) -> list[SubtitleSegment]:
        clip_kwargs: dict = {
            "path_or_hf_repo": mlx_repo,
            "task": task,
            "verbose": False,
            "condition_on_previous_text": False,
        }
        if language:
            clip_kwargs["language"] = language
        if not traditional and (not language or language.startswith("zh")):
            clip_kwargs["initial_prompt"] = _SIMPLIFIED_CHINESE_PROMPT
        clip_result = _mlx_whisper.transcribe(str(clip_path), **clip_kwargs)
        return [
            SubtitleSegment(s["start"], s["end"], (s.get("text") or "").strip())
            for s in (clip_result.get("segments") or [])
            if (s.get("text") or "").strip()
        ]

    if gap_fill and duration and subtitle_segments:
        subtitle_segments = fill_gaps(subtitle_segments, audio_path, duration, gap_threshold, _mlx_transcribe_clip)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if verify and api_key:
        subtitle_segments = verify_and_retry(subtitle_segments, audio_path, _mlx_transcribe_clip, api_key, language)
    elif verify and not api_key:
        print("Skipping LLM verification: ANTHROPIC_API_KEY not set.", flush=True)

    subtitle_segments = retranscribe_hallucinations(subtitle_segments, audio_path, _mlx_transcribe_clip)

    written = write_srt_from_segments(subtitle_segments, srt_path)
    return written, {"model_load": load_elapsed, "transcribe": transcribe_elapsed}, subtitle_segments, detected_language


def _write_srt_faster_whisper(
    audio_path: Path,
    srt_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    task: str,
    language: str | None,
    gap_fill: bool = True,
    gap_threshold: float = 3.0,
    condition_on_previous_text: bool = False,
    verify: bool = True,
    traditional: bool = False,
) -> tuple[int, dict[str, float], list[SubtitleSegment], str | None]:
    model_load_started = time.monotonic()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    model_load_elapsed = time.monotonic() - model_load_started
    transcribe_args: dict = {
        "task": task,
        "beam_size": 5,
        "vad_filter": True,
        "condition_on_previous_text": condition_on_previous_text,
    }
    if language:
        transcribe_args["language"] = language
    if not traditional and (not language or language.startswith("zh")):
        transcribe_args["initial_prompt"] = _SIMPLIFIED_CHINESE_PROMPT
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

    subtitle_segments: list[SubtitleSegment] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        subtitle_segments.append(
            SubtitleSegment(start=segment.start, end=segment.end, text=text)
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

    # Clip transcriber — used by both fill_gaps and verify_and_retry
    def _fw_transcribe_clip(clip_path: Path) -> list[SubtitleSegment]:
        clip_args: dict = {
            "task": task,
            "beam_size": 5,
            "vad_filter": True,
            "condition_on_previous_text": False,
        }
        if language:
            clip_args["language"] = language
        if not traditional and (not language or language.startswith("zh")):
            clip_args["initial_prompt"] = _SIMPLIFIED_CHINESE_PROMPT
        clip_segments, _ = model.transcribe(str(clip_path), **clip_args)
        return [
            SubtitleSegment(s.start, s.end, s.text.strip())
            for s in clip_segments if s.text.strip()
        ]

    if gap_fill and duration and subtitle_segments:
        subtitle_segments = fill_gaps(subtitle_segments, audio_path, duration, gap_threshold, _fw_transcribe_clip)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if verify and api_key:
        subtitle_segments = verify_and_retry(subtitle_segments, audio_path, _fw_transcribe_clip, api_key, language)
    elif verify and not api_key:
        print("Skipping LLM verification: ANTHROPIC_API_KEY not set.", flush=True)

    subtitle_segments = retranscribe_hallucinations(subtitle_segments, audio_path, _fw_transcribe_clip)

    written = write_srt_from_segments(subtitle_segments, srt_path)
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
    parser.add_argument(
        "--no-gap-fill",
        action="store_true",
        help="Disable gap detection and re-transcription of skipped sections. Default: gap-fill is on.",
    )
    parser.add_argument(
        "--gap-threshold",
        type=float,
        default=3.0,
        metavar="SECS",
        help="Minimum silence gap in seconds to trigger re-transcription. Default: 3.0",
    )
    parser.add_argument(
        "--condition-on-previous-text",
        action="store_true",
        help="Re-enable Whisper conditioning on its own previous output (may cause repetition loops). Default: off.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable LLM semantic verification and retry of suspect segments. Default: on if ANTHROPIC_API_KEY is set.",
    )
    parser.add_argument(
        "--traditional",
        action="store_true",
        help="Output traditional Chinese characters instead of simplified. Default: simplified Chinese.",
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
            gap_fill=not args.no_gap_fill,
            gap_threshold=args.gap_threshold,
            condition_on_previous_text=args.condition_on_previous_text,
            verify=not args.no_verify,
            traditional=args.traditional,
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
