"""Command line entry point.

    blast-radius plan   --change change.sql [--depth 2] [--out out/]
    blast-radius demo   [--change ...]        # replays recorded fixtures, no DataHub
    blast-radius doctor                       # is the MCP server reachable?

``plan`` is CI-shaped: ``--fail-on breaking`` exits non-zero when something
downstream provably breaks, so a schema change can be gated on it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from . import llm, report as report_mod
from .change import parse_change
from .datahub import DEFAULT_GMS_URL, DataHubError, DataHubMCP
from .impact import analyse
from .models import ImpactReport
from .patch import patches_for
from .writeback import write_back

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 2, 1


def _read_change(args: argparse.Namespace, console: Console) -> str:
    if args.change:
        candidate = Path(args.change)
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
        return args.change
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    console.print("[red]error:[/red] pass --change '<DDL>' , a file path, or pipe it in")
    raise SystemExit(EXIT_ERROR)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blast-radius",
        description="Compute the blast radius of a schema change from DataHub "
        "metadata, then write the migration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--gms-url", default=None, help=f"DataHub GMS (default {DEFAULT_GMS_URL})")
        p.add_argument("--token", default=None, help="DataHub personal access token")
        p.add_argument("--frontend", default=None, help="DataHub UI base URL, for links")
        p.add_argument("--fixtures", default=str(FIXTURES), help="fixture directory")

    plan = sub.add_parser("plan", help="analyse a proposed change")
    add_common(plan)
    plan.add_argument("--change", "-c", help="DDL string, a file, or a dbt diff file")
    plan.add_argument("--table", help="override the table the change applies to")
    plan.add_argument("--urn", help="skip search and use this dataset URN")
    plan.add_argument("--dialect", help="SQL dialect (snowflake, postgres, ...)")
    plan.add_argument("--depth", type=int, default=2, help="lineage hops (default 2)")
    plan.add_argument("--query-limit", type=int, default=25, help="queries per asset")
    plan.add_argument("--out", "-o", default=None, help="write report/patches here")
    plan.add_argument("--llm", action="store_true", help="add an LLM prose summary")
    plan.add_argument("--llm-base-url", default=None, help="OpenAI-compatible endpoint")
    plan.add_argument("--llm-model", default=None)
    plan.add_argument("--llm-provider", choices=["openai", "anthropic"], default="openai")
    plan.add_argument("--write-back", action="store_true", help="annotate DataHub")
    plan.add_argument("--record", action="store_true", help="save MCP responses as fixtures")
    plan.add_argument("--offline", action="store_true", help="replay fixtures only")
    plan.add_argument(
        "--fail-on",
        choices=["breaking", "at-risk", "never"],
        default="never",
        help="exit non-zero when findings at this level or worse exist",
    )

    demo = sub.add_parser("demo", help="run against recorded fixtures (no DataHub)")
    add_common(demo)
    demo.add_argument(
        "--change",
        "-c",
        default="ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount",
        help="defaults to the recorded scenario",
    )
    demo.add_argument("--dialect", default=None)
    demo.add_argument("--depth", type=int, default=2)
    demo.add_argument("--out", "-o", default=None)

    doctor = sub.add_parser("doctor", help="check DataHub + MCP connectivity")
    add_common(doctor)
    return parser


async def _run_plan(args: argparse.Namespace, console: Console) -> int:
    text = _read_change(args, console)
    try:
        change = parse_change(text, dialect=getattr(args, "dialect", None), table=getattr(args, "table", None))
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        return EXIT_ERROR

    console.print(f"[bold]change:[/bold] {change.describe()}")

    offline = getattr(args, "offline", False) or args.command == "demo"
    async with DataHubMCP(
        gms_url=args.gms_url,
        token=args.token,
        fixtures_dir=Path(args.fixtures),
        offline=offline,
        record=getattr(args, "record", False),
        mutations=getattr(args, "write_back", False),
    ) as hub:
        result = await analyse(
            hub,
            change,
            depth=args.depth,
            root_urn=getattr(args, "urn", None),
            query_limit=getattr(args, "query_limit", 25),
            datahub_frontend=args.frontend,
        )
        result.patches = patches_for(result, dialect=getattr(args, "dialect", None))

        if getattr(args, "llm", False):
            summary, warning = llm.narrate(
                result,
                base_url=args.llm_base_url,
                model=args.llm_model,
                provider=args.llm_provider,
            )
            if summary:
                result.summary = summary
                result.generated_with_llm = True
            if warning:
                result.warnings.append(warning)

        if getattr(args, "write_back", False):
            for line in await write_back(hub, result):
                console.print(f"[cyan]write-back:[/cyan] {line}")

    report_mod.print_console(result, console)

    if args.out:
        _write_outputs(result, Path(args.out), console)

    threshold = getattr(args, "fail_on", "never")
    if threshold == "breaking" and result.breaking:
        return EXIT_FINDINGS
    if threshold == "at-risk" and (result.breaking or result.at_risk):
        return EXIT_FINDINGS
    return EXIT_OK


def _write_outputs(result: ImpactReport, out_dir: Path, console: Console) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "impact-report.md").write_text(report_mod.to_markdown(result), encoding="utf-8")
    (out_dir / "impact.json").write_text(report_mod.to_json(result), encoding="utf-8")

    if result.patches:
        patch_dir = out_dir / "patches"
        patch_dir.mkdir(exist_ok=True)
        for index, patch in enumerate(result.patches, start=1):
            stem = f"{index:02d}-{patch.target.rsplit(',', 2)[-2] if ',' in patch.target else 'patch'}"
            stem = "".join(c if c.isalnum() or c in "-._" else "-" for c in stem)[:60]
            (patch_dir / f"{stem}.diff").write_text(patch.diff, encoding="utf-8")
            (patch_dir / f"{stem}.sql").write_text(patch.updated, encoding="utf-8")

    notify = result.owners_to_notify()
    if notify:
        lines = ["# Notify", ""]
        for owner, assets in notify.items():
            lines.append(f"## {owner}")
            lines.append("")
            for asset in assets:
                top = asset.worst_evidence()
                lines.append(
                    f"- `{asset.name}` ({asset.verdict.value})"
                    + (f" — {top.detail}" if top else "")
                )
            lines.append("")
        (out_dir / "notify.md").write_text("\n".join(lines), encoding="utf-8")

    console.print(f"\nwrote [bold]{out_dir}/impact-report.md[/bold], impact.json"
                  + (", patches/" if result.patches else "")
                  + (", notify.md" if notify else ""))


async def _run_doctor(args: argparse.Namespace, console: Console) -> int:
    console.print(f"GMS URL: [bold]{args.gms_url or DEFAULT_GMS_URL}[/bold]")
    try:
        async with DataHubMCP(gms_url=args.gms_url, token=args.token) as hub:
            tools = hub.available_tools
            console.print(f"MCP server: [green]up[/green] — {len(tools)} tools")
            console.print("tools: " + ", ".join(tools))
            for required in ("search", "get_lineage"):
                mark = "[green]yes[/green]" if hub.has_tool(required) else "[red]no[/red]"
                console.print(f"  {required}: {mark}")
            who = await hub.whoami()
            if who:
                console.print(f"authenticated as: {who}")
            for warning in hub.warnings:
                console.print(f"[yellow]warning:[/yellow] {warning}")
            return EXIT_OK if hub.has_tool("get_lineage") else EXIT_ERROR
    except DataHubError as exc:
        console.print(f"[red]not reachable:[/red] {exc}")
        console.print(
            "\nchecklist:\n"
            "  1. is DataHub running?  datahub docker quickstart\n"
            "  2. is the token set?    export DATAHUB_GMS_TOKEN=...\n"
            "  3. is uv installed?     uvx --version"
        )
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    console = Console()
    try:
        if args.command == "doctor":
            return asyncio.run(_run_doctor(args, console))
        return asyncio.run(_run_plan(args, console))
    except DataHubError as exc:
        console.print(f"[red]error:[/red] {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
