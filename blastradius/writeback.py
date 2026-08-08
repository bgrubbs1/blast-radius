"""Write the finding back into DataHub, so the catalog learns from the review.

A report in a PR is read once. A warning on the changed column and a linked
analysis document are visible to everyone who opens that dataset afterwards --
which is the point of having a catalog. This module is opt-in (``--write-back``)
and needs the MCP server started with ``TOOLS_IS_MUTATION_ENABLED=true``.

We only ever *add* context: an appended warning on the changed column and a
standalone analysis document linked to the affected assets. Nothing is deleted,
replaced, or reassigned.
"""

from __future__ import annotations

from .datahub import DataHubMCP
from .models import ImpactReport, Operation, Verdict


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


def _analysis(report: ImpactReport) -> str:
    affected = [asset for asset in report.assets if asset.verdict is not Verdict.SAFE]
    lines = [
        "## Proposed change",
        "",
        f"`{report.change.describe()}`",
        "",
        "## Impact",
        "",
        f"- {len(report.breaking)} breaking",
        f"- {len(report.at_risk)} at risk",
        f"- {len(report.safe)} cleared",
        "",
        "## Affected assets",
        "",
    ]
    lines.extend(f"- **{asset.verdict.value}** — `{asset.name}`" for asset in affected)
    lines.extend(["", _note(report)])
    return "\n".join(lines)


def _succeeded(payload: object) -> bool:
    if payload is None:
        return False
    if isinstance(payload, dict) and payload.get("success") is False:
        return False
    return True


async def write_back(hub: DataHubMCP, report: ImpactReport) -> list[str]:
    """Annotate DataHub with the review outcome. Returns human-readable results."""
    results: list[str] = []
    if not hub.mutations:
        return ["write-back skipped: mutations are not enabled on the MCP server"]
    if not report.root_urn:
        return ["write-back skipped: the changed dataset was never resolved"]

    description_tool = hub.has_tool("update_description")
    if description_tool:
        description_args = {
            "entity_urn": report.root_urn,
            "operation": "append",
            "description": f"\n\n> **Blast Radius warning:** {_note(report)}",
        }
        if report.change.column:
            description_args["column_path"] = report.change.column
        payload = await hub.call(description_tool, description_args)
        target = report.change.column or report.change.table
        if _succeeded(payload):
            results.append(
                f"{description_tool}: appended schema-change warning to {target}"
            )
        else:
            results.append(f"{description_tool}: failed (see warnings)")
    else:
        results.append("update_description is not exposed -- column warning skipped")

    doc_tool = hub.has_tool("save_document")
    if doc_tool:
        related_assets = [report.root_urn]
        related_assets.extend(
            asset.urn for asset in report.assets if asset.verdict is not Verdict.SAFE
        )
        related_assets = list(dict.fromkeys(related_assets))[:25]
        payload = await hub.call(
            doc_tool,
            {
                "document_type": "Analysis",
                "title": f"Blast radius: {report.change.describe()}",
                "content": _analysis(report),
                "related_assets": related_assets,
            },
        )
        if _succeeded(payload):
            results.append(f"{doc_tool}: saved linked impact analysis")
        else:
            results.append(f"{doc_tool}: failed (see warnings)")
    else:
        results.append("save_document is not exposed -- linked analysis skipped")

    return results
