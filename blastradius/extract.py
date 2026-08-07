"""Tolerant readers for DataHub payloads and SQL.

The MCP server's exact JSON shape varies by version and by entity type, so
nothing here assumes a fixed path. Each helper walks whatever nested structure
it is handed and pulls out the first plausible value. When a helper cannot find
something it returns ``None`` and the caller records "unknown" -- never "safe".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

_URN_RE = re.compile(r"urn:li:[a-zA-Z]+:\([^)]*\)|urn:li:[a-zA-Z]+:[\w.\-/@:]+")
_ENTITY_TYPE_RE = re.compile(r"^urn:li:([a-zA-Z]+):")
_PLATFORM_RE = re.compile(r"urn:li:dataPlatform:([\w-]+)")
_DATASET_NAME_RE = re.compile(r"urn:li:dataset:\(urn:li:dataPlatform:[\w-]+,([^,]+),")

NAME_KEYS = ("qualifiedName", "name", "displayName", "title", "urn")


def walk(obj: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict nested anywhere inside ``obj``."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from walk(item)


def all_urns(obj: Any) -> list[str]:
    """Every DataHub URN appearing anywhere in a payload, de-duplicated."""
    found: list[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, str):
            for match in _URN_RE.findall(node):
                if match not in seen:
                    seen.add(match)
                    found.append(match)
        elif isinstance(node, dict):
            for value in node.values():
                visit(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)

    visit(obj)
    return found


def entity_type(urn: str) -> str:
    match = _ENTITY_TYPE_RE.match(urn)
    return match.group(1) if match else "unknown"


def platform_of(urn: str) -> str | None:
    match = _PLATFORM_RE.search(urn)
    return match.group(1) if match else None


def dataset_name(urn: str) -> str | None:
    """``urn:li:dataset:(urn:li:dataPlatform:hive,db.tbl,PROD)`` -> ``db.tbl``."""
    match = _DATASET_NAME_RE.search(urn)
    return match.group(1) if match else None


def display_name(urn: str, payload: Any = None) -> str:
    """Best human-readable name for an URN, preferring metadata over the URN."""
    if payload is not None:
        for node in walk(payload):
            if node.get("urn") != urn:
                continue
            for key in NAME_KEYS:
                value = node.get(key)
                if isinstance(value, str) and value and not value.startswith("urn:li:"):
                    return value
            props = node.get("properties") or node.get("datasetProperties")
            if isinstance(props, dict):
                for key in NAME_KEYS:
                    value = props.get(key)
                    if isinstance(value, str) and value:
                        return value
    return dataset_name(urn) or urn.rsplit(":", 1)[-1].strip("()")


def owners_of(urn: str, payload: Any) -> list[tuple[str, str, str]]:
    """Owners as ``(urn, display name, ownership type)`` for one entity."""
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for node in walk(payload):
        if node.get("urn") != urn:
            continue
        for candidate in walk(node.get("ownership") or node.get("owners") or {}):
            owner = candidate.get("owner")
            owner_urn = (
                owner.get("urn") if isinstance(owner, dict) else owner
                if isinstance(owner, str)
                else None
            )
            if not owner_urn or not str(owner_urn).startswith("urn:li:"):
                continue
            if owner_urn in seen:
                continue
            seen.add(owner_urn)
            name = owner_urn.rsplit(":", 1)[-1]
            if isinstance(owner, dict):
                name = (
                    owner.get("username")
                    or owner.get("name")
                    or owner.get("displayName")
                    or name
                )
            otype = candidate.get("type") or candidate.get("ownershipType") or "unknown"
            if isinstance(otype, dict):
                otype = otype.get("urn") or otype.get("type") or "unknown"
            results.append((owner_urn, str(name), str(otype)))
    return results


def domain_of(urn: str, payload: Any) -> str | None:
    for node in walk(payload):
        if node.get("urn") != urn:
            continue
        domain = node.get("domain")
        if isinstance(domain, str) and domain:
            return domain.rsplit(":", 1)[-1].strip("()")
        if isinstance(domain, dict):
            inner = domain.get("urn") or domain.get("name")
            if isinstance(inner, str):
                return inner.rsplit(":", 1)[-1].strip("()")
    return None


def schema_field_names(payload: Any) -> list[str]:
    """Column names from a ``list_schema_fields`` (or entity) payload."""
    names: list[str] = []
    seen: set[str] = set()
    for node in walk(payload):
        raw = node.get("fieldPath") or node.get("path")
        if raw is None and node.get("type") is not None and "name" in node:
            raw = node.get("name")
        if not isinstance(raw, str) or not raw:
            continue
        # DataHub v2 field paths look like [version=2.0].[type=struct].[type=string].col
        leaf = raw.split(".")[-1].strip("[]")
        leaf = leaf.split("=")[-1] if leaf.startswith("type=") else leaf
        key = leaf.lower()
        if key and key not in seen:
            seen.add(key)
            names.append(leaf)
    return names


def sql_statements(payload: Any) -> list[tuple[str, str | None]]:
    """SQL strings from a queries payload, as ``(sql, source ref)``."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for node in walk(payload):
        for key in ("statement", "sql", "query", "queryText", "rawQuery", "text"):
            value = node.get(key)
            if isinstance(value, dict):
                value = value.get("value") or value.get("statement")
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if len(candidate) < 12 or not re.search(
                r"\b(select|insert|update|merge|create|with)\b", candidate, re.I
            ):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            ref = node.get("urn") or node.get("id") or node.get("name")
            out.append((candidate, ref if isinstance(ref, str) else None))
    return out


# -- SQL analysis ------------------------------------------------------------


def alias_map(tree: exp.Expression) -> dict[str, str]:
    """alias (or bare table name) -> fully qualified table name, lowercased."""
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        full = ".".join(
            part.name.lower()
            for part in (table.args.get("catalog"), table.args.get("db"), table.this)
            if part is not None and getattr(part, "name", None)
        )
        if not full:
            continue
        leaf = full.rsplit(".", 1)[-1]
        mapping[leaf] = full
        alias = table.alias
        if alias:
            mapping[alias.lower()] = full
    return mapping


def matches_table(qualified: str | None, table: str) -> bool:
    """Does a resolved table name refer to ``table`` (leaf-insensitive)?"""
    if qualified is None:
        return False
    return qualified == table or qualified.rsplit(".", 1)[-1] == table.rsplit(".", 1)[-1]


def find_column_references(
    sql: str, column: str, table: str, dialect: str | None = None
) -> list[tuple[str, str]]:
    """Find references to ``table.column`` in ``sql``.

    Returns a list of ``(kind, snippet)`` where kind is one of:

    * ``column_ref``   -- the column is named explicitly
    * ``star_select``  -- ``SELECT *`` over the table, so the column flows
                          through implicitly and the downstream schema shifts
    * ``unparsed_sql`` -- sqlglot could not parse it; a human must look

    An empty list means "parsed cleanly, no reference found".
    """
    column_lc = column.lower()
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (ParseError, TokenError):
        for fallback in ("postgres", "snowflake", "bigquery", "databricks", "hive"):
            if fallback == dialect:
                continue
            try:
                tree = sqlglot.parse_one(sql, read=fallback)
                break
            except (ParseError, TokenError):
                continue
        else:
            if re.search(rf"\b{re.escape(column_lc)}\b", sql, re.IGNORECASE):
                return [("unparsed_sql", _snippet(sql, column_lc))]
            return [("unparsed_sql", sql.strip()[:200])]

    aliases = alias_map(tree)
    table_present = any(matches_table(v, table) for v in aliases.values())
    hits: list[tuple[str, str]] = []

    for col in tree.find_all(exp.Column):
        if col.name.lower() != column_lc:
            continue
        qualifier = col.table.lower() if col.table else None
        if qualifier is None:
            # Unqualified: attributable to our table only if it is in scope.
            if table_present:
                hits.append(("column_ref", col.sql()))
            continue
        if matches_table(aliases.get(qualifier, qualifier), table):
            hits.append(("column_ref", col.sql()))

    if table_present:
        for star in tree.find_all(exp.Star):
            parent = star.parent
            # A bare "SELECT *" or "t.*" pulls the column through implicitly.
            if isinstance(parent, (exp.Select, exp.Column)):
                hits.append(("star_select", "SELECT *"))
                break

    # De-duplicate while keeping the strongest signal first.
    ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind in ("column_ref", "star_select", "unparsed_sql"):
        for hit in hits:
            if hit[0] == kind and hit not in seen:
                seen.add(hit)
                ordered.append(hit)
    return ordered


def _snippet(sql: str, needle: str) -> str:
    for line in sql.splitlines():
        if needle in line.lower():
            return line.strip()[:200]
    return sql.strip()[:200]
