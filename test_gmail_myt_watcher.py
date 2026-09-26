#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

import gmail_myt_watcher as watcher


def encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Messages:
    def __init__(self, messages):
        self.messages = messages

    def list(self, **_kwargs):
        return Request({"messages": [{"id": item} for item in self.messages]})

    def get(self, *, id, **_kwargs):
        return Request(self.messages[id])


class Users:
    def __init__(self, messages):
        self._messages = Messages(messages)

    def messages(self):
        return self._messages


class Service:
    def __init__(self, messages):
        self._users = Users(messages)

    def users(self):
        return self._users


def message(subject: str, body: str) -> dict:
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "mimeType": "text/plain",
            "body": {"data": encoded(body)},
        }
    }


def test_supported_video_urls() -> None:
    urls = watcher.supported_video_urls(
        "A https://fast.wistia.net/embed/iframe/abc, "
        "B https://example.com/nope "
        "C https://www.youtube.com/watch?v=123"
    )
    assert urls == [
        "https://fast.wistia.net/embed/iframe/abc",
        "https://www.youtube.com/watch?v=123",
    ]


def test_html_button_url_is_preserved() -> None:
    payload = {
        "mimeType": "text/html",
        "body": {
            "data": encoded(
                '<a href="https://fast.wistia.net/embed/iframe/hidden123">Watch</a>'
            )
        },
    }
    assert watcher.supported_video_urls(watcher.message_text(payload)) == [
        "https://fast.wistia.net/embed/iframe/hidden123"
    ]


def test_output_name_for_subject() -> None:
    assert watcher.output_name_for_subject("[2026-09-20 大盤板塊方向]") == (
        "2026-09-20_大盘.mp4"
    )
    assert watcher.output_name_for_subject("[2026-09-20 圖表教學]") == (
        "2026-09-20_图表.mp4"
    )
    assert watcher.output_name_for_subject("[2026-09-15 Live Coaching]") == (
        "2026-09-15_Q&A.mp4"
    )
    assert watcher.output_name_for_subject("[2026-08-22 大盤及圖表]") == (
        "2026-08-22_大盘+图表.mp4"
    )


def test_initial_run_only_creates_baseline() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "state.json"
        service = Service({"old": message("[MYT - JL] video", "https://youtu.be/abc")})
        calls = []
        original = watcher.run_translation
        watcher.run_translation = lambda *args: calls.append(args)
        try:
            assert watcher.process_once(service, state_path, Path("script.py"), "query", []) == 0
        finally:
            watcher.run_translation = original
        state = json.loads(state_path.read_text())
        assert state["initialized"] is True
        assert state["processed_message_ids"] == ["old"]
        assert calls == []


def test_new_myt_video_runs_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "state.json"
        state_path.write_text(
            json.dumps({"initialized": True, "processed_message_ids": [], "processed_urls": []})
        )
        service = Service({
            "new": message("[MYT - JL] coaching", "https://fast.wistia.net/embed/iframe/abc")
        })
        calls = []
        original = watcher.run_translation
        watcher.run_translation = lambda *args: calls.append(args)
        try:
            assert watcher.process_once(service, state_path, Path("script.py"), "query", ["--model", "turbo"]) == 1
            assert watcher.process_once(service, state_path, Path("script.py"), "query", ["--model", "turbo"]) == 0
        finally:
            watcher.run_translation = original
        assert len(calls) == 1
        assert calls[0][1] == "https://fast.wistia.net/embed/iframe/abc"


def test_notify_user_invokes_macos_notification() -> None:
    calls = []
    original = subprocess.run
    subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs)) or subprocess.CompletedProcess(args, 0)
    try:
        assert watcher.notify_user("download failed", "video processing failed") is True
    finally:
        subprocess.run = original
    assert calls[0][0][0][0] == "/usr/bin/osascript"
    assert "download failed" in calls[0][0][0]


if __name__ == "__main__":
    test_supported_video_urls()
    test_html_button_url_is_preserved()
    test_output_name_for_subject()
    test_initial_run_only_creates_baseline()
    test_new_myt_video_runs_once()
    test_notify_user_invokes_macos_notification()
    print("6 passed")
