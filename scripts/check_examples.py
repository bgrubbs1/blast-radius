"""Guard against fixture drift.

Replays ``fixtures/`` and asserts the verdicts still match the committed
``examples/impact.json``. If someone changes the impact engine without
regenerating the examples, CI says so instead of shipping a stale demo.

    python scripts/check_examples.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blastradius.change import parse_change  # noqa: E402
from blastradius.datahub import DataHubMCP  # noqa: E402
from blastradius.impact import analyse  # noqa: E402
from blastradius.patch import patches_for  # noqa: E402

CHANGE = "ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount"


async def main() -> int:
    expected_path = ROOT / "examples" / "impact.json"
    if not expected_path.exists():
        print("examples/impact.json is missing -- run: blast-radius demo --out examples/")
        return 1
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    async with DataHubMCP(fixtures_dir=ROOT / "fixtures", offline=True) as hub:
        result = await analyse(hub, parse_change(CHANGE), depth=2)
    result.patches = patches_for(result)

    actual = {
        "breaking": sorted(a.name for a in result.breaking),
        "at_risk": sorted(a.name for a in result.at_risk),
        "safe": sorted(a.name for a in result.safe),
        "patches": len(result.patches),
    }
    want = {
        "breaking": sorted(
            a["name"] for a in expected["assets"] if a["verdict"] == "breaking"
        ),
        "at_risk": sorted(
            a["name"] for a in expected["assets"] if a["verdict"] == "at_risk"
        ),
        "safe": sorted(a["name"] for a in expected["assets"] if a["verdict"] == "safe"),
        "patches": expected["counts"]["patches"],
    }

    if actual != want:
        print("examples/ are stale -- rerun scripts/make_fixtures.py\n")
        print("expected:", json.dumps(want, indent=2))
        print("actual:  ", json.dumps(actual, indent=2))
        return 1

    print(
        f"examples match: {len(actual['breaking'])} breaking, "
        f"{len(actual['at_risk'])} at risk, {len(actual['safe'])} safe, "
        f"{actual['patches']} patches"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
