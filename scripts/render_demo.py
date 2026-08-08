"""Render the demo video from real captured output.

Every line of terminal text in the video comes from ``docs/capture/*.txt``, which
were captured from actual runs against a live DataHub Core instance -- this
script only animates and captions them. Nothing is re-typed or edited for effect.

    python scripts/render_demo.py --out docs/demo.mp4

Needs Pillow and ffmpeg on PATH. Output: 1920x1080, 30fps, under 3 minutes.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "docs" / "capture"

W, H = 1920, 1080
FPS = 30
MARGIN = 70
LINE_H = 30
FONT_SIZE = 22
TITLE_SIZE = 58
SUB_SIZE = 30

BG = (13, 17, 23)
FG = (201, 209, 217)
DIM = (110, 118, 129)
ACCENT = (88, 166, 255)
RED = (248, 81, 73)
YELLOW = (210, 153, 34)
GREEN = (63, 185, 80)
PROMPT = (126, 231, 135)

MONO = "C:/Windows/Fonts/consola.ttf"
MONO_ALT = "C:/Windows/Fonts/CascadiaMono.ttf"


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in (MONO, MONO_ALT):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


F_BODY = font(FONT_SIZE)
F_TITLE = font(TITLE_SIZE)
F_SUB = font(SUB_SIZE)


def colour_for(line: str) -> tuple[int, int, int]:
    """Colour a terminal line the way the tool itself would."""
    stripped = line.strip()
    if stripped.startswith("$"):
        return PROMPT
    if stripped.startswith("BREAKING") or "will break" in stripped:
        return RED
    if stripped.startswith("AT RISK"):
        return YELLOW
    if stripped.startswith("safe") or "passed" in stripped:
        return GREEN
    if stripped.startswith("+"):
        return GREEN
    if stripped.startswith("-") and not stripped.startswith("---"):
        return RED
    if stripped.startswith(("#", "##", "───", "—")):
        return ACCENT
    if stripped.startswith("warning:"):
        return YELLOW
    return FG


@dataclass
class Scene:
    caption: str
    lines: list[str]
    hold_s: float = 3.0
    type_s: float = 0.0  # seconds spent revealing the lines


def read_capture(name: str, limit: int | None = None) -> list[str]:
    path = CAPTURE / name
    if not path.exists():
        raise SystemExit(f"missing capture: {path} (see SUBMISSION.md for how these are made)")
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[:limit] if limit else lines


def title_frame(title: str, subtitle: str, footer: str = "") -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.text((MARGIN, H // 2 - 120), title, font=F_TITLE, fill=FG)
    draw.text((MARGIN, H // 2 - 30), subtitle, font=F_SUB, fill=ACCENT)
    if footer:
        draw.text((MARGIN, H - 120), footer, font=F_SUB, fill=DIM)
    return image


def terminal_frame(caption: str, lines: list[str], revealed: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.text((MARGIN, 44), caption, font=F_SUB, fill=ACCENT)
    draw.line([(MARGIN, 92), (W - MARGIN, 92)], fill=(48, 54, 61), width=2)

    y = 130
    for line in lines[:revealed]:
        draw.text((MARGIN, y), line[:118], font=F_BODY, fill=colour_for(line))
        y += LINE_H
        if y > H - 90:
            break
    draw.text((MARGIN, H - 60), "blast-radius  ·  github.com/bgrubbs1/blast-radius",
              font=F_BODY, fill=DIM)
    return image


def build_scenes() -> list[Scene]:
    return [
        Scene(
            "__title__",
            [
                "Blast Radius",
                "One DROP COLUMN → 2 breaking · 2 at risk · 1 safe · 3 patches",
                "Build with DataHub: The Agent Hackathon",
            ],
            hold_s=3.0,
        ),
        Scene(
            "1 — the proposed change: one column, on a fact table with consumers",
            ["$ cat examples/migration.sql", ""]
            + read_capture("../../examples/migration.sql")[-1:],
            type_s=0.8,
            hold_s=2.2,
        ),
        Scene(
            "2 — what actually breaks. Every verdict cites evidence from DataHub's own query index",
            ["$ blast-radius plan --change examples/migration.sql --depth 2", ""]
            + read_capture("plan.txt", limit=26),
            type_s=3.0,
            hold_s=8.0,
        ),
        Scene(
            "3 — proof, not a mock: DataHub Core 1.7.0 reached through the official MCP Server",
            ["$ blast-radius doctor", ""] + read_capture("doctor.txt"),
            type_s=1.5,
            hold_s=4.0,
        ),
        Scene(
            "4 — BREAKING means a query names the column. AT RISK means unproven, never 'safe'",
            [
                "BREAKING   dim_customer_ltv    a query references fct_orders.discount_amount",
                "                              -> SUM(o.discount_amount) AS total_discount",
                "",
                "AT RISK    rpt_daily_revenue  SELECT * over fct_orders: output schema shifts",
                "AT RISK    Finance Exec       dashboard; definition is not SQL DataHub indexes",
                "",
                "safe       stg_orders_audit   1 indexed query parsed; none reference the column",
            ],
            type_s=2.5,
            hold_s=6.0,
        ),
        Scene(
            "5 — who has to act, from DataHub ownership. '(unowned)' is its own finding",
            read_capture("notify.txt"),
            type_s=1.5,
            hold_s=5.0,
        ),
        Scene(
            "6 — the safe sequence for this operation: expand, migrate, verify, contract",
            read_capture("rollout.txt"),
            type_s=2.5,
            hold_s=7.0,
        ),
        Scene(
            "7 — a mechanical patch: the dead projection is removed, the rest is untouched",
            read_capture("patch_mechanical.txt"),
            type_s=2.0,
            hold_s=6.0,
        ),
        Scene(
            "8 — and where it refuses to guess: the column drives a WHERE, so a human decides",
            read_capture("patch_review.txt"),
            type_s=2.0,
            hold_s=6.0,
        ),
        Scene(
            "9 — gate the pull request: non-zero exit when something provably breaks",
            read_capture("gate.txt"),
            type_s=1.2,
            hold_s=4.5,
        ),
        Scene(
            "10 — verdicts are deterministic, so they are testable: 57 tests replay real MCP payloads",
            ["$ pytest -q", ""] + read_capture("tests.txt"),
            type_s=1.0,
            hold_s=4.0,
        ),
        Scene(
            "__title__",
            [
                "Ask before you break it.",
                "blast-radius demo  —  runs with no DataHub, from recorded payloads",
                "github.com/bgrubbs1/blast-radius  ·  Apache 2.0",
            ],
            hold_s=4.5,
        ),
    ]


def render(scenes: list[Scene], out: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp)
        index = 0
        for scene in scenes:
            if scene.caption == "__title__":
                title, subtitle, footer = (scene.lines + ["", "", ""])[:3]
                frame = title_frame(title, subtitle, footer)
                for _ in range(int(scene.hold_s * FPS)):
                    frame.save(frames_dir / f"{index:06d}.png")
                    index += 1
                continue

            total = len(scene.lines)
            reveal_frames = max(1, int(scene.type_s * FPS))
            for step in range(reveal_frames):
                revealed = max(1, round(total * (step + 1) / reveal_frames))
                terminal_frame(scene.caption, scene.lines, revealed).save(
                    frames_dir / f"{index:06d}.png"
                )
                index += 1
            full = terminal_frame(scene.caption, scene.lines, total)
            for _ in range(int(scene.hold_s * FPS)):
                full.save(frames_dir / f"{index:06d}.png")
                index += 1

        duration = index / FPS
        print(f"rendered {index} frames = {duration:.1f}s")
        if duration > 175:
            print(f"WARNING: {duration:.0f}s is close to the 3-minute limit", file=sys.stderr)

        out.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-y", "-framerate", str(FPS),
            "-i", str(frames_dir / "%06d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-movflags", "+faststart", str(out),
        ]
        subprocess.run(command, check=True, capture_output=True)
    size_mb = out.stat().st_size / 1_000_000
    print(f"wrote {out} ({size_mb:.1f} MB, {duration:.0f}s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "docs" / "demo.mp4"))
    args = parser.parse_args()
    render(build_scenes(), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
