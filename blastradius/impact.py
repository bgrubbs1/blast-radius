"""The impact engine: what does this change actually break?

The traversal is deterministic. No model decides whether an asset is affected --
a verdict comes from evidence we can point at:

* a query in DataHub that names the column        -> BREAKING
* a ``SELECT *`` over the changed table           -> AT_RISK (schema drift)
* a downstream column with the same name          -> AT_RISK (propagated)
* SQL we could not parse, or no queries indexed   -> AT_RISK (unknown)
* parsed everything, found nothing                -> SAFE

An LLM may later *narrate* this report, but it cannot promote or demote a
verdict. That separation is deliberate: a migration plan is only useful if the
severity is reproducible.
"""

from __future__ import annotations

from . import extract
from .change import table_leaf
from .datahub import DataHubMCP
from .models import (
    VERDICT_SEVERITY,
    Evidence,
    ImpactedAsset,
    ImpactReport,
    Operation,
    Owner,
    SchemaChange,
    Verdict,
)

# Entities we can inspect column-by-column. Everything else is judged by
# lineage alone.
INSPECTABLE = {"dataset"}


async def resolve_dataset(hub: DataHubMCP, table: str) -> str | None:
    """Find the dataset URN for a (possibly qualified) table name."""
    leaf = table_leaf(table)
    for query in (table, leaf):
        payload = await hub.search(query, entity_types=["DATASET"], limit=25)
        if payload is None:
            continue
        candidates = [u for u in extract.all_urns(payload) if u.startswith("urn:li:dataset:")]
        if not candidates:
            continue

        exact = [u for u in candidates if (extract.dataset_name(u) or "").lower() == table]
        if exact:
            return exact[0]
        leaf_match = [
            u
            for u in candidates
            if (extract.dataset_name(u) or "").lower().rsplit(".", 1)[-1] == leaf
        ]
        if leaf_match:
            return leaf_match[0]
        return candidates[0]
    return None


def _lineage_hops(payload: object, root_urn: str) -> dict[str, int]:
    """Downstream URN -> hop count, reading whatever the lineage payload gives us.

    DataHub reports the hop count as ``degree``/``hops``/``distance`` depending
    on version; when absent we fall back to 1 so the asset is still reported
    (an unknown distance must never hide an asset).
    """
    hops: dict[str, int] = {}
    for node in extract.walk(payload):
        urn = node.get("urn") or node.get("entityUrn") or node.get("entity")
        if isinstance(urn, dict):
            urn = urn.get("urn")
        if not isinstance(urn, str) or not urn.startswith("urn:li:"):
            continue
        if urn == root_urn:
            continue
        distance = 1
        for key in ("degree", "hops", "distance", "hop", "numHops"):
            value = node.get(key)
            if isinstance(value, int) and value > 0:
                distance = value
                break
        hops[urn] = min(hops.get(urn, distance), distance)

    for urn in extract.all_urns(payload):
        if urn == root_urn or urn in hops:
            continue
        if urn.startswith("urn:li:dataPlatform:") or urn.startswith("urn:li:corp"):
            continue
        hops[urn] = 1
    return hops


async def _classify_dataset(
    hub: DataHubMCP,
    asset: ImpactedAsset,
    change: SchemaChange,
    query_limit: int,
) -> None:
    """Attach evidence and a verdict to one downstream dataset."""
    if change.operation is Operation.DROP_TABLE:
        asset.verdict = Verdict.BREAKING
        asset.evidence.append(
            Evidence(
                kind="lineage_only",
                detail=f"consumes {change.table}, which is being dropped",
            )
        )
        return

    column = change.column or ""
    table = change.table

    fields_payload = await hub.list_schema_fields(asset.urn)
    field_names = extract.schema_field_names(fields_payload) if fields_payload else []
    if any(name.lower() == column.lower() for name in field_names):
        asset.evidence.append(
            Evidence(
                kind="schema_field",
                detail=f"exposes a column named '{column}' of its own -- likely propagated",
                source_ref=asset.urn,
            )
        )

    queries_payload = await hub.get_dataset_queries(asset.urn, limit=query_limit)
    statements = extract.sql_statements(queries_payload) if queries_payload else []

    propagated = bool(asset.evidence)  # a same-named column was found above
    names_column = False
    schema_drift = False
    unparsed = False

    for sql, ref in statements:
        for kind, snippet in extract.find_column_references(
            sql, column, table, dialect=change.dialect
        ):
            if kind == "column_ref":
                names_column = True
                asset.evidence.insert(
                    0,
                    Evidence(
                        kind="query",
                        detail=f"a query on this asset references {table}.{column}",
                        snippet=snippet,
                        source_ref=ref,
                        statement=sql,
                    ),
                )
            elif kind == "star_select":
                schema_drift = True
                asset.evidence.append(
                    Evidence(
                        kind="query",
                        detail=f"SELECT * over {table} -- this asset's output schema shifts",
                        snippet=snippet,
                        source_ref=ref,
                        statement=sql,
                    )
                )
            else:
                unparsed = True
                asset.evidence.append(
                    Evidence(
                        kind="unparsed_sql",
                        detail="a query could not be parsed; review by hand",
                        snippet=snippet,
                        source_ref=ref,
                        statement=sql,
                    )
                )

    if not statements:
        asset.evidence.append(
            Evidence(
                kind="lineage_only",
                detail="downstream of the change, but DataHub has no queries indexed "
                "for it -- impact unproven",
            )
        )
    elif not (names_column or schema_drift or unparsed):
        plural = "y" if len(statements) == 1 else "ies"
        asset.evidence.append(
            Evidence(
                kind="lineage_only",
                detail=f"{len(statements)} indexed quer{plural} parsed; "
                f"none reference {column}",
            )
        )

    if names_column:
        asset.verdict = Verdict.BREAKING
    elif schema_drift or unparsed or propagated or not statements:
        asset.verdict = Verdict.AT_RISK
    else:
        asset.verdict = Verdict.SAFE


async def analyse(
    hub: DataHubMCP,
    change: SchemaChange,
    depth: int = 2,
    root_urn: str | None = None,
    query_limit: int = 25,
    datahub_frontend: str | None = None,
) -> ImpactReport:
    """Walk downstream from ``change.table`` and classify everything we find."""
    report = ImpactReport(change=change, root_urn=root_urn)

    if root_urn is None:
        root_urn = await resolve_dataset(hub, change.table)
        report.root_urn = root_urn
    if root_urn is None:
        report.warnings.append(
            f"could not find a dataset matching '{change.table}' in DataHub -- "
            "pass --urn to name it explicitly"
        )
        report.warnings.extend(hub.warnings)
        report.tool_calls = list(hub.tool_calls)
        return report

    lineage = await hub.get_lineage(root_urn, direction="DOWNSTREAM", hops=depth)
    hops = _lineage_hops(lineage, root_urn) if lineage else {}

    urns = list(hops)
    entities = await hub.get_entities([root_urn] + urns) if urns else await hub.get_entities([root_urn])

    for urn, distance in hops.items():
        etype = extract.entity_type(urn)
        asset = ImpactedAsset(
            urn=urn,
            name=extract.display_name(urn, entities),
            entity_type=etype,
            platform=extract.platform_of(urn),
            hops=distance,
        )
        for owner_urn, owner_name, owner_type in extract.owners_of(urn, entities):
            asset.owners.append(Owner(urn=owner_urn, name=owner_name, type=owner_type))
        asset.domain = extract.domain_of(urn, entities)

        if etype in INSPECTABLE:
            await _classify_dataset(hub, asset, change, query_limit)
        else:
            asset.verdict = (
                Verdict.BREAKING
                if change.operation is Operation.DROP_TABLE
                else Verdict.AT_RISK
            )
            asset.evidence.append(
                Evidence(
                    kind="lineage_only",
                    detail=f"{etype} consuming the change -- its definition lives "
                    "outside DataHub's SQL index, so a human must confirm",
                )
            )
        report.assets.append(asset)

    report.assets.sort(
        key=lambda a: (VERDICT_SEVERITY[a.verdict], a.hops, a.name.lower())
    )
    report.warnings.extend(hub.warnings)
    report.tool_calls = list(hub.tool_calls)
    if datahub_frontend and root_urn:
        report.datahub_url = f"{datahub_frontend.rstrip('/')}/dataset/{root_urn}"
    return report
