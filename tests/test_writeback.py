from __future__ import annotations

import asyncio

from blastradius.models import ImpactedAsset, ImpactReport, Operation, SchemaChange, Verdict
from blastradius.writeback import write_back


ROOT_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)"
IMPACTED_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)"


class FakeHub:
    def __init__(self, tools: list[str], *, mutations: bool = True) -> None:
        self.available_tools = tools
        self.mutations = mutations
        self.calls: list[tuple[str, dict]] = []

    def has_tool(self, *names: str) -> str | None:
        return next((name for name in names if name in self.available_tools), None)

    async def call(self, tool: str, args: dict):
        self.calls.append((tool, args))
        return {"success": True}


def sample_report() -> ImpactReport:
    return ImpactReport(
        change=SchemaChange(
            operation=Operation.DROP_COLUMN,
            table="analytics.public.fct_orders",
            columns=["discount_amount"],
        ),
        root_urn=ROOT_URN,
        assets=[
            ImpactedAsset(
                urn=IMPACTED_URN,
                name="dim_customer_ltv",
                entity_type="dataset",
                verdict=Verdict.BREAKING,
            )
        ],
    )


def test_writeback_uses_current_datahub_mutation_schemas():
    hub = FakeHub(["update_description", "save_document"])

    results = asyncio.run(write_back(hub, sample_report()))

    assert [tool for tool, _ in hub.calls] == ["update_description", "save_document"]
    description = hub.calls[0][1]
    assert description["entity_urn"] == ROOT_URN
    assert description["operation"] == "append"
    assert description["column_path"] == "discount_amount"
    assert "2" not in description["description"]
    document = hub.calls[1][1]
    assert document["document_type"] == "Analysis"
    assert document["related_assets"] == [ROOT_URN, IMPACTED_URN]
    assert "warning" in " ".join(results).lower()
    assert "analysis" in " ".join(results).lower()


def test_writeback_requires_explicit_mutation_enablement():
    hub = FakeHub(["update_description", "save_document"], mutations=False)

    results = asyncio.run(write_back(hub, sample_report()))

    assert hub.calls == []
    assert results == ["write-back skipped: mutations are not enabled on the MCP server"]
