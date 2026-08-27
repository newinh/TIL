#!/usr/bin/env python3
"""Prepare one due System Design Daily lesson and record verified completion."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "course-schedule.json"
DEFAULT_STATE = ROOT / ".system-design-daily-progress.json"
REQUIRED_SECTIONS = (
    "### 오늘의 질문",
    "### 함정 체크",
    "### 오늘의 한 문장",
    "### 30초 확인 문제",
    "### 정답과 해설",
    "### 더 보기",
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schedule(path: Path = SCHEDULE) -> dict[str, object]:
    value = load_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("days"), list):
        raise ValueError(f"invalid schedule: {path}")
    return value


def load_progress(path: Path) -> int:
    value = load_json(path)
    if not isinstance(value, dict) or set(value) != {"last_completed_day"}:
        raise ValueError(f"progress must contain only last_completed_day: {path}")
    day = value["last_completed_day"]
    if not isinstance(day, int) or not 0 <= day <= 40:
        raise ValueError(f"invalid last_completed_day: {day!r}")
    return day


def split_message(source: str) -> tuple[str, str]:
    lines = source.splitlines()
    headings = [index for index, line in enumerate(lines) if line.startswith(("# ", "## "))]
    if len(headings) < 2 or headings[:2] != [0, 2]:
        raise ValueError("Slack source must start with H1, blank line, H2")
    root = f"{lines[0]}\n{lines[2]}"
    thread = "\n".join(lines[3:]).strip()
    if not thread:
        raise ValueError("Slack thread body is empty")
    return root, thread


def prepare(today: date, state_path: Path) -> dict[str, object]:
    schedule = load_schedule()
    days = schedule["days"]
    last = load_progress(state_path)
    if last == 40:
        return {"status": "complete", "last_completed_day": 40}

    item = days[last]
    due = date.fromisoformat(item["date"])
    if due > today:
        return {"status": "not_due", "next_day": item["day"], "scheduled_date": item["date"]}

    source_path = ROOT / item["slack"]
    root, thread = split_message(source_path.read_text(encoding="utf-8"))
    return {
        "status": "ready",
        "day": item["day"],
        "scheduled_date": item["date"],
        "root": root,
        "thread": thread,
        "slack_file": str(source_path),
    }


def complete(day: int, state_path: Path) -> None:
    last = load_progress(state_path)
    if day != last + 1:
        raise ValueError(f"completion must be sequential: expected Day {last + 1:02d}, got Day {day:02d}")
    if day > 40:
        raise ValueError("course is already complete")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state_path.parent, delete=False) as handle:
        json.dump({"last_completed_day": day}, handle, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, state_path)


def validate() -> None:
    schedule = load_schedule()
    days = schedule["days"]
    assert len(days) == 40
    assert [item["day"] for item in days] == list(range(1, 41))
    assert days[0]["date"] == "2026-08-25"
    assert days[1]["date"] == "2026-08-29"
    assert days[-1]["date"] == "2026-10-06"
    assert all(
        date.fromisoformat(days[index]["date"]).toordinal()
        == date.fromisoformat(days[index - 1]["date"]).toordinal() + 1
        for index in range(2, 40)
    )

    for item in days:
        slack_path = ROOT / item["slack"]
        lesson_path = ROOT / item["lesson"]
        diagram_path = ROOT / item["diagram"]
        html_path = diagram_path.with_suffix(".html")
        for path in (slack_path, lesson_path, diagram_path, html_path):
            assert path.is_file(), path
        source = slack_path.read_text(encoding="utf-8")
        lesson = lesson_path.read_text(encoding="utf-8")
        assert "·" not in source and "HUMANIZE-SUMMARY" not in source, slack_path
        assert "·" not in lesson and "HUMANIZE-SUMMARY" not in lesson, lesson_path
        assert all(section in source for section in REQUIRED_SECTIONS), slack_path
        root, thread = split_message(source)
        assert root == f"# System Design Daily\n## Day {item['day']:02d}/40: {item['title']}"
        assert thread.startswith("### 오늘의 질문")
        assert item["source"] in source
        assert item["source"] in lesson
        assert f"../{item['diagram']}" in lesson
        assert f"https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/{item['diagram']}" in source
        assert f"https://github.com/newinh/TIL/blob/orca/sys-design/{item['lesson']}" in source

    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory) / "progress.json"
        state.write_text('{"last_completed_day": 1}\n', encoding="utf-8")
        ready = prepare(date(2026, 8, 29), state)
        assert ready["status"] == "ready" and ready["day"] == 2
        complete(2, state)
        assert load_progress(state) == 2
        try:
            complete(4, state)
        except ValueError:
            pass
        else:
            raise AssertionError("out-of-order completion was accepted")
        state.write_text('{"last_completed_day": 40}\n', encoding="utf-8")
        assert prepare(date(2027, 1, 1), state) == {"status": "complete", "last_completed_day": 40}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "complete", "self-check"), nargs="?", default="prepare")
    parser.add_argument("day", type=int, nargs="?")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Seoul")).date())
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.today, args.state), ensure_ascii=False, indent=2))
    elif args.command == "complete":
        if args.day is None:
            raise SystemExit("complete requires a day")
        complete(args.day, args.state)
        print(json.dumps({"status": "completed", "last_completed_day": args.day}))
    else:
        validate()
        print("self-check: OK")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
