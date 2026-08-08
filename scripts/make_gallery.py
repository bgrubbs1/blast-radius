"""Re-extract the Devpost gallery stills from the freshly rendered demo.mp4.

Timestamps are computed from render_demo.build_scenes() rather than guessed, so
the stills stay aligned with the video if the scene timings ever change.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\blast-radius")
sys.path.insert(0, str(ROOT / "scripts"))

import render_demo  # noqa: E402

scenes = render_demo.build_scenes()

# cumulative start/end for each scene
t = 0.0
timeline = []
for s in scenes:
    dur = s.type_s + s.hold_s
    timeline.append((t, t + dur, s.caption))
    t += dur

print(f"total timeline = {t:.1f}s")
for start, end, cap in timeline:
    print(f"  {start:6.1f} - {end:6.1f}  {cap[:70]}")

# Which scenes become gallery stills, and the output name for each.
WANT = [
    ("__title__", "0-title.jpg", 0),      # first title scene
    ("3 ", "1-verdicts.jpg", None),
    ("6 ", "2-notify-rollout.jpg", None),
    ("8 ", "3-patch.jpg", None),
]

gallery = ROOT / "docs" / "gallery"
gallery.mkdir(parents=True, exist_ok=True)
video = ROOT / "docs" / "demo.mp4"

seen_title = 0
picked = []
for prefix, name, which in WANT:
    hit = None
    for idx, (start, end, cap) in enumerate(timeline):
        if prefix == "__title__":
            if cap == "__title__":
                if seen_title == which:
                    hit = (start, end, cap)
                    seen_title += 1
                    break
                seen_title += 1
        elif cap.startswith(prefix):
            hit = (start, end, cap)
            break
    if hit is None:
        print(f"!! no scene matched {prefix!r} for {name}")
        continue
    start, end, cap = hit
    # Grab near the end of the hold, once everything has been typed out.
    ts = end - 0.6
    picked.append((name, ts, cap))

for name, ts, cap in picked:
    out = gallery / name
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{ts:.2f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = out.exists() and out.stat().st_size > 0
    print(f"{name}: t={ts:.2f}s size={out.stat().st_size if ok else 0} rc={r.returncode} {cap[:55]}")
    if r.stderr.strip():
        print("   stderr:", r.stderr.strip()[:200])
