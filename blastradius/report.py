"""Render an :class:`ImpactReport` as markdown, JSON, and a terminal view.

The markdown file is the artefact people actually read -- it goes in the PR
description. It leads with the verdict, then the evidence, then who to tell,
then the rollout order, and only then the generated patches. Anything we could
not prove is listed under its own heading rather than folded into the summary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .models import ImpactedAsset, ImpactReport, Operation, Patch, Verdict

_VERDICT_LABEL = {
    Verdict.BREAKING: "BREAKING",
    Verdict.AT_RISK: "AT RISK",
    Verdict.SAFE: "safe",
}
_VERDICT_STYLE = {
    Verdict.BREAKING: "bold red",
    Verdict.AT_RISK: "yellow",
    Verdict.SAFE: "green",
}


def _owner_names(asset: ImpactedAsset) -> str:
    return ", ".join(o.name for o in asset.owners) if asset.owners else "(unowned)"


def _evidence_line(asset: ImpactedAsset) -> str:
    top = asset.worst_evidence()
    if not top:
        return ""
    text = top.detail
    if top.snippet:
        text += f" — `{top.snippet.strip()}`"
    return text


def _single_line(value: object) -> str:
    return " ".join(str(value).replace("\r", "\n").splitlines()).strip()


def _md_text(value: object) -> str:
    """Render untrusted catalog text without creating Markdown structure."""

    text = _single_line(value).replace("\\", "\\\\")
    for char in ("`", "*", "_", "[", "]", "<", ">", "#", "|"):
        text = text.replace(char, f"\\{char}")
    return text.replace("@", "&#64;")


def _md_code(value: object) -> str:
    """Render one line inside a Markdown code span."""

    return (
        _single_line(value)
        .replace("`", "'")
        .replace("|", "\\|")
        .replace("@", "&#64;")
    )


def _fenced_block(body: object, language: str = "") -> str:
    text = str(body).rstrip("\n") or "(empty)"
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _evidence_md(asset: ImpactedAsset) -> str:
    top = asset.worst_evidence()
    if not top:
        return ""
    text = _md_text(top.detail)
    if top.snippet:
        text += f" — `{_md_code(top.snippet)}`"
    return text


def rollout_plan(report: ImpactReport) -> list[str]:
    """Ordered steps that make the change safe to land."""
    change = report.change
    breaking = report.breaking
    at_risk = report.at_risk
    steps: list[str] = []

    if change.operation is Operation.RENAME_COLUMN:
        steps.append(
            f"**Expand.** Add `{_md_code(change.new_name)}` alongside "
            f"`{_md_code(change.column)}` in `{_md_code(change.table)}` "
            "(a view or a generated column is enough). Do not "
            "remove anything yet."
        )
    elif change.operation is Operation.DROP_COLUMN:
        steps.append(
            f"**Announce.** Append a warning to `{_md_code(change.table)}."
            f"{_md_code(change.column)}` "
            "in DataHub and save a linked impact analysis "
            "(`blast-radius plan ... --write-back` does this for you)."
        )
    elif change.operation is Operation.RETYPE_COLUMN:
        steps.append(
            f"**Stage.** Land the explicit casts below *before* changing the type "
            f"of `{_md_code(change.column)}`, so consumers are already type-correct."
        )
    else:
        steps.append(
            f"**Confirm retirement.** `{_md_code(change.table)}` is being dropped; every "
            "consumer below needs a new source or an owner sign-off to retire."
        )

    if breaking:
        names = ", ".join(f"`{_md_code(a.name)}`" for a in breaking[:8])
        more = "" if len(breaking) <= 8 else f" (+{len(breaking) - 8} more)"
        steps.append(
            f"**Migrate the {len(breaking)} breaking consumer"
            f"{'s' if len(breaking) != 1 else ''}:** {names}{more}. Patches for the "
            "queries we could rewrite are in `patches/`."
        )
    if at_risk:
        steps.append(
            f"**Get eyes on {len(at_risk)} unproven asset"
            f"{'s' if len(at_risk) != 1 else ''}.** These are downstream but we "
            "could not prove a reference — ask the owners listed below to confirm "
            "before you proceed."
        )

    steps.append(
        "**Re-run and require zero breaking.** `blast-radius plan --change <ddl> "
        "--fail-on breaking` in CI; when it exits 0, nothing indexed in DataHub "
        "still depends on the old shape."
    )
    if change.operation in (Operation.RENAME_COLUMN, Operation.DROP_COLUMN):
        steps.append(
            f"**Contract.** Only now {'drop the old column' if change.operation is Operation.RENAME_COLUMN else 'apply the DROP'}: "
            f"`{_md_code(change.source.splitlines()[0][:120])}`"
        )
    return steps


def _asset_table_md(assets: list[ImpactedAsset]) -> str:
    if not assets:
        return "_none_\n"
    lines = [
        "| asset | type | hops | owner | why |",
        "| --- | --- | --- | --- | --- |",
    ]
    for asset in assets:
        why = _evidence_md(asset)
        asset_type = _md_text(asset.entity_type)
        platform = f" ({_md_text(asset.platform)})" if asset.platform else ""
        lines.append(
            f"| `{_md_code(asset.name)}` | {asset_type}{platform} | {asset.hops} | "
            f"{_md_text(_owner_names(asset))} | {why} |"
        )
    return "\n".join(lines) + "\n"


def _patch_md(patch: Patch) -> str:
    flag = "mechanical" if patch.confidence == "mechanical" else "needs review"
    body = [
        f"#### {_md_text(patch.title)}",
        "",
        f"*{flag}* — {_md_text(patch.note)}",
        "",
        _fenced_block(patch.diff, "diff"),
    ]
    return "\n".join(body) + "\n"


def to_markdown(report: ImpactReport) -> str:
    change = report.change
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    breaking, at_risk, safe = report.breaking, report.at_risk, report.safe

    if breaking:
        headline = (
            f"**{len(breaking)} asset{'s' if len(breaking) != 1 else ''} will break.** "
            "Do not ship this change as-is."
        )
    elif at_risk:
        headline = (
            f"**Nothing provably breaks, but {len(at_risk)} asset"
            f"{'s' if len(at_risk) != 1 else ''} could not be cleared.** Get owner "
            "sign-off before shipping."
        )
    else:
        headline = "**No downstream impact found.** This change looks safe to land."

    out: list[str] = [
        f"# Blast radius: {_md_text(change.describe())}",
        "",
        headline,
        "",
        f"- **Change**: `{_md_code(change.describe())}`",
        f"- **Dataset**: `{_md_code(report.root_urn or change.table)}`",
        f"- **Downstream assets examined**: {len(report.assets)} "
        f"({len(breaking)} breaking, {len(at_risk)} at risk, {len(safe)} safe)",
        f"- **Generated**: {now} by "
        "[blast-radius](https://github.com/bgrubbs1/blast-radius)",
    ]
    if report.datahub_url:
        out.append(f"- **In DataHub**: `{_md_code(report.datahub_url)}`")
    out.append("")

    if report.summary:
        out += ["## Summary", "", _md_text(report.summary), ""]

    out += ["## Breaking", "", _asset_table_md(breaking)]
    out += ["## At risk (unproven — needs a human)", "", _asset_table_md(at_risk)]

    notify = report.owners_to_notify()
    out += ["## Who to notify", ""]
    if notify:
        for owner, assets in notify.items():
            worst = min(assets, key=lambda a: 0 if a.verdict is Verdict.BREAKING else 1)
            label = _VERDICT_LABEL[worst.verdict]
            names = ", ".join(f"`{_md_code(a.name)}`" for a in assets[:6])
            more = "" if len(assets) <= 6 else f" (+{len(assets) - 6} more)"
            out.append(f"- **{_md_text(owner)}** — {label}: {names}{more}")
    else:
        out.append("_no affected assets have owners recorded in DataHub_")
    out.append("")

    out += ["## Rollout order", ""]
    out += [f"{i}. {step}" for i, step in enumerate(rollout_plan(report), start=1)]
    out.append("")

    out += ["## Generated patches", ""]
    if report.patches:
        mech = sum(1 for p in report.patches if p.confidence == "mechanical")
        out.append(
            f"{len(report.patches)} patch{'es' if len(report.patches) != 1 else ''} — "
            f"{mech} mechanical, {len(report.patches) - mech} needing a decision. "
            "Files are in `patches/`."
        )
        out.append("")
        out += [_patch_md(p) for p in report.patches]
    else:
        out.append("_no rewritable SQL was found for the affected assets_\n")

    if safe:
        out += [
            "## Cleared",
            "",
            _asset_table_md(safe),
        ]

    out += [
        "## How this was computed",
        "",
        f"Every verdict above comes from DataHub metadata read over MCP "
        f"({len(report.tool_calls)} tool call"
        f"{'s' if len(report.tool_calls) != 1 else ''}). A verdict of *breaking* means "
        "a query indexed in DataHub names the changed column; *at risk* means the "
        "asset is downstream but the reference could not be proven either way.",
        "",
    ]
    if report.generated_with_llm:
        out.append(
            "> The prose summary was written by a language model from the findings "
            "below. Verdicts, evidence and patches are produced deterministically "
            "and are not model output.\n"
        )
    if report.warnings:
        out += ["### Warnings", ""]
        out += [f"- {_md_text(w)}" for w in dict.fromkeys(report.warnings)]
        out.append("")
    out += ["<details><summary>MCP tool calls</summary>", ""]
    out += [_fenced_block("\n".join(report.tool_calls)), "</details>", ""]
    return "\n".join(out)


def to_json(report: ImpactReport) -> str:
    payload = report.to_dict()
    payload["counts"] = {
        "breaking": len(report.breaking),
        "at_risk": len(report.at_risk),
        "safe": len(report.safe),
        "patches": len(report.patches),
    }
    return json.dumps(payload, indent=2, default=str)


def print_console(report: ImpactReport, console: Console | None = None) -> None:
    console = console or Console()
    change = report.change
    console.print()
    console.rule(f"[bold]blast radius[/bold] — {change.describe()}")

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("verdict", width=9)
    table.add_column("asset", overflow="fold")
    table.add_column("type", width=12)
    table.add_column("hops", width=4, justify="right")
    table.add_column("owner", overflow="fold")
    table.add_column("why", overflow="fold")

    for asset in report.assets:
        table.add_row(
            f"[{_VERDICT_STYLE[asset.verdict]}]{_VERDICT_LABEL[asset.verdict]}[/]",
            asset.name,
            asset.entity_type,
            str(asset.hops),
            _owner_names(asset),
            _evidence_line(asset),
        )
    if report.assets:
        console.print(table)
    else:
        console.print("[green]no downstream assets found[/green]")

    console.print(
        f"\n[bold]{len(report.breaking)}[/bold] breaking · "
        f"[bold]{len(report.at_risk)}[/bold] at risk · "
        f"[bold]{len(report.safe)}[/bold] safe · "
        f"[bold]{len(report.patches)}[/bold] patches"
    )
    for warning in dict.fromkeys(report.warnings):
        console.print(f"[yellow]warning:[/yellow] {warning}")
