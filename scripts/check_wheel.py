"""Build the wheel and prove its bundled offline demo runs.

Usage: ``python scripts/check_wheel.py``
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="blast-radius-wheel-") as raw_tmp:
        tmp = Path(raw_tmp)
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(tmp)],
            cwd=ROOT,
            check=True,
        )
        wheel = next(tmp.glob("blast_radius-*.whl"))
        site = tmp / "site"
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            if "fixtures/_tools.json" not in names:
                raise SystemExit("wheel is missing fixtures/_tools.json")
            archive.extractall(site)

        env = dict(os.environ)
        env["PYTHONPATH"] = str(site)
        completed = subprocess.run(
            [sys.executable, "-m", "blastradius.cli", "demo", "--out", str(tmp / "out")],
            cwd=tmp,
            env=env,
            text=True,
            capture_output=True,
        )
        if completed.returncode:
            sys.stderr.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            return completed.returncode
        if not (tmp / "out" / "impact-report.md").exists():
            raise SystemExit("wheel demo did not write impact-report.md")
        print("wheel demo passed with bundled fixtures")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
