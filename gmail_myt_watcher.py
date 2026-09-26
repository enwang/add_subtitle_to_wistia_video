#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CLIENT_PATH = Path.home() / ".config" / "jlaw_video" / "google_drive_client_secret.json"
DEFAULT_TOKEN_PATH = Path.home() / ".cache" / "jlaw_video" / "google_gmail_token.json"
DEFAULT_STATE_PATH = Path.home() / ".cache" / "jlaw_video" / "myt_mail_state.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "JLaw Videos"
GMAIL_SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]
SUBJECT_MARKER = "[MYT - JL]"
SUPPORTED_HOSTS = {
    "fast.wistia.net",
    "fast.wistia.com",
    "www.youtube.com",
    "youtube.com",
    "youtu.be",
    "drive.google.com",
    "www.drive.google.com",
}
URL_RE = re.compile(r"https?://[^\s<>\"']+")
NOTIFICATION_TITLE = "JLaw 视频自动处理"


def notify_user(message: str, subtitle: str = "") -> bool:
    """Show a best-effort macOS notification without hiding the original error."""
    osascript = Path("/usr/bin/osascript")
    if not osascript.exists():
        return False
    script = (
        "on run argv\n"
        "display notification (item 1 of argv) with title (item 2 of argv) "
        "subtitle (item 3 of argv)\n"
        "end run"
    )
    try:
        result = subprocess.run(
            [str(osascript), "-e", script, message[:240], NOTIFICATION_TITLE, subtitle],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def oauth_dependencies():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Missing Gmail OAuth dependencies. Install them with: "
            f"{sys.executable} -m pip install google-api-python-client "
            "google-auth-oauthlib google-auth-httplib2"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def gmail_service(client_path: Path, token_path: Path):
    Request, Credentials, InstalledAppFlow, build = oauth_dependencies()
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPE)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not client_path.exists():
            raise FileNotFoundError(f"Google OAuth client file not found: {client_path}")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), GMAIL_SCOPE)
        credentials = flow.run_local_server(port=0, open_browser=True)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json())
    os.chmod(token_path, 0o600)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def decode_body(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def message_text(payload: dict) -> str:
    chunks: list[str] = []

    def visit(part: dict) -> None:
        body = decode_body(part.get("body", {}).get("data"))
        mime_type = part.get("mimeType", "")
        if body and mime_type in {"text/plain", "text/html"}:
            if mime_type == "text/html":
                raw_html = html.unescape(body)
                chunks.append(raw_html)
                body = re.sub(r"<[^>]+>", " ", raw_html)
            chunks.append(body)
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    return "\n".join(chunks)


def header_value(payload: dict, name: str) -> str:
    target = name.lower()
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == target:
            return header.get("value", "")
    return ""


def supported_video_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(html.unescape(text)):
        url = match.rstrip(".,;:!?)]}")
        parsed = urlparse(url)
        if parsed.netloc.lower() not in SUPPORTED_HOSTS:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"initialized": False, "processed_message_ids": [], "processed_urls": []}
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"initialized": False, "processed_message_ids": [], "processed_urls": []}
    state.setdefault("initialized", False)
    state.setdefault("processed_message_ids", [])
    state.setdefault("processed_urls", [])
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    temp_path.replace(path)


def matching_message_ids(service, query: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        response = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=100
        ).execute()
        ids.extend(item["id"] for item in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return ids


def output_name_for_subject(subject: str) -> str | None:
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", subject)
    if not date_match:
        return None
    has_market = any(
        marker in subject
        for marker in ("大盤", "大盘", "板塊", "板块", "市況", "市况", "股市分析")
    )
    has_chart = "圖表" in subject or "图表" in subject
    has_qa = any(
        marker.lower() in subject.lower()
        for marker in ("Q&A", "問答", "问答", "Live Coaching")
    )
    if has_market and has_chart:
        category = "大盘+图表"
    elif has_market:
        category = "大盘"
    elif has_chart:
        category = "图表"
    elif has_qa:
        category = "Q&A"
    else:
        category = "其他"
    return f"{date_match.group(1)}_{category}.mp4"


def run_translation(
    script_path: Path,
    url: str,
    extra_args: list[str],
    output_path: Path | None = None,
) -> None:
    command = [sys.executable, str(script_path), url]
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["-o", str(output_path)])
    command.extend(extra_args)
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def process_once(
    service,
    state_path: Path,
    script_path: Path,
    query: str,
    extra_args: list[str],
    process_existing: bool = False,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> int:
    state = load_state(state_path)
    message_ids = matching_message_ids(service, query)
    processed_ids = list(dict.fromkeys(state["processed_message_ids"]))
    processed_id_set = set(processed_ids)
    processed_urls = list(dict.fromkeys(state["processed_urls"]))
    processed_url_set = set(processed_urls)

    if not state["initialized"] and not process_existing:
        state["initialized"] = True
        state["processed_message_ids"] = message_ids[-2000:]
        save_state(state_path, state)
        print(f"Initialized MYT mailbox baseline with {len(message_ids)} existing messages.")
        return 0

    completed = 0
    for message_id in reversed(message_ids):
        if message_id in processed_id_set:
            continue
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = message.get("payload", {})
        subject = header_value(payload, "Subject")
        if SUBJECT_MARKER not in subject:
            processed_ids.append(message_id)
            processed_id_set.add(message_id)
            continue
        urls = supported_video_urls(message_text(payload))
        if not urls:
            print(f"No supported video link in: {subject}", flush=True)
            notify_user(subject[:180], "邮件中没有找到视频链接")
        for url in urls:
            if url in processed_url_set:
                continue
            output_name = output_name_for_subject(subject)
            output_path = output_dir / output_name if output_name else None
            run_translation(script_path, url, extra_args, output_path)
            processed_urls.append(url)
            processed_url_set.add(url)
            state["processed_urls"] = processed_urls[-2000:]
            save_state(state_path, state)
            completed += 1
        processed_ids.append(message_id)
        processed_id_set.add(message_id)
        state["initialized"] = True
        state["processed_message_ids"] = processed_ids[-2000:]
        save_state(state_path, state)

    print(f"Processed {completed} new MYT video link(s).", flush=True)
    return completed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Gmail for new [MYT - JL] video links and run wistia_srt.py."
    )
    parser.add_argument("--client", type=Path, default=DEFAULT_CLIENT_PATH)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--query",
        default='subject:"MYT - JL" newer_than:30d -in:spam -in:trash',
        help="Gmail search query used to find candidate messages.",
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=Path(__file__).with_name("wistia_srt.py"),
    )
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Process matching existing messages instead of using them as the initial baseline.",
    )
    parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help="Arguments after -- are passed to wistia_srt.py.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    extra_args = args.script_args
    if extra_args[:1] == ["--"]:
        extra_args = extra_args[1:]
    service = gmail_service(args.client.expanduser(), args.token.expanduser())
    process_once(
        service,
        args.state.expanduser(),
        args.script.expanduser(),
        args.query,
        extra_args,
        process_existing=args.process_existing,
        output_dir=args.output_dir.expanduser(),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"JLaw mail watcher failed: {detail}", file=sys.stderr, flush=True)
        notify_user(detail, "下载、翻译或视频处理失败")
        raise
