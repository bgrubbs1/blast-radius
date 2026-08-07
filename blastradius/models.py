"""Core data model.

Everything that flows between the change parser, the impact engine, the patch
writer and the report renderer is one of these dataclasses. They are plain and
JSON-serialisable on purpose: the whole run is dumped to ``impact.json`` so the
output can be diffed, asserted on in tests, and consumed by CI.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operation(str, Enum):
    """The kinds of schema change we can reason about."""

    DROP_COLUMN = "drop_column"
    RENAME_COLUMN = "rename_column"
    RETYPE_COLUMN = "retype_column"
    DROP_TABLE = "drop_table"


class Verdict(str, Enum):
    """How badly a downstream asset is affected.

    BREAKING  we found direct evidence (a query or a schema field) that
              references the changed column and will fail or silently change.
    AT_RISK   the asset is downstream of the change but we could not prove a
              reference either way -- no queries indexed, or SQL we could not
              parse. A human has to look.
    SAFE      downstream, but nothing references the changed column.
    """

    BREAKING = "breaking"
    AT_RISK = "at_risk"
    SAFE = "safe"


# Order matters: used to sort and to pick the worst verdict of a group.
VERDICT_SEVERITY = {Verdict.BREAKING: 0, Verdict.AT_RISK: 1, Verdict.SAFE: 2}


@dataclass
class SchemaChange:
    """A proposed change, as parsed from DDL or a dbt model diff."""

    operation: Operation
    table: str
    columns: list[str] = field(default_factory=list)
    new_name: str | None = None
    new_type: str | None = None
    source: str = ""
    dialect: str | None = None

    @property
    def column(self) -> str | None:
        return self.columns[0] if self.columns else None

    def describe(self) -> str:
        if self.operation is Operation.DROP_TABLE:
            return f"DROP TABLE {self.table}"
        if self.operation is Operation.DROP_COLUMN:
            return f"DROP COLUMN {self.table}.{', '.join(self.columns)}"
        if self.operation is Operation.RENAME_COLUMN:
            return f"RENAME COLUMN {self.table}.{self.column} -> {self.new_name}"
        return f"RETYPE COLUMN {self.table}.{self.column} -> {self.new_type}"


@dataclass
class Evidence:
    """One concrete reason we believe an asset breaks.

    ``detail`` is short and quotable -- it goes straight into the report so a
    reviewer can judge the finding without re-running anything.
    """

    kind: str  # "query" | "schema_field" | "lineage_only" | "unparsed_sql"
    detail: str
    snippet: str | None = None  # short, quotable -- goes in the report
    source_ref: str | None = None  # query URN, field path, ...
    statement: str | None = None  # full SQL, when there is one -- input to patching


@dataclass
class Owner:
    urn: str
    name: str
    type: str = "unknown"  # TECHNICAL_OWNER, DATAOWNER, ...


@dataclass
class ImpactedAsset:
    """A downstream entity plus why it matters."""

    urn: str
    name: str
    entity_type: str  # dataset, dashboard, chart, mlFeatureTable, ...
    platform: str | None = None
    hops: int = 1
    verdict: Verdict = Verdict.AT_RISK
    evidence: list[Evidence] = field(default_factory=list)
    owners: list[Owner] = field(default_factory=list)
    domain: str | None = None

    def worst_evidence(self) -> Evidence | None:
        return self.evidence[0] if self.evidence else None


@dataclass
class Patch:
    """A generated code change, ready to become a pull request."""

    target: str  # asset urn or file path
    title: str
    language: str  # sql, yaml, ...
    original: str
    updated: str
    diff: str
    confidence: str = "review"  # "mechanical" | "review"
    note: str = ""


@dataclass
class ImpactReport:
    change: SchemaChange
    root_urn: str | None
    assets: list[ImpactedAsset] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    summary: str = ""
    datahub_url: str | None = None
    generated_with_llm: bool = False
    warnings: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

    # -- aggregates used by the report and by the CLI exit code ---------------

    def by_verdict(self, verdict: Verdict) -> list[ImpactedAsset]:
        return [a for a in self.assets if a.verdict is verdict]

    @property
    def breaking(self) -> list[ImpactedAsset]:
        return self.by_verdict(Verdict.BREAKING)

    @property
    def at_risk(self) -> list[ImpactedAsset]:
        return self.by_verdict(Verdict.AT_RISK)

    @property
    def safe(self) -> list[ImpactedAsset]:
        return self.by_verdict(Verdict.SAFE)

    def owners_to_notify(self) -> dict[str, list[ImpactedAsset]]:
        """Group affected assets by owner, worst first.

        Assets with no owner in DataHub land under ``"(unowned)"`` -- that gap
        is itself worth reporting, since nobody will answer for them.
        """
        grouped: dict[str, list[ImpactedAsset]] = {}
        for asset in sorted(self.assets, key=lambda a: VERDICT_SEVERITY[a.verdict]):
            if asset.verdict is Verdict.SAFE:
                continue
            if not asset.owners:
                grouped.setdefault("(unowned)", []).append(asset)
            for owner in asset.owners:
                grouped.setdefault(owner.name, []).append(asset)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
