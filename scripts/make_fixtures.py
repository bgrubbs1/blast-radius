"""Generate the recorded fixtures used by ``blast-radius demo`` and the tests.

This drives the *real* pipeline against a stub MCP session, so the fixture
filenames are produced by the same hashing path as a live ``--record`` run. If
the tool ever changes the arguments it sends, regenerating here keeps the demo
honest instead of silently drifting.

    python scripts/make_fixtures.py

The scenario is a small Snowflake warehouse where someone proposes dropping
``analytics.public.fct_orders.discount_amount``: two consumers provably break,
one drifts via ``SELECT *``, a dashboard and an ML feature table cannot be
proven either way, and one staging table is genuinely clear.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blastradius.change import parse_change  # noqa: E402
from blastradius.datahub import DataHubMCP  # noqa: E402
from blastradius.impact import analyse  # noqa: E402
from blastradius.patch import patches_for  # noqa: E402
from blastradius.report import to_json, to_markdown  # noqa: E402

FIXTURES = ROOT / "fixtures"
EXAMPLES = ROOT / "examples"

PLATFORM = "urn:li:dataPlatform:snowflake"


def dataset(name: str) -> str:
    return f"urn:li:dataset:({PLATFORM},{name},PROD)"


ROOT_URN = dataset("analytics.public.fct_orders")
LTV = dataset("analytics.marts.dim_customer_ltv")
REVENUE = dataset("analytics.marts.rpt_daily_revenue")
FLAT = dataset("analytics.marts.mart_orders_flat")
AUDIT = dataset("analytics.staging.stg_orders_audit")
DASHBOARD = "urn:li:dashboard:(looker,finance_exec_overview)"
FEATURES = "urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,order_propensity_v3)"

# The MCP server's canonical tool surface (mirrors mcp-server-datahub).
TOOL_SCHEMAS = {
    "search": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "entity_types": {"type": "array"},
            "limit": {"type": "integer"},
        },
    },
    "get_entities": {"type": "object", "properties": {"urns": {"type": "array"}}},
    "get_lineage": {
        "type": "object",
        "properties": {
            "urn": {"type": "string"},
            "direction": {"type": "string"},
            "max_hops": {"type": "integer"},
        },
    },
    "list_schema_fields": {"type": "object", "properties": {"urn": {"type": "string"}}},
    "get_dataset_queries": {
        "type": "object",
        "properties": {"urn": {"type": "string"}, "limit": {"type": "integer"}},
    },
    "get_me": {"type": "object", "properties": {}},
}

OWNERS = {
    LTV: [("urn:li:corpuser:maya.iyer", "maya.iyer", "TECHNICAL_OWNER")],
    REVENUE: [("urn:li:corpGroup:finance-analytics", "finance-analytics", "DATAOWNER")],
    FLAT: [("urn:li:corpuser:sam.okafor", "sam.okafor", "TECHNICAL_OWNER")],
    AUDIT: [("urn:li:corpuser:maya.iyer", "maya.iyer", "TECHNICAL_OWNER")],
    DASHBOARD: [("urn:li:corpGroup:finance-analytics", "finance-analytics", "DATAOWNER")],
    FEATURES: [],  # deliberately unowned -- the report should call this out
    ROOT_URN: [("urn:li:corpuser:sam.okafor", "sam.okafor", "TECHNICAL_OWNER")],
}
DOMAINS = {
    LTV: "urn:li:domain:(customer)",
    REVENUE: "urn:li:domain:(finance)",
    FLAT: "urn:li:domain:(finance)",
    DASHBOARD: "urn:li:domain:(finance)",
}
NAMES = {
    ROOT_URN: "analytics.public.fct_orders",
    LTV: "analytics.marts.dim_customer_ltv",
    REVENUE: "analytics.marts.rpt_daily_revenue",
    FLAT: "analytics.marts.mart_orders_flat",
    AUDIT: "analytics.staging.stg_orders_audit",
    DASHBOARD: "Finance Exec Overview",
    FEATURES: "order_propensity_v3",
}

SCHEMAS = {
    ROOT_URN: ["order_id", "customer_id", "order_ts", "gross_amount", "discount_amount", "net_amount"],
    LTV: ["customer_id", "orders", "total_discount", "ltv"],
    REVENUE: ["order_date", "gross_amount", "discount_amount", "net_amount"],
    FLAT: ["order_id", "customer_id", "total"],
    AUDIT: ["order_id", "loaded_at", "row_hash"],
}

QUERIES = {
    LTV: [
        (
            "urn:li:query:ltv-rollup",
            "SELECT o.customer_id,\n"
            "       COUNT(*) AS orders,\n"
            "       SUM(o.discount_amount) AS total_discount,\n"
            "       SUM(o.net_amount) AS ltv\n"
            "FROM analytics.public.fct_orders o\n"
            "GROUP BY o.customer_id",
        )
    ],
    REVENUE: [
        (
            "urn:li:query:daily-revenue",
            "SELECT * FROM analytics.public.fct_orders WHERE order_ts >= CURRENT_DATE - 30",
        )
    ],
    FLAT: [
        (
            "urn:li:query:orders-flat",
            "SELECT order_id, customer_id, net_amount AS total\n"
            "FROM analytics.public.fct_orders\n"
            "WHERE discount_amount > 0",
        )
    ],
    AUDIT: [
        (
            "urn:li:query:audit-load",
            "SELECT order_id, CURRENT_TIMESTAMP AS loaded_at\nFROM analytics.public.fct_orders",
        )
    ],
}

LINEAGE = {
    ROOT_URN: [(LTV, 1), (REVENUE, 1), (FLAT, 1), (AUDIT, 1), (DASHBOARD, 2), (FEATURES, 2)]
}


def _entity(urn: str) -> dict:
    payload: dict = {"urn": urn, "name": NAMES.get(urn, urn), "type": urn.split(":")[2]}
    owners = OWNERS.get(urn, [])
    if owners:
        payload["ownership"] = {
            "owners": [
                {"owner": {"urn": o[0], "username": o[1]}, "type": o[2]} for o in owners
            ]
        }
    if urn in DOMAINS:
        payload["domain"] = {"urn": DOMAINS[urn]}
    return payload


class StubSession:
    """Answers MCP tool calls from the tables above."""

    async def call_tool(self, name: str, args: dict):
        payload = self._dispatch(name, args)
        return SimpleNamespace(
            isError=False,
            structuredContent=None,
            content=[SimpleNamespace(text=json.dumps(payload), type="text")],
        )

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "search":
            query = str(args.get("query", "")).lower()
            hits = [u for u in NAMES if query and query in u.lower()]
            return {
                "searchResults": [
                    {"entity": {"urn": urn, "name": NAMES[urn]}} for urn in hits
                ]
            }
        if name == "get_lineage":
            urn = args.get("urn")
            hops = int(args.get("max_hops", 1))
            edges = LINEAGE.get(urn, [])
            return {
                "urn": urn,
                "direction": args.get("direction", "DOWNSTREAM"),
                "results": [
                    {"urn": child, "degree": degree, "name": NAMES.get(child, child)}
                    for child, degree in edges
                    if degree <= hops
                ],
            }
        if name == "get_entities":
            urns = args.get("urns") or []
            if isinstance(urns, str):
                urns = [urns]
            return {"entities": [_entity(u) for u in urns]}
        if name == "list_schema_fields":
            urn = args.get("urn")
            return {
                "urn": urn,
                "fields": [
                    {"fieldPath": f, "type": "NUMBER" if "amount" in f else "STRING"}
                    for f in SCHEMAS.get(urn, [])
                ],
            }
        if name == "get_dataset_queries":
            urn = args.get("urn")
            return {
                "urn": urn,
                "queries": [
                    {"urn": qurn, "statement": sql} for qurn, sql in QUERIES.get(urn, [])
                ],
            }
        if name == "get_me":
            return {"corpUser": {"urn": "urn:li:corpuser:datahub", "username": "datahub"}}
        return {}


async def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "_tools.json").write_text(
        json.dumps(TOOL_SCHEMAS, indent=2, sort_keys=True), encoding="utf-8"
    )

    change = parse_change("ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount")
    hub = DataHubMCP(fixtures_dir=FIXTURES, record=True)
    hub._session = StubSession()  # noqa: SLF001 - fixture generation is internal
    hub._schemas = dict(TOOL_SCHEMAS)  # noqa: SLF001

    result = await analyse(hub, change, depth=2, datahub_frontend="http://localhost:9002")
    result.patches = patches_for(result)

    EXAMPLES.mkdir(parents=True, exist_ok=True)
    (EXAMPLES / "impact-report.md").write_text(to_markdown(result), encoding="utf-8")
    (EXAMPLES / "impact.json").write_text(to_json(result), encoding="utf-8")
    patch_dir = EXAMPLES / "patches"
    patch_dir.mkdir(exist_ok=True)
    for index, patch in enumerate(result.patches, start=1):
        stem = f"{index:02d}-{patch.target.rsplit(',', 2)[-2].replace('.', '_')}"
        (patch_dir / f"{stem}.diff").write_text(patch.diff, encoding="utf-8")
        (patch_dir / f"{stem}.sql").write_text(patch.updated, encoding="utf-8")

    print(f"fixtures: {len(list(FIXTURES.glob('*.json')))} files in {FIXTURES}")
    print(
        f"verdicts: {len(result.breaking)} breaking, {len(result.at_risk)} at risk, "
        f"{len(result.safe)} safe, {len(result.patches)} patches"
    )
    for asset in result.assets:
        print(f"  {asset.verdict.value:9} {asset.name}")


if __name__ == "__main__":
    asyncio.run(main())
