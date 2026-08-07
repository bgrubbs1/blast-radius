"""Write the finding back into DataHub, so the catalog learns from the review.

A report in a PR is read once. A deprecation notice on the column is read by
everyone who opens that dataset afterwards -- which is the point of having a
catalog. This module is opt-in (``--write-back``) and needs the MCP server
started with ``TOOLS_IS_MUTATION_ENABLED=true``.

We only ever *add* context: a deprecation note on the changed column, a tag on
affected downstream assets, and a link back to the report. Nothing is deleted
and no ownership is reassigned.
"""

from __future__ import annotations

from .datahub import DataHubMCP
from .models import ImpactReport, Operation, Verdict

TAG = "blast-radius:impacted"


def _find_tool(hub: DataHubMCP, *needles: str) -> str | None:
    """First exposed tool whose name contains any of ``needles``."""
    for name in hub.available_tools:
        lowered = name.lower()
        if any(needle in lowered for needle in needles):
            return name
    return None


def _note(report: ImpactReport) -> str:
    change = report.change
    counts = (
        f"{len(report.breaking)} breaking, {len(report.at_risk)} unproven "
        f"downstream asset(s)"
    )
    if change.operation is Operation.RENAME_COLUMN:
        action = f"being renamed to '{change.new_name}'"
    elif change.operation is Operation.RETYPE_COLUMN:
        action = f"changing type to {change.new_type}"
    elif change.operation is Operation.DROP_TABLE:
        action = "scheduled for removal"
    else:
        action = "scheduled for removal"
    return (
        f"{change.column or change.table} is {action}. Blast Radius found {counts}. "
        "Migrate before this lands; see the generated impact report."
    )


async def write_back(hub: DataHubMCP, report: ImpactReport) -> list[str]:
    """Annotate DataHub with the review outcome. Returns human-readable results."""
    results: list[str] = []
    if not hub.mutations:
        return ["write-back skipped: mutations are not enabled on the MCP server"]
    if not report.root_urn:
        return ["write-back skipped: the changed dataset was never resolved"]

    deprecate = _find_tool(hub, "deprecat")
    if deprecate:
        payload = await hub.call(
            deprecate,
            {"urn": report.root_urn, "deprecated": True, "note": _note(report)},
        )
        results.append(
            f"{deprecate}: {'ok' if payload is not None else 'failed (see warnings)'} "
            f"on {report.root_urn}"
        )
    else:
        results.append(
            "no deprecation tool exposed -- upgrade mcp-server-datahub for write-back"
        )

    tag_tool = _find_tool(hub, "tag")
    if tag_tool:
        targets = [a.urn for a in report.assets if a.verdict is not Verdict.SAFE][:25]
        tagged = 0
        for urn in targets:
            if await hub.call(tag_tool, {"urn": urn, "tags": [TAG]}) is not None:
                tagged += 1
        results.append(f"{tag_tool}: tagged {tagged}/{len(targets)} impacted assets")

    doc_tool = _find_tool(hub, "document")
    if doc_tool:
        payload = await hub.call(
            doc_tool,
            {
                "urn": report.root_urn,
                "title": f"Blast radius: {report.change.describe()}",
                "content": report.summary or _note(report),
            },
        )
        if payload is not None:
            results.append(f"{doc_tool}: attached the impact summary")

    return results
