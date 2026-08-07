"""End-to-end tests that replay the recorded fixtures.

These are the tests that would catch a regression in the part judges care
about: given a real DataHub payload, does the tool reach the right verdicts?
Regenerate the fixtures with ``python scripts/make_fixtures.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from blastradius.change import parse_change
from blastradius.cli import main
from blastradius.datahub import DataHubMCP
from blastradius.impact import analyse
from blastradius.models import Verdict
from blastradius.patch import patches_for
from blastradius.report import to_json, to_markdown

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CHANGE = "ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount"


@pytest.fixture(scope="module")
def report():
    async def run():
        change = parse_change(CHANGE)
        async with DataHubMCP(fixtures_dir=FIXTURES, offline=True) as hub:
            result = await analyse(hub, change, depth=2)
        result.patches = patches_for(result)
        return result

    return asyncio.run(run())


def names(assets) -> set[str]:
    return {a.name for a in assets}


def test_fixtures_exist():
    assert (FIXTURES / "_tools.json").exists(), "run scripts/make_fixtures.py first"


def test_root_dataset_is_resolved(report):
    assert report.root_urn is not None
    assert "fct_orders" in report.root_urn


def test_queries_naming_the_column_are_breaking(report):
    assert names(report.breaking) == {
        "analytics.marts.dim_customer_ltv",
        "analytics.marts.mart_orders_flat",
    }


def test_select_star_and_opaque_assets_are_at_risk_not_breaking(report):
    at_risk = names(report.at_risk)
    assert "analytics.marts.rpt_daily_revenue" in at_risk
    assert "Finance Exec Overview" in at_risk  # dashboard, no SQL to parse
    assert "order_propensity_v3" in at_risk  # ML feature table


def test_clean_consumer_is_cleared(report):
    assert names(report.safe) == {"analytics.staging.stg_orders_audit"}


def test_every_non_safe_asset_carries_evidence(report):
    for asset in report.assets:
        if asset.verdict is not Verdict.SAFE:
            assert asset.evidence, f"{asset.name} has a verdict but no evidence"
            assert asset.worst_evidence().detail


def test_unowned_assets_are_surfaced_for_notification(report):
    notify = report.owners_to_notify()
    assert "(unowned)" in notify
    assert "maya.iyer" in notify and "sam.okafor" in notify


def test_patches_are_generated_for_breaking_queries(report):
    targets = {p.title.split(":")[0] for p in report.patches}
    assert "analytics.marts.dim_customer_ltv" in targets
    assert any(p.confidence == "mechanical" for p in report.patches)
    assert any(p.confidence == "review" for p in report.patches)


def test_markdown_report_has_the_sections_a_reviewer_needs(report):
    md = to_markdown(report)
    for heading in ("# Blast radius", "## Breaking", "## Who to notify", "## Rollout order"):
        assert heading in md
    assert "2 assets will break" in md


def test_json_report_is_machine_readable(report):
    payload = json.loads(to_json(report))
    assert payload["counts"] == {
        "breaking": 2,
        "at_risk": 3,
        "safe": 1,
        "patches": len(report.patches),
    }
    assert payload["change"]["operation"] == "drop_column"


def test_cli_demo_exits_zero(capsys):
    assert main(["demo"]) == 0
    assert "blast radius" in capsys.readouterr().out.lower()


def test_cli_fail_on_breaking_exits_two(capsys):
    code = main(
        ["plan", "--offline", "--fixtures", str(FIXTURES), "--change", CHANGE, "--fail-on", "breaking"]
    )
    capsys.readouterr()
    assert code == 2


def test_cli_writes_artifacts(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["demo", "--out", str(out)]) == 0
    capsys.readouterr()
    assert (out / "impact-report.md").exists()
    assert (out / "impact.json").exists()
    assert (out / "notify.md").exists()
    assert list((out / "patches").glob("*.diff"))
