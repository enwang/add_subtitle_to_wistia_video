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
    fill_gaps,
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
    test_fix2_condition_on_previous_text_param()
    test_fix3_gap_detected_and_filled()
    test_fix3_leading_gap_filled()
    test_fix3_no_gap_passthrough()
    test_fix3_failed_clip_graceful()
    test_integration_srt_output()

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
