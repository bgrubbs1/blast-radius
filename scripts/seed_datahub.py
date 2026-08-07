"""Seed a local DataHub Core instance with the demo warehouse.

Creates the scenario the README and the demo describe -- a Snowflake-shaped
warehouse where dropping ``analytics.public.fct_orders.discount_amount`` has
consequences -- so the tool can be exercised end to end against a real DataHub
rather than only against fixtures.

    python scripts/seed_datahub.py --gms http://localhost:8080

What it emits:

* 5 datasets with real schemas (snowflake)
* downstream lineage, including a 2-hop Looker dashboard and a Feast feature table
* ownership and domains, with one asset deliberately left unowned
* **Query entities** carrying the SQL that makes column-level impact provable --
  this is what ``get_dataset_queries`` returns and what Blast Radius parses

Safe to re-run: every aspect is emitted by URN, so a second run overwrites
rather than duplicates.
"""

from __future__ import annotations

import argparse
import time

from datahub.emitter.mce_builder import make_dataset_urn, make_domain_urn, make_group_urn, make_user_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    AuditStampClass,
    ChangeAuditStampsClass,
    ChangeTypeClass,
    DashboardInfoClass,
    DatasetPropertiesClass,
    DomainsClass,
    OtherSchemaClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    QueryLanguageClass,
    QueryPropertiesClass,
    QuerySourceClass,
    QueryStatementClass,
    QuerySubjectClass,
    QuerySubjectsClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    NumberTypeClass,
    TimeTypeClass,
    UpstreamClass,
    UpstreamLineageClass,
    DatasetLineageTypeClass,
)

PLATFORM = "snowflake"
ENV = "PROD"

ORDERS = "analytics.public.fct_orders"
LTV = "analytics.marts.dim_customer_ltv"
REVENUE = "analytics.marts.rpt_daily_revenue"
FLAT = "analytics.marts.mart_orders_flat"
AUDIT = "analytics.staging.stg_orders_audit"

DASHBOARD_URN = "urn:li:dashboard:(looker,finance_exec_overview)"
FEATURES_URN = "urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,order_propensity_v3)"

SCHEMAS: dict[str, list[tuple[str, str]]] = {
    ORDERS: [
        ("order_id", "string"),
        ("customer_id", "string"),
        ("order_ts", "time"),
        ("gross_amount", "number"),
        ("discount_amount", "number"),
        ("net_amount", "number"),
    ],
    LTV: [("customer_id", "string"), ("orders", "number"), ("total_discount", "number"), ("ltv", "number")],
    REVENUE: [("order_date", "time"), ("gross_amount", "number"), ("discount_amount", "number"), ("net_amount", "number")],
    FLAT: [("order_id", "string"), ("customer_id", "string"), ("total", "number")],
    AUDIT: [("order_id", "string"), ("loaded_at", "time"), ("row_hash", "string")],
}

LINEAGE: dict[str, list[str]] = {
    LTV: [ORDERS],
    REVENUE: [ORDERS],
    FLAT: [ORDERS],
    AUDIT: [ORDERS],
}

OWNERS: dict[str, tuple[str, bool]] = {
    ORDERS: ("sam.okafor", False),
    LTV: ("maya.iyer", False),
    REVENUE: ("finance-analytics", True),
    FLAT: ("sam.okafor", False),
    AUDIT: ("maya.iyer", False),
}

DOMAINS = {LTV: "customer", REVENUE: "finance", FLAT: "finance"}

QUERIES: list[tuple[str, str, str]] = [
    (
        "ltv-rollup",
        LTV,
        "SELECT o.customer_id,\n"
        "       COUNT(*) AS orders,\n"
        "       SUM(o.discount_amount) AS total_discount,\n"
        "       SUM(o.net_amount) AS ltv\n"
        "FROM analytics.public.fct_orders o\n"
        "GROUP BY o.customer_id",
    ),
    (
        "daily-revenue",
        REVENUE,
        "SELECT * FROM analytics.public.fct_orders WHERE order_ts >= CURRENT_DATE - 30",
    ),
    (
        "orders-flat",
        FLAT,
        "SELECT order_id, customer_id, net_amount AS total\n"
        "FROM analytics.public.fct_orders\n"
        "WHERE discount_amount > 0",
    ),
    (
        "audit-load",
        AUDIT,
        "SELECT order_id, CURRENT_TIMESTAMP AS loaded_at\nFROM analytics.public.fct_orders",
    ),
]

_TYPES = {
    "string": StringTypeClass(),
    "number": NumberTypeClass(),
    "time": TimeTypeClass(),
}


def dataset_urn(name: str) -> str:
    return make_dataset_urn(platform=PLATFORM, name=name, env=ENV)


def _stamp() -> AuditStampClass:
    return AuditStampClass(time=int(time.time() * 1000), actor="urn:li:corpuser:datahub")


def build_mcps() -> list[MetadataChangeProposalWrapper]:
    mcps: list[MetadataChangeProposalWrapper] = []

    for name, fields in SCHEMAS.items():
        urn = dataset_urn(name)
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=DatasetPropertiesClass(
                    name=name.rsplit(".", 1)[-1],
                    qualifiedName=name,
                    description=f"Demo asset for blast-radius ({name}).",
                ),
            )
        )
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=SchemaMetadataClass(
                    schemaName=name,
                    platform=f"urn:li:dataPlatform:{PLATFORM}",
                    version=0,
                    hash="",
                    platformSchema=OtherSchemaClass(rawSchema=""),
                    fields=[
                        SchemaFieldClass(
                            fieldPath=column,
                            type=SchemaFieldDataTypeClass(type=_TYPES[kind]),
                            nativeDataType=kind.upper(),
                        )
                        for column, kind in fields
                    ],
                ),
            )
        )

        owner, is_group = OWNERS[name]
        owner_urn = make_group_urn(owner) if is_group else make_user_urn(owner)
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=OwnershipClass(
                    owners=[OwnerClass(owner=owner_urn, type=OwnershipTypeClass.TECHNICAL_OWNER)]
                ),
            )
        )
        if name in DOMAINS:
            mcps.append(
                MetadataChangeProposalWrapper(
                    entityUrn=urn,
                    aspect=DomainsClass(domains=[make_domain_urn(DOMAINS[name])]),
                )
            )

    for downstream, upstreams in LINEAGE.items():
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=dataset_urn(downstream),
                aspect=UpstreamLineageClass(
                    upstreams=[
                        UpstreamClass(
                            dataset=dataset_urn(up), type=DatasetLineageTypeClass.TRANSFORMED
                        )
                        for up in upstreams
                    ]
                ),
            )
        )

    # A dashboard two hops out: consumes the marts, not the fact table directly.
    mcps.append(
        MetadataChangeProposalWrapper(
            entityUrn=DASHBOARD_URN,
            aspect=DashboardInfoClass(
                title="Finance Exec Overview",
                description="Weekly revenue review deck.",
                lastModified=ChangeAuditStampsClass(created=_stamp(), lastModified=_stamp()),
                datasets=[dataset_urn(REVENUE), dataset_urn(FLAT)],
            ),
        )
    )

    for query_id, consumer, sql in QUERIES:
        query_urn = f"urn:li:query:{query_id}"
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=query_urn,
                aspect=QueryPropertiesClass(
                    statement=QueryStatementClass(value=sql, language=QueryLanguageClass.SQL),
                    source=QuerySourceClass.MANUAL,
                    created=_stamp(),
                    lastModified=_stamp(),
                    name=query_id,
                    description=f"Populates {consumer}",
                ),
            )
        )
        # Subjects tie the query to the datasets it touches, which is how
        # get_dataset_queries finds it.
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=query_urn,
                aspect=QuerySubjectsClass(
                    subjects=[
                        QuerySubjectClass(entity=dataset_urn(consumer)),
                        QuerySubjectClass(entity=dataset_urn(ORDERS)),
                    ]
                ),
            )
        )

    return mcps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gms", default="http://localhost:8080")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    emitter = DatahubRestEmitter(gms_server=args.gms, token=args.token)
    mcps = build_mcps()
    for index, mcp in enumerate(mcps, start=1):
        mcp.changeType = ChangeTypeClass.UPSERT
        emitter.emit(mcp)
        if index % 10 == 0:
            print(f"  emitted {index}/{len(mcps)} aspects")
    print(f"emitted {len(mcps)} aspects for {len(SCHEMAS)} datasets, "
          f"{len(QUERIES)} queries, 1 dashboard")
    print(f"root dataset: {dataset_urn(ORDERS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
