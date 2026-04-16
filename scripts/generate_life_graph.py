#!/usr/bin/env python3
"""Build an animated Conway contribution grid SVG.

The script uses GitHub's GraphQL API when GITHUB_TOKEN is available. Without a
token it still emits a deterministic local preview, so the README is never blank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path


WIDTH = 53
HEIGHT = 7
FRAMES = 72
BOOT_HOLD_FRAMES = 3

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

FONT_5X7 = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "01010", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


THEMES = {
    "dark": {
        "bg": "#000000",
        "panel": "#010401",
        "border": "#0b2a10",
        "grid": "#041007",
        "level0": "#161b22",
        "level1": "#006d32",
        "level2": "#26a641",
        "level3": "#39d353",
        "level4": "#7ee787",
        "text": "#b9ffc7",
        "muted": "#4f8f59",
        "scanline": "#ffffff",
    },
    "light": {
        "bg": "#f7f4e8",
        "panel": "#fffdf4",
        "border": "#b8d0a8",
        "grid": "#d9e6ce",
        "level0": "#ebedf0",
        "level1": "#9be9a8",
        "level2": "#40c463",
        "level3": "#30a14e",
        "level4": "#216e39",
        "text": "#18351e",
        "muted": "#58725c",
        "scanline": "#1a1a1a",
    },
}


def empty_grid() -> list[list[int]]:
    return [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]


def empty_counts() -> list[list[int]]:
    return [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]


def stable_rng(login: str, salt: str = "") -> random.Random:
    digest = hashlib.sha256(f"{login}:{salt}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def fetch_calendar(login: str, token: str | None) -> dict | None:
    if not token:
        return None

    payload = json.dumps({"query": QUERY, "variables": {"login": login}}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ECD5A-conway-contribution-grid",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"warning: GitHub API unavailable, using local seed: {exc}", file=sys.stderr)
        return None

    if data.get("errors"):
        print(f"warning: GitHub API returned errors: {data['errors']}", file=sys.stderr)
        return None

    user = data.get("data", {}).get("user")
    if not user:
        print(f"warning: GitHub user {login!r} was not found, using local seed", file=sys.stderr)
        return None

    return user["contributionsCollection"]["contributionCalendar"]


def counts_from_calendar(calendar: dict | None) -> tuple[list[list[int]], int]:
    counts = empty_counts()
    total = 0

    if not calendar:
        return counts, total

    total = int(calendar.get("totalContributions") or 0)
    weeks = calendar.get("weeks", [])[-WIDTH:]
    x_offset = WIDTH - len(weeks)
    for x, week in enumerate(weeks, start=x_offset):
        days = week.get("contributionDays", [])[:HEIGHT]
        for y, day in enumerate(days):
            counts[y][x] = int(day.get("contributionCount") or 0)

    return counts, total


def stamp_name(grid: list[list[int]], counts: list[list[int]], login: str) -> None:
    text = "".join(ch for ch in login.upper() if ch in FONT_5X7)[:8]
    if not text:
        text = "ECD5A"

    width = (len(text) * 6) - 1
    if width > WIDTH:
        text = text[: max(1, (WIDTH + 1) // 6)]
        width = (len(text) * 6) - 1

    x0 = max(0, (WIDTH - width) // 2)
    for index, char in enumerate(text):
        glyph = FONT_5X7[char]
        gx = x0 + index * 6
        for y, row in enumerate(glyph):
            for dx, value in enumerate(row):
                x = gx + dx
                if value == "1" and 0 <= x < WIDTH:
                    grid[y][x] = 1
                    counts[y][x] = max(counts[y][x], 3)


def clean_name_grid(login: str) -> list[list[int]]:
    grid = empty_grid()
    counts = empty_counts()
    stamp_name(grid, counts, login)
    return grid


def inject_gliders(
    grid: list[list[int]],
    counts: list[list[int]],
    login: str,
    minimum: int = 4,
) -> None:
    glider = ((1, 0), (2, 1), (0, 2), (1, 2), (2, 2))
    rng = stable_rng(login, "gliders")
    live = sum(sum(row) for row in grid)
    pieces = minimum if live < 80 else max(2, minimum // 2)

    for _ in range(pieces):
        x0 = rng.randrange(0, WIDTH - 3)
        y0 = rng.randrange(0, HEIGHT - 2)
        for dx, dy in glider:
            x = (x0 + dx) % WIDTH
            y = (y0 + dy) % HEIGHT
            grid[y][x] = 1
            counts[y][x] = max(counts[y][x], 2)


def inject_activity_stream(grid: list[list[int]], login: str, frame: int) -> None:
    if frame < 6 or frame % 2:
        return

    rng = stable_rng(login, f"edge-stream:{frame}")
    for _ in range(3):
        side = rng.choice(("left", "right"))
        y = rng.randrange(1, HEIGHT - 1)
        if side == "left":
            cells = ((0, y), (1, y + 1), (2, y - 1), (2, y), (2, y + 1))
        else:
            cells = (
                (WIDTH - 1, y),
                (WIDTH - 2, y + 1),
                (WIDTH - 3, y - 1),
                (WIDTH - 3, y),
                (WIDTH - 3, y + 1),
            )

        for x, cell_y in cells:
            if 0 <= x < WIDTH and 0 <= cell_y < HEIGHT:
                grid[cell_y][x] = 1


def next_state(grid: list[list[int]]) -> list[list[int]]:
    nxt = empty_grid()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            neighbours = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = (x + dx) % WIDTH
                    ny = (y + dy) % HEIGHT
                    neighbours += grid[ny][nx]

            if grid[y][x]:
                nxt[y][x] = 1 if neighbours in (2, 3) else 0
            else:
                nxt[y][x] = 1 if neighbours == 3 else 0
    return nxt


def evolve(seed: list[list[int]], login: str) -> list[list[list[int]]]:
    states = [[row[:] for row in seed]]
    for frame in range(1, FRAMES):
        nxt = next_state(states[-1])
        inject_activity_stream(nxt, login, frame)
        if frame > 10 and sum(sum(row) for row in nxt) < 8:
            counts = empty_counts()
            inject_gliders(nxt, counts, f"{login}:{frame}", minimum=6)
        states.append(nxt)
    return states


def cell_fill(theme: dict[str, str], count: int, x: int, y: int, login: str) -> str:
    if count >= 10:
        return theme["level4"]
    if count >= 5:
        return theme["level3"]
    if count >= 2:
        return theme["level2"]
    if count == 1:
        return theme["level1"]

    # Deterministic green levels keep the local preview from becoming one flat
    # shade when the GitHub token is unavailable.
    digest = hashlib.sha256(f"{login}:{x}:{y}:accent".encode("utf-8")).digest()[0]
    if digest < 16:
        return theme["level4"]
    if digest < 48:
        return theme["level3"]
    if digest < 96:
        return theme["level2"]
    return theme["level1"]


def make_svg(
    states: list[list[list[int]]],
    counts: list[list[int]],
    login: str,
    total: int,
    theme_name: str,
) -> str:
    theme = THEMES[theme_name]
    cell = 13
    gap = 4
    grid_width = WIDTH * cell + (WIDTH - 1) * gap
    grid_height = HEIGHT * cell + (HEIGHT - 1) * gap
    svg_width = 960
    svg_height = 190
    x0 = (svg_width - grid_width) // 2
    y0 = 56
    duration = "30s"
    title = f"{login} Conway contribution grid"

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}" '
        'role="img" aria-labelledby="title desc">',
        f"<title id=\"title\">{escape(title)}</title>",
        (
            f"<desc id=\"desc\">A {WIDTH} by {HEIGHT} animated Conway Game of Life grid "
            f"started from the {escape(login)} mark and colored with GitHub-style contribution levels.</desc>"
        ),
        "<defs>",
        (
            '<pattern id="scanlines" width="6" height="6" patternUnits="userSpaceOnUse">'
            f'<path d="M0 0H6" stroke="{theme["scanline"]}" stroke-opacity="0.055"/>'
            "</pattern>"
        ),
        "</defs>",
        f'<rect width="{svg_width}" height="{svg_height}" rx="8" fill="{theme["bg"]}"/>',
        (
            f'<rect x="12" y="12" width="{svg_width - 24}" height="{svg_height - 24}" rx="8" '
            f'fill="{theme["panel"]}" stroke="{theme["border"]}" stroke-width="1"/>'
        ),
        f'<rect x="12" y="12" width="{svg_width - 24}" height="{svg_height - 24}" rx="8" fill="url(#scanlines)"/>',
        (
            f'<text x="30" y="34" fill="{theme["text"]}" '
            'font-family="ui-monospace, SFMono-Regular, Consolas, Liberation Mono, monospace" '
            'font-size="15">ECD5A://CONWAY LIFEGRID</text>'
        ),
        (
            f'<text x="{svg_width - 30}" y="34" text-anchor="end" fill="{theme["muted"]}" '
            'font-family="ui-monospace, SFMono-Regular, Consolas, Liberation Mono, monospace" '
            'font-size="12">B3/S23 // CRYPTONATIVE</text>'
        ),
    ]

    boot_state = states[0]
    animation_states = [boot_state] * BOOT_HOLD_FRAMES + states

    for y in range(HEIGHT):
        for x in range(WIDTH):
            px = x0 + x * (cell + gap)
            py = y0 + y * (cell + gap)
            count = counts[y][x]
            fill = cell_fill(theme, count, x, y, login)
            fill_values = ";".join(fill if state[y][x] else theme["level0"] for state in animation_states)
            lines.append(
                f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" rx="2" '
                f'fill="{fill_values.split(";")[0]}">'
                f'<animate attributeName="fill" dur="{duration}" repeatCount="indefinite" '
                f'values="{fill_values}"/>'
                "</rect>"
            )

    lines.append("</svg>")

    return "\n".join(lines)


def write_outputs(login: str, out_dir: Path, token: str | None) -> None:
    calendar = fetch_calendar(login, token)
    seed = clean_name_grid(login)
    counts, total = counts_from_calendar(calendar)
    states = evolve(seed, login)
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme_name in ("dark", "light"):
        svg = make_svg(states, counts, login, total, theme_name)
        suffix = "-dark" if theme_name == "dark" else ""
        path = out_dir / f"conway-contribution-grid{suffix}.svg"
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"wrote {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Conway contribution grid SVG.")
    parser.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "ECD5A"))
    parser.add_argument("--out-dir", default="dist")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    write_outputs(args.user, Path(args.out_dir), token)


if __name__ == "__main__":
    main()
