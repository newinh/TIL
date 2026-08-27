#!/usr/bin/env python3
"""Build the course manifest and self-contained lesson diagrams."""

from __future__ import annotations

import argparse
import html
import json
import re
import textwrap
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAY_RE = re.compile(r"day-(\d{2})-(.+)\.md$")


def section_summary(lines: list[str], heading_index: int) -> str:
    for line in lines[heading_index + 1 :]:
        value = line.strip().lstrip("- ").replace("**", "").replace("`", "")
        if line.startswith("## "):
            break
        if value and not value.startswith(("#", "![", "```", ">")):
            return value.split(".")[0]
    return "핵심 흐름을 확인한다"


def wrap(value: str, width: int, limit: int) -> list[str]:
    shortened = textwrap.shorten(value, width=width * limit, placeholder="…")
    return textwrap.wrap(shortened, width=width, break_long_words=True)[:limit]


def diagram_html(day: int, slug: str, title: str, lesson: str) -> str:
    lines = lesson.splitlines()
    question = next((lines[index + 2].lstrip("- ") for index, line in enumerate(lines[:-2]) if line == "## 오늘의 질문"), title)
    excluded = {"오늘의 질문", "함정 체크", "오늘의 한 문장", "30초 확인 문제", "정답과 해설"}
    sections = [(line[3:], section_summary(lines, index)) for index, line in enumerate(lines) if line.startswith("## ") and line[3:] not in excluded]
    sections = (sections + [("검증", "실패 모드와 운영 지표를 확인한다")])[:4]
    quote = next((line[2:] for line in lines if line.startswith("> ")), title)

    cards = []
    for index, (heading, summary) in enumerate(sections):
        x = 64 + index * 280
        focal = " focal" if index == 2 else ""
        heading_lines = wrap(heading, 12, 2)
        summary_lines = wrap(summary, 17, 3)
        heading_svg = "".join(f'<text x="{x + 20}" y="{230 + offset * 22}" class="card-title">{html.escape(line)}</text>' for offset, line in enumerate(heading_lines))
        summary_svg = "".join(f'<text x="{x + 20}" y="{312 + offset * 24}" class="card-copy">{html.escape(line)}</text>' for offset, line in enumerate(summary_lines))
        cards.append(f'<g><rect class="card{focal}" x="{x}" y="188" width="232" height="220" rx="10"/><text x="{x + 20}" y="214" class="step">0{index + 1}</text>{heading_svg}<line x1="{x + 20}" y1="284" x2="{x + 212}" y2="284" class="rule"/>{summary_svg}</g>')
    arrows = "".join(f'<line x1="{296 + index * 280}" y1="298" x2="{340 + index * 280}" y2="298" class="arrow" marker-end="url(#arrow)"/>' for index in range(3))
    question_svg = "".join(f'<text x="64" y="{132 + offset * 24}" class="question">{html.escape(line)}</text>' for offset, line in enumerate(wrap(question, 64, 2)))
    quote_svg = "".join(f'<text x="88" y="{502 + offset * 28}" class="takeaway">{html.escape(line)}</text>' for offset, line in enumerate(wrap(quote, 62, 2)))

    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>*{{box-sizing:border-box}}html,body{{margin:0;width:1200px;height:632px;overflow:hidden}}body{{background:#f5f5f5;font-family:'Geist','Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif}}svg{{display:block;width:1200px;height:632px}}.eyebrow,.step{{font-family:'Geist Mono','Noto Sans Mono CJK KR',monospace;font-size:9px;font-weight:600;letter-spacing:.14em;fill:#4f5d75}}.title{{font-size:34px;font-weight:700;fill:#2d3142}}.question{{font-size:16px;fill:#4f5d75}}.card{{fill:#fff;stroke:rgba(45,49,66,.22)}}.card.focal{{fill:rgba(235,108,54,.07);stroke:#eb6c36;stroke-width:1.4}}.card-title{{font-size:16px;font-weight:700;fill:#2d3142}}.card-copy{{font-size:14px;fill:#4f5d75}}.rule{{stroke:rgba(45,49,66,.12)}}.arrow{{stroke:#4f5d75;stroke-width:1.2}}.takeaway-box{{fill:#2d3142}}.takeaway-label{{font-family:'Geist Mono','Noto Sans Mono CJK KR',monospace;font-size:9px;font-weight:600;letter-spacing:.14em;fill:#eb6c36}}.takeaway{{font-size:18px;font-weight:600;fill:#fff}}</style></head>
<body><svg viewBox="0 0 1200 632" role="img" aria-labelledby="day-{day:02d}-{slug}-title day-{day:02d}-{slug}-desc"><title id="day-{day:02d}-{slug}-title">{html.escape(title)}</title><desc id="day-{day:02d}-{slug}-desc">{html.escape(question)}</desc><defs><marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="#4f5d75"/></marker></defs><rect width="1200" height="632" fill="#f5f5f5"/><text x="64" y="42" class="eyebrow">SYSTEM DESIGN DAILY / DAY {day:02d}</text><text x="64" y="88" class="title">{html.escape(title)}</text>{question_svg}{arrows}{''.join(cards)}<rect x="64" y="452" width="1072" height="124" rx="10" class="takeaway-box"/><text x="88" y="482" class="takeaway-label">TAKEAWAY</text>{quote_svg}<text x="1136" y="610" text-anchor="end" class="eyebrow">LIQUIDSLR / SYSTEM-DESIGN-NOTES</text></svg></body></html>'''


def build_manifest() -> dict[str, object]:
    files = sorted((ROOT / "slack").glob("day-*.md"))
    if len(files) != 40:
        raise ValueError(f"expected 40 Slack lessons, found {len(files)}")
    days = []
    for path in files:
        match = DAY_RE.fullmatch(path.name)
        if not match:
            raise ValueError(path.name)
        day, slug = int(match.group(1)), match.group(2)
        source = path.read_text(encoding="utf-8")
        title = next(line.split(": ", 1)[1] for line in source.splitlines() if line.startswith(f"## Day {day:02d}/40: "))
        source_url = next(re.search(r"\((https://github\.com/liquidslr/system-design-notes/[^)]+)\)", line).group(1) for line in source.splitlines() if "[원문" in line)
        scheduled = date(2026, 8, 25) if day == 1 else date(2026, 8, 29) + timedelta(days=day - 2)
        days.append({
            "day": day,
            "date": scheduled.isoformat(),
            "slug": slug,
            "title": title,
            "slack": f"slack/{path.name}",
            "lesson": f"lessons/{path.name}",
            "diagram": f"diagrams/day-{day:02d}-{slug}.png",
            "source": source_url,
        })
    if [item["day"] for item in days] != list(range(1, 41)):
        raise ValueError("course days are not continuous")
    return {"course": "System Design Daily", "timezone": "Asia/Seoul", "time": "09:30", "days": days}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest()
    (ROOT / "course-schedule.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for item in manifest["days"][3:]:
        lesson = (ROOT / item["lesson"]).read_text(encoding="utf-8")
        target = (ROOT / item["diagram"]).with_suffix(".html")
        target.write_text(diagram_html(item["day"], item["slug"], item["title"], lesson), encoding="utf-8")

    if args.render:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 632}, device_scale_factor=2)
            for item in manifest["days"][3:]:
                source = (ROOT / item["diagram"]).with_suffix(".html")
                page.goto(source.as_uri(), wait_until="load")
                page.locator("svg").screenshot(path=str(ROOT / item["diagram"]))
            browser.close()


if __name__ == "__main__":
    main()
