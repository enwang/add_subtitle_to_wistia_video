#!/usr/bin/env python3
"""
Test script for the 3 subtitle quality fixes:
  Fix 1 — Exact duplicate merging (extend end time instead of drop)
  Fix 2 — condition_on_previous_text=False (verified via transcription kwargs)
  Fix 3 — Gap detection + re-transcription (fill_gaps)

Run:  python3 test_fixes.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# ── import the functions under test ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from wistia_srt import (
    SubtitleSegment,
    collapse_repetition_loops,
    download_google_drive_file,
    extract_youtube_video_id,
    extract_wistia_media_id,
    fill_gaps,
    ffmpeg_subtitle_video_filter,
    is_hallucination,
    normalize_input_url,
    resolve_google_drive_virus_warning_url,
    resolve_google_drive_download_url,
    resolve_wistia_mp4_url,
    sanitize_segments,
    timestamp,
    write_srt_from_segments,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))
    _results.append((name, condition))


# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 — Exact duplicate: extend end time instead of drop
# ══════════════════════════════════════════════════════════════════════════════
def test_fix1_exact_duplicate_extended():
    print("\n── Fix 1: Exact duplicate → extend end time ─────────────────────────────")

    segments = [
        SubtitleSegment(start=1.0, end=3.0, text="你好"),
        SubtitleSegment(start=3.0, end=5.0, text="你好"),   # exact dup
        SubtitleSegment(start=5.0, end=7.0, text="歡迎"),
    ]
    cleaned = sanitize_segments(segments)

    check("Duplicate removed from output list",
          len(cleaned) == 2)
    check("Remaining segment 0 text is correct",
          cleaned[0].text == "你好")
    check("Duplicate end time merged (prev.end extended to 5.0)",
          cleaned[0].end == 5.0,
          f"got end={cleaned[0].end}")
    check("Non-duplicate segment preserved",
          cleaned[1].text == "歡迎")


def test_fix1_triple_duplicate():
    print("\n── Fix 1: Triple duplicate → single segment with max end time ───────────")

    segments = [
        SubtitleSegment(start=0.0, end=2.0, text="重複"),
        SubtitleSegment(start=2.0, end=4.0, text="重複"),
        SubtitleSegment(start=4.0, end=6.0, text="重複"),
        SubtitleSegment(start=6.0, end=8.0, text="結束"),
    ]
    cleaned = sanitize_segments(segments)

    check("All three duplicates collapsed to one",
          len(cleaned) == 2)
    check("Collapsed segment end time = 6.0",
          cleaned[0].end == 6.0,
          f"got end={cleaned[0].end}")
    check("Non-duplicate follows",
          cleaned[1].text == "結束")


def test_fix1_no_false_positives():
    print("\n── Fix 1: No duplicates → nothing removed ───────────────────────────────")

    segments = [
        SubtitleSegment(start=0.0, end=2.0, text="第一"),
        SubtitleSegment(start=2.0, end=4.0, text="第二"),
        SubtitleSegment(start=4.0, end=6.0, text="第三"),
    ]
    cleaned = sanitize_segments(segments)

    check("All 3 segments preserved",
          len(cleaned) == 3)
    check("End times unchanged",
          cleaned[0].end == 2.0 and cleaned[1].end == 4.0)


# ══════════════════════════════════════════════════════════════════════════════
# Fix 1b — Overlapping timestamps fixed
# ══════════════════════════════════════════════════════════════════════════════
def test_fix1b_overlap():
    print("\n── Fix 1b: Overlapping timestamps → start adjusted ──────────────────────")

    segments = [
        SubtitleSegment(start=0.0, end=5.0, text="長段落"),
        SubtitleSegment(start=3.0, end=7.0, text="新段落"),   # overlaps
    ]
    cleaned = sanitize_segments(segments)

    check("Both segments retained",
          len(cleaned) == 2)
    check("Second segment start adjusted to prev.end (5.0)",
          cleaned[1].start == 5.0,
          f"got start={cleaned[1].start}")
    check("Second segment end preserved (7.0)",
          cleaned[1].end == 7.0)


# ══════════════════════════════════════════════════════════════════════════════
# Fix 1c — Near-duplicate (containment) merged
# ══════════════════════════════════════════════════════════════════════════════
def test_fix1c_containment():
    print("\n── Fix 1c: Near-duplicate containment → merged ──────────────────────────")

    # Case A: seg.text is fully contained in prev.text → drop seg
    segments = [
        SubtitleSegment(start=0.0, end=3.0, text="今天天氣很好"),
        SubtitleSegment(start=3.0, end=5.0, text="天氣很好"),   # contained in prev
    ]
    cleaned = sanitize_segments(segments)
    check("Redundant contained segment dropped",
          len(cleaned) == 1)
    check("Longer segment kept",
          cleaned[0].text == "今天天氣很好")

    # Case B: prev.text is contained in seg.text → replace prev with seg
    segments2 = [
        SubtitleSegment(start=0.0, end=3.0, text="天氣很好"),
        SubtitleSegment(start=3.0, end=5.0, text="今天天氣很好"),  # longer, contains prev
    ]
    cleaned2 = sanitize_segments(segments2)
    check("Previous shorter segment replaced by longer",
          len(cleaned2) == 1)
    check("Longer text kept",
          cleaned2[0].text == "今天天氣很好")
    check("Start time kept from original first segment",
          cleaned2[0].start == 0.0)


def test_fix1d_phrase_repetition_loop_collapsed():
    print("\n── Fix 1d: Phrase repetition loop → collapsed before SRT ────────────────")

    bad_text = (
        "所以这些是旧息所导致的市场回报,一些很强势的股票,"
        "这个机会有回报,不知道会不会是什么,但是可能会是什么,"
        "可能会是什么,可能会是什么,可能会是什么,可能会是什么"
    )
    collapsed, changed = collapse_repetition_loops(bad_text)

    check("Phrase loop detected",
          changed)
    check("Repeated tail collapsed to one copy",
          collapsed.count("可能会是什么") == 1,
          collapsed)
    check("Useful prefix preserved",
          "这个机会有回报" in collapsed)

    cleaned = sanitize_segments([SubtitleSegment(start=0.0, end=8.0, text=bad_text)])
    check("Sanitized segment remains usable after collapse",
          len(cleaned) == 1 and cleaned[0].text == collapsed)


def test_fix1e_phrase_repetition_loop_is_hallucination():
    print("\n── Fix 1e: Phrase repetition loop → hallucination signal ────────────────")

    check("Repeated phrase is flagged",
          is_hallucination("可能会是什么,可能会是什么,可能会是什么,可能会是什么"))
    check("Natural repeated finance wording is preserved",
          not is_hallucination("这个位置可能会有反弹, 但需要等确认, 可能会有机会"))


# ══════════════════════════════════════════════════════════════════════════════
# Fix 2 — condition_on_previous_text param threading
# ══════════════════════════════════════════════════════════════════════════════
def test_fix2_condition_on_previous_text_param():
    print("\n── Fix 2: condition_on_previous_text threading ───────────────────────────")

    # Inspect the source to confirm the param is wired in both backends
    import inspect
    import wistia_srt

    mlx_src = inspect.getsource(wistia_srt._write_srt_mlx)
    fw_src = inspect.getsource(wistia_srt._write_srt_faster_whisper)

    check("MLX backend accepts condition_on_previous_text param",
          "condition_on_previous_text" in inspect.signature(wistia_srt._write_srt_mlx).parameters)
    check("MLX backend passes it to transcribe_kwargs",
          '"condition_on_previous_text": condition_on_previous_text' in mlx_src
          or "'condition_on_previous_text': condition_on_previous_text" in mlx_src)
    check("faster-whisper backend accepts condition_on_previous_text param",
          "condition_on_previous_text" in inspect.signature(wistia_srt._write_srt_faster_whisper).parameters)
    check("faster-whisper backend passes it to transcribe_args",
          '"condition_on_previous_text": condition_on_previous_text' in fw_src
          or "'condition_on_previous_text': condition_on_previous_text" in fw_src)
    check("write_srt() defaults condition_on_previous_text=False",
          inspect.signature(wistia_srt.write_srt).parameters["condition_on_previous_text"].default is False)
    check("CLI --condition-on-previous-text flag exists in parse_args source",
          "condition-on-previous-text" in inspect.getsource(wistia_srt.parse_args))


# ══════════════════════════════════════════════════════════════════════════════
# Fix 3 — Gap detection + re-transcription (fill_gaps with mock)
# ══════════════════════════════════════════════════════════════════════════════
def test_fix3_gap_detected_and_filled():
    print("\n── Fix 3: Gap detected and filled with mock transcribe_clip ─────────────")

    # Segments with a 10-second gap between them
    segments = [
        SubtitleSegment(start=0.0,  end=3.0,  text="開始"),
        SubtitleSegment(start=13.0, end=16.0, text="結束"),   # 10s gap after 3.0
    ]
    audio_path = Path("/dev/null")  # won't actually be read by the mock
    audio_duration = 20.0
    gap_threshold = 3.0

    calls: list[tuple[float, float]] = []   # track which clips were requested

    def mock_transcribe_clip(clip_path: Path) -> list[SubtitleSegment]:
        # Parse gap start/end from filename (gap_0.m4a)
        # Instead, just return a fixed segment relative to clip start (t=0)
        calls.append(clip_path)
        return [SubtitleSegment(start=1.0, end=4.0, text="中間內容")]

    # Patch extract_audio_clip so it doesn't actually call ffmpeg
    import wistia_srt as _mod
    original_extract = _mod.extract_audio_clip

    def mock_extract(audio_path, start, end, dest):
        dest.touch()   # create empty file so clip_path exists

    _mod.extract_audio_clip = mock_extract
    try:
        result = fill_gaps(segments, audio_path, audio_duration, gap_threshold, mock_transcribe_clip)
    finally:
        _mod.extract_audio_clip = original_extract

    check("fill_gaps called transcribe_clip once for the gap",
          len(calls) == 1)
    check("Result has 3 segments (2 original + 1 recovered)",
          len(result) == 3,
          f"got {len(result)}")
    check("Recovered segment inserted in chronological order",
          result[1].text == "中間內容")
    check("Recovered segment start time offset by gap_start (3.0 + 1.0 = 4.0)",
          result[1].start == 4.0,
          f"got start={result[1].start}")
    check("Recovered segment end time offset correctly (3.0 + 4.0 = 7.0)",
          result[1].end == 7.0,
          f"got end={result[1].end}")
    check("Original segments preserved around recovered segment",
          result[0].text == "開始" and result[2].text == "結束")


def test_fix3_leading_gap_filled():
    print("\n── Fix 3: Leading gap (before first segment) filled ─────────────────────")

    segments = [
        SubtitleSegment(start=8.0, end=11.0, text="晚到的字幕"),
    ]

    import wistia_srt as _mod
    original_extract = _mod.extract_audio_clip

    def mock_extract(audio_path, start, end, dest):
        dest.touch()

    recovered_segments: list[SubtitleSegment] = []

    def mock_transcribe_clip(clip_path: Path) -> list[SubtitleSegment]:
        recovered_segments.append(clip_path)
        return [SubtitleSegment(start=1.0, end=4.0, text="開頭")]

    _mod.extract_audio_clip = mock_extract
    try:
        result = fill_gaps(segments, Path("/dev/null"), 15.0, 3.0, mock_transcribe_clip)
    finally:
        _mod.extract_audio_clip = original_extract

    check("Leading gap triggered re-transcription",
          len(recovered_segments) == 1)
    check("Recovered leading segment inserted at start",
          result[0].text == "開頭")
    check("Recovered leading segment start = 0.0 + 1.0 = 1.0",
          result[0].start == 1.0,
          f"got start={result[0].start}")
    check("Original segment preserved at end",
          result[-1].text == "晚到的字幕")


def test_fix3_no_gap_passthrough():
    print("\n── Fix 3: No gaps → segments returned unchanged ─────────────────────────")

    segments = [
        SubtitleSegment(start=0.0, end=2.0, text="A"),
        SubtitleSegment(start=2.0, end=4.0, text="B"),
        SubtitleSegment(start=4.0, end=6.0, text="C"),
    ]

    called = []

    def mock_transcribe_clip(clip_path: Path) -> list[SubtitleSegment]:
        called.append(clip_path)
        return []

    result = fill_gaps(segments, Path("/dev/null"), 6.0, 3.0, mock_transcribe_clip)

    check("transcribe_clip never called (no gaps)",
          len(called) == 0)
    check("Segments returned unchanged",
          result is segments or result == segments)


def test_fix3_failed_clip_graceful():
    print("\n── Fix 3: Clip extraction failure → graceful fallback ───────────────────")

    segments = [
        SubtitleSegment(start=0.0, end=2.0, text="before"),
        SubtitleSegment(start=10.0, end=12.0, text="after"),
    ]

    import wistia_srt as _mod
    original_extract = _mod.extract_audio_clip

    def mock_extract_fail(audio_path, start, end, dest):
        raise RuntimeError("ffmpeg failed")

    _mod.extract_audio_clip = mock_extract_fail
    try:
        result = fill_gaps(segments, Path("/dev/null"), 15.0, 3.0, lambda p: [])
    finally:
        _mod.extract_audio_clip = original_extract

    check("Original segments returned despite clip failure",
          result == segments or (len(result) == 2 and result[0].text == "before"))


# ══════════════════════════════════════════════════════════════════════════════
# Integration: write_srt_from_segments produces valid SRT with fixes applied
# ══════════════════════════════════════════════════════════════════════════════
def test_integration_srt_output():
    print("\n── Integration: SRT file output with all fixes ───────────────────────────")

    segments = [
        SubtitleSegment(start=0.0,  end=2.0, text="第一句"),
        SubtitleSegment(start=2.0,  end=4.0, text="第二句"),
        SubtitleSegment(start=4.0,  end=6.0, text="第二句"),    # exact dup → extend
        SubtitleSegment(start=5.0,  end=8.0, text="第三句"),    # overlaps previous
        SubtitleSegment(start=8.0,  end=10.0, text="第四句"),
    ]

    with tempfile.NamedTemporaryFile(suffix=".srt", mode="r", delete=False) as f:
        srt_path = Path(f.name)

    written = write_srt_from_segments(segments, srt_path)
    content = srt_path.read_text(encoding="utf-8")
    srt_path.unlink()

    lines = [l for l in content.splitlines() if l.strip()]
    check("SRT written with correct segment count",
          written == 4,
          f"got {written}")
    check("SRT starts with index 1",
          lines[0] == "1")
    check("SRT contains all 4 unique texts",
          all(t in content for t in ["第一句", "第二句", "第三句", "第四句"]))
    check("Duplicate extended: 第二句 spans 00:00:02,000 --> 00:00:06,000 in SRT",
          "00:00:02,000 --> 00:00:06,000" in content,
          f"content snippet: {[l for l in content.splitlines() if '第二句' in l or '00:00:0' in l][:6]}")


# ══════════════════════════════════════════════════════════════════════════════
# Wistia resolver: direct MP4 avoids slow HLS segment downloads
# ══════════════════════════════════════════════════════════════════════════════
def test_wistia_resolver_selects_direct_mp4():
    print("\n── Wistia resolver: iframe/media URL → direct MP4 ───────────────────────")

    check("Iframe URL media id extracted",
          extract_wistia_media_id("https://fast.wistia.net/embed/iframe/mroy9jmeg2") == "mroy9jmeg2")
    check("Media playlist URL media id extracted",
          extract_wistia_media_id("https://fast.wistia.net/embed/medias/mroy9jmeg2.m3u8") == "mroy9jmeg2")

    import wistia_srt as _mod
    original_fetch_json = _mod.fetch_json

    def mock_fetch_json(url: str, timeout: float = 20.0) -> dict:
        return {
            "media": {
                "assets": [
                    {
                        "display_name": "360p",
                        "container": "mp4",
                        "ext": "mp4",
                        "height": 360,
                        "width": 640,
                        "size": 68_000_000,
                        "url": "https://example.test/360.bin",
                    },
                    {
                        "display_name": "540p",
                        "container": "mp4",
                        "ext": "mp4",
                        "height": 540,
                        "width": 960,
                        "size": 99_000_000,
                        "url": "https://example.test/540.bin",
                    },
                    {
                        "display_name": "720p",
                        "container": "mp4",
                        "ext": "mp4",
                        "height": 720,
                        "width": 1280,
                        "size": 130_000_000,
                        "url": "https://example.test/720.bin",
                    },
                ]
            }
        }

    _mod.fetch_json = mock_fetch_json
    try:
        default_resolved = resolve_wistia_mp4_url("https://fast.wistia.net/embed/iframe/mroy9jmeg2")
        height_resolved = resolve_wistia_mp4_url("https://fast.wistia.net/embed/iframe/mroy9jmeg2", 540)
    finally:
        _mod.fetch_json = original_fetch_json

    check("Default selects highest available direct MP4",
          default_resolved == "https://example.test/720.bin",
          f"got {default_resolved}")
    check("Target height selects closest direct MP4",
          height_resolved == "https://example.test/540.bin",
          f"got {height_resolved}")


# ══════════════════════════════════════════════════════════════════════════════
# YouTube resolver: supported URL shapes share the same subtitle pipeline
# ══════════════════════════════════════════════════════════════════════════════
def test_youtube_video_id_extraction():
    print("\n── YouTube resolver: URL → video id ─────────────────────────────────────")

    check("Standard watch URL video id extracted",
          extract_youtube_video_id("https://www.youtube.com/watch?is=9f-MWUHaQxVoAX8R&v=2-dKidjsu9I&feature=youtu.be") == "2-dKidjsu9I")
    check("Short youtu.be URL video id extracted",
          extract_youtube_video_id("https://youtu.be/2-dKidjsu9I") == "2-dKidjsu9I")
    check("Shorts URL video id extracted",
          extract_youtube_video_id("https://youtube.com/shorts/2-dKidjsu9I?feature=share") == "2-dKidjsu9I")
    check("Non-YouTube URL ignored",
          extract_youtube_video_id("https://fast.wistia.net/embed/iframe/mroy9jmeg2") is None)


def test_input_url_normalization():
    print("\n── Input URL normalization: markdown links and escapes ──────────────────")

    markdown = "[https://drive.google.com/file/d/1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-\\_aMy/view](https://drive.google.com/file/d/1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-_aMy/view)"
    normalized = normalize_input_url(markdown)
    check("Markdown link unwraps to target URL",
          normalized == "https://drive.google.com/file/d/1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-_aMy/view",
          normalized)
    check("Escaped underscore is restored",
          normalize_input_url("https://example.com/a\\_b") == "https://example.com/a_b")


def test_existing_subtitle_cover_filter():
    print("\n── Subtitle burn filter: cover existing hard subtitles ──────────────────")

    srt = Path("/tmp/example subtitles.srt")
    default_filter = ffmpeg_subtitle_video_filter(srt)
    cover_filter = ffmpeg_subtitle_video_filter(
        srt,
        cover_existing_subtitles=True,
        subtitle_cover_height=0.35,
    )
    clamped_filter = ffmpeg_subtitle_video_filter(
        srt,
        cover_existing_subtitles=True,
        subtitle_cover_height=0.90,
    )

    check("Default filter does not cover source video",
          default_filter.startswith("subtitles=") and "drawbox" not in default_filter,
          default_filter)
    check("Cover filter draws black box before burning new subtitles",
          cover_filter.startswith("drawbox=") and ",subtitles=" in cover_filter,
          cover_filter)
    check("Cover height controls bottom area",
          "y=ih*0.6500" in cover_filter and "h=ih*0.3500" in cover_filter,
          cover_filter)
    check("Cover height is clamped to avoid covering most of the video",
          "h=ih*0.6000" in clamped_filter,
          clamped_filter)


# ══════════════════════════════════════════════════════════════════════════════
# Google Drive resolver: private files fail early with a useful message
# ══════════════════════════════════════════════════════════════════════════════
def test_google_drive_private_file_error():
    print("\n── Google Drive resolver: private file → clear permission error ─────────")

    import subprocess
    import wistia_srt as _mod
    original_check_output = _mod.subprocess.check_output

    def mock_check_output(cmd, text=True):
        return '<html><head><base href="https://accounts.google.com/v3/signin/"></head></html>'

    _mod.subprocess.check_output = mock_check_output
    try:
        try:
            resolve_google_drive_download_url("https://drive.google.com/file/d/1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-_aMy/view")
        except PermissionError as exc:
            message = str(exc)
            check("Private Drive file raises PermissionError", True)
            check("Error explains sharing requirement",
                  "Anyone with the link" in message and "1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-_aMy" in message,
                  message)
        except Exception as exc:
            check("Private Drive file raises PermissionError", False, type(exc).__name__)
        else:
            check("Private Drive file raises PermissionError", False, "no exception")
    finally:
        _mod.subprocess.check_output = original_check_output


def test_google_drive_private_file_uses_chrome_cookie_fallback():
    print("\n── Google Drive resolver: private file → Chrome cookie fallback ─────────")

    import wistia_srt as _mod
    original_oauth_client_path = _mod.google_drive_oauth_client_path
    original_resolve = _mod.resolve_google_drive_download_url
    original_cookie_download = _mod.download_google_drive_with_browser_cookies

    calls: list[tuple[str, Path, str]] = []

    def mock_oauth_client_path(explicit_path=None):
        return None

    def mock_resolve(url: str) -> str:
        raise PermissionError("private")

    def mock_cookie_download(url: str, destination: Path, browser: str = "chrome") -> None:
        calls.append((browser, destination, url))
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    _mod.google_drive_oauth_client_path = mock_oauth_client_path
    _mod.resolve_google_drive_download_url = mock_resolve
    _mod.download_google_drive_with_browser_cookies = mock_cookie_download
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        dest = Path(tmp.name)
    dest.unlink(missing_ok=True)
    try:
        download_google_drive_file("https://drive.google.com/file/d/1SYKC3ZjpwtOSCWXYcBKf9W8NlnD-_aMy/view", dest)
        check("Chrome cookie fallback was called",
              len(calls) == 1 and calls[0][0] == "chrome")
        check("Fallback output was accepted as non-HTML video-like file",
              dest.exists() and dest.stat().st_size > 0)
    finally:
        dest.unlink(missing_ok=True)
        _mod.google_drive_oauth_client_path = original_oauth_client_path
        _mod.resolve_google_drive_download_url = original_resolve
        _mod.download_google_drive_with_browser_cookies = original_cookie_download


def test_google_drive_cookie_fallback_normalizes_markdown_url():
    print("\n── Google Drive resolver: cookie fallback normalizes input URL ──────────")

    import wistia_srt as _mod
    original_yt_dlp_command = _mod.yt_dlp_command
    original_run = _mod.run

    captured: list[list[str]] = []

    def mock_yt_dlp_command() -> list[str]:
        return ["yt-dlp"]

    def mock_run(cmd: list[str]) -> None:
        captured.append(cmd)

    _mod.yt_dlp_command = mock_yt_dlp_command
    _mod.run = mock_run
    try:
        _mod.download_google_drive_with_browser_cookies(
            "[https://drive.google.com/file/d/1\\_hp\\_IhRp3YOfufdzIuSUlmoV4F1of\\_Ir/view](https://drive.google.com/file/d/1_hp_IhRp3YOfufdzIuSUlmoV4F1of_Ir/view)",
            Path("/tmp/out.mp4"),
        )
        check("yt-dlp receives pure URL after normalization",
              captured and captured[0][-1] == "https://drive.google.com/file/d/1_hp_IhRp3YOfufdzIuSUlmoV4F1of_Ir/view",
              str(captured[0] if captured else []))
    finally:
        _mod.yt_dlp_command = original_yt_dlp_command
        _mod.run = original_run


def test_google_drive_cached_file_fallback():
    print("\n── Google Drive resolver: cached local file fallback ────────────────────")

    import wistia_srt as _mod
    original_cache_path = _mod._DRIVE_CACHE_PATH
    original_oauth_client_path = _mod.google_drive_oauth_client_path
    original_resolve = _mod.resolve_google_drive_download_url
    original_cookie_download = _mod.download_google_drive_with_browser_cookies

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        cached = tmpdir_path / "cached.mov"
        cached.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        cache_path = tmpdir_path / "drive_cache.json"
        cache_path.write_text(
            '{"file123": "' + str(cached).replace("\\", "\\\\") + '"}',
            encoding="utf-8",
        )
        dest = tmpdir_path / "dest.mp4"

        def mock_resolve(url: str) -> str:
            raise AssertionError("network should not be used when cache exists")

        def mock_oauth_client_path(explicit_path=None):
            return None

        def mock_cookie_download(url: str, destination: Path, browser: str = "chrome") -> None:
            raise AssertionError("cookie fallback should not be used when cache exists")

        _mod._DRIVE_CACHE_PATH = cache_path
        _mod.google_drive_oauth_client_path = mock_oauth_client_path
        _mod.resolve_google_drive_download_url = mock_resolve
        _mod.download_google_drive_with_browser_cookies = mock_cookie_download
        try:
            download_google_drive_file("https://drive.google.com/file/d/file123/view", dest)
            check("Cached Drive file copied to destination",
                  dest.exists() and dest.read_bytes() == cached.read_bytes())
        finally:
            _mod._DRIVE_CACHE_PATH = original_cache_path
            _mod.google_drive_oauth_client_path = original_oauth_client_path
            _mod.resolve_google_drive_download_url = original_resolve
            _mod.download_google_drive_with_browser_cookies = original_cookie_download


def test_google_drive_oauth_preferred_when_configured():
    print("\n── Google Drive resolver: OAuth preferred when configured ───────────────")

    import wistia_srt as _mod
    original_oauth_client_path = _mod.google_drive_oauth_client_path
    original_oauth_download = _mod.download_google_drive_with_oauth
    original_cache = _mod.copy_cached_google_drive_file
    original_resolve = _mod.resolve_google_drive_download_url

    calls: list[tuple[str, Path, Path]] = []

    def mock_oauth_client_path(explicit_path=None):
        return Path("/tmp/client_secret.json")

    def mock_oauth_download(file_id: str, destination: Path, client_secret_path: Path) -> None:
        calls.append((file_id, destination, client_secret_path))
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def fail_cache(file_id: str, destination: Path) -> bool:
        raise AssertionError("cache should not run before OAuth")

    def fail_resolve(url: str) -> str:
        raise AssertionError("direct resolver should not run before OAuth")

    _mod.google_drive_oauth_client_path = mock_oauth_client_path
    _mod.download_google_drive_with_oauth = mock_oauth_download
    _mod.copy_cached_google_drive_file = fail_cache
    _mod.resolve_google_drive_download_url = fail_resolve
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        dest = Path(tmp.name)
    dest.unlink(missing_ok=True)
    try:
        download_google_drive_file("https://drive.google.com/file/d/file123/view", dest)
        check("OAuth download was used",
              len(calls) == 1 and calls[0][0] == "file123")
        check("OAuth output accepted",
              dest.exists() and dest.stat().st_size > 0)
    finally:
        dest.unlink(missing_ok=True)
        _mod.google_drive_oauth_client_path = original_oauth_client_path
        _mod.download_google_drive_with_oauth = original_oauth_download
        _mod.copy_cached_google_drive_file = original_cache
        _mod.resolve_google_drive_download_url = original_resolve


def test_google_drive_virus_warning_resolves_confirm_url():
    print("\n── Google Drive resolver: virus warning → confirm download URL ──────────")

    import wistia_srt as _mod
    original_check_output = _mod.subprocess.check_output

    warning_html = """
    <html><body>
      <p>Google Drive can't scan this file for viruses.</p>
      <p>Would you still like to download this file?</p>
      <form action="https://drive.usercontent.google.com/download" method="get">
        <input type="hidden" name="id" value="1_hp_IhRp3YOfufdzIuSUlmoV4F1of_Ir">
        <input type="hidden" name="export" value="download">
        <input type="hidden" name="confirm" value="t">
        <input type="hidden" name="uuid" value="abc123">
      </form>
      <button id="uc-download-link">Download anyway</button>
    </body></html>
    """

    def mock_check_output(cmd, text=True):
        return warning_html

    _mod.subprocess.check_output = mock_check_output
    try:
        resolved = resolve_google_drive_download_url("https://drive.google.com/file/d/1_hp_IhRp3YOfufdzIuSUlmoV4F1of_Ir/view")
        check("Virus warning resolves to usercontent download endpoint",
              resolved.startswith("https://drive.usercontent.google.com/download?"),
              resolved)
        check("Confirm token preserved",
              "confirm=t" in resolved and "uuid=abc123" in resolved,
              resolved)
    finally:
        _mod.subprocess.check_output = original_check_output


def test_google_drive_virus_warning_link_fallback():
    print("\n── Google Drive resolver: virus warning link fallback ───────────────────")

    html = """
    <html><body>
      Google Drive can't scan this file for viruses.
      <a id="uc-download-link" href="/download?id=file123&export=download&confirm=t">Download anyway</a>
    </body></html>
    """
    resolved = resolve_google_drive_virus_warning_url(
        html,
        "https://drive.usercontent.google.com/download?id=file123&export=download",
        "file123",
    )
    check("Virus warning link fallback resolves relative URL",
          resolved == "https://drive.usercontent.google.com/download?id=file123&export=download&confirm=t",
          str(resolved))


# ══════════════════════════════════════════════════════════════════════════════
# Run all tests
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 70)
    print("Subtitle Fix Tests")
    print("=" * 70)

    test_fix1_exact_duplicate_extended()
    test_fix1_triple_duplicate()
    test_fix1_no_false_positives()
    test_fix1b_overlap()
    test_fix1c_containment()
    test_fix1d_phrase_repetition_loop_collapsed()
    test_fix1e_phrase_repetition_loop_is_hallucination()
    test_fix2_condition_on_previous_text_param()
    test_fix3_gap_detected_and_filled()
    test_fix3_leading_gap_filled()
    test_fix3_no_gap_passthrough()
    test_fix3_failed_clip_graceful()
    test_integration_srt_output()
    test_wistia_resolver_selects_direct_mp4()
    test_youtube_video_id_extraction()
    test_input_url_normalization()
    test_existing_subtitle_cover_filter()
    test_google_drive_private_file_error()
    test_google_drive_private_file_uses_chrome_cookie_fallback()
    test_google_drive_cookie_fallback_normalizes_markdown_url()
    test_google_drive_cached_file_fallback()
    test_google_drive_oauth_preferred_when_configured()
    test_google_drive_virus_warning_resolves_confirm_url()
    test_google_drive_virus_warning_link_fallback()

    print("\n" + "=" * 70)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    print(f"Results: {passed} passed, {failed} failed  ({len(_results)} total)")
    if failed:
        print("\nFailed tests:")
        for name, ok in _results:
            if not ok:
                print(f"  - {name}")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)
