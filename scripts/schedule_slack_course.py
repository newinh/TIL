#!/usr/bin/env python3
"""Convert course Markdown to Block Kit and schedule all 40 Slack messages."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


API_URL = "https://slack.com/api/chat.scheduleMessage"
DEFAULT_CHANNEL = "C070BBNJB89"
DAY_RE = re.compile(r"day-(\d{2})-")
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
IMAGE_RE = re.compile(r"^!\[([^]]+)]\((https://[^)]+)\)$")
LINK_RE = re.compile(r"\[([^]]+)]\((https://[^)]+)\)")


def slack_text(text: str) -> str:
    text = LINK_RE.sub(lambda match: f"<{match.group(2)}|{match.group(1)}>", text)
    return text.replace("**", "*")


def markdown_to_message(source: str) -> tuple[str, list[dict[str, object]]]:
    if "·" in source:
        raise ValueError("가운뎃점은 Slack 원고에서 사용할 수 없습니다")

    lines = source.splitlines()
    h1 = next((match.group(2) for line in lines if (match := HEADING_RE.match(line)) and len(match.group(1)) == 1), "System Design Daily")
    h2 = next((match.group(2) for line in lines if (match := HEADING_RE.match(line)) and len(match.group(1)) == 2), h1)
    blocks: list[dict[str, object]] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{slack_text(h1)}*"}]},
        {"type": "header", "text": {"type": "plain_text", "text": h2, "emoji": True}},
        {"type": "divider"},
    ]

    heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        content = "\n".join(body).strip()
        if heading and content:
            text = f"*{slack_text(heading)}*\n{slack_text(content)}"
            if len(text) > 3000:
                raise ValueError(f"section too long: {heading}")
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        elif content:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": slack_text(content)}})
        body = []

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2)
            if level <= 2:
                continue
            flush()
            heading = title
            continue

        image = IMAGE_RE.match(line)
        if image:
            flush()
            if heading:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{slack_text(heading)}*"}})
                heading = None
            blocks.append({"type": "image", "image_url": image.group(2), "alt_text": image.group(1)})
            continue

        if line.strip() or body:
            body.append(line)

    flush()
    if len(blocks) > 50:
        raise ValueError(f"too many blocks: {len(blocks)}")
    return f"{h1}: {h2}", blocks


def course_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("day-*.md"))
    days = [int(match.group(1)) for path in files if (match := DAY_RE.search(path.name))]
    if days != list(range(1, len(files) + 1)):
        raise ValueError(f"강의 파일이 Day 01부터 연속적이지 않습니다: {days}")
    return files


def schedule_times(count: int, start: date, at: time, timezone: ZoneInfo) -> list[int]:
    return [int(datetime.combine(start + timedelta(days=offset), at, timezone).timestamp()) for offset in range(count)]


def post_message(token: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.URLError as error:
        raise RuntimeError(f"Slack API request failed: {error}") from error
    if not result.get("ok"):
        raise RuntimeError(f"Slack API error: {result.get('error', 'unknown_error')}")
    return result


def self_check() -> None:
    sample = """# System Design Daily
## Day 01/40: 제목
### 핵심
- 항목
### 더 보기
![그림](https://example.com/a.png)
- [원문](https://example.com)
"""
    fallback, blocks = markdown_to_message(sample)
    assert fallback == "System Design Daily: Day 01/40: 제목"
    assert any(block["type"] == "header" for block in blocks)
    assert any(block["type"] == "image" for block in blocks)
    assert "<https://example.com|원문>" in json.dumps(blocks, ensure_ascii=False)
    print("self-check: OK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("slack"))
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--time", default="09:30")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--send", action="store_true", help="실제로 Slack 예약을 생성합니다")
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_check:
        self_check()
        return

    timezone = ZoneInfo(args.timezone)
    start = args.start_date or (datetime.now(timezone).date() + timedelta(days=1))
    hour, minute = map(int, args.time.split(":"))
    files = course_files(args.directory)
    if not files:
        raise SystemExit(f"원고가 없습니다: {args.directory}")
    if args.send and len(files) != 40:
        raise SystemExit(f"실제 예약은 40개 원고가 모두 있을 때만 가능합니다. 현재 {len(files)}개입니다.")

    payloads = []
    for path, post_at in zip(files, schedule_times(len(files), start, time(hour, minute), timezone)):
        fallback, blocks = markdown_to_message(path.read_text(encoding="utf-8"))
        payloads.append({"channel": args.channel, "post_at": post_at, "text": fallback, "blocks": blocks})

    now = int(datetime.now(timezone).timestamp())
    if payloads[0]["post_at"] < now + 120:
        raise SystemExit("첫 예약 시각은 현재보다 최소 2분 뒤여야 합니다.")
    if payloads[-1]["post_at"] > now + 120 * 24 * 60 * 60:
        raise SystemExit("마지막 예약 시각이 Slack의 120일 제한을 넘습니다.")

    if not args.send:
        print(json.dumps(payloads, ensure_ascii=False, indent=2))
        return

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise SystemExit("SLACK_BOT_TOKEN 환경변수가 필요합니다.")
    for path, payload in zip(files, payloads):
        result = post_message(token, payload)
        print(f"scheduled {path.name}: {result['scheduled_message_id']}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
