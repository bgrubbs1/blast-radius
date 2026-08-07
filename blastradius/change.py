"""Parse a proposed change into a :class:`SchemaChange`.

Two input shapes are supported:

* **DDL** -- ``ALTER TABLE ... DROP COLUMN``, ``RENAME COLUMN``, a type change,
  or ``DROP TABLE``. Parsed with regexes and then validated by handing the
  statement to sqlglot, which catches typos without making us depend on the
  exact shape of sqlglot's ``Alter`` AST (it moves between versions).
* **dbt model diff** -- a unified diff of a ``.sql`` model. Columns that
  disappear from the SELECT list are treated as dropped, which is the change
  that actually breaks downstream consumers in a dbt project.
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot.errors import ParseError

from .models import Operation, SchemaChange

_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_QUALIFIED = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"

_DROP_COLUMN_RE = re.compile(
    rf"""\bALTER\s+TABLE\s+(?P<table>{_QUALIFIED})\s+
        DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(?P<cols>{_IDENT}(?:\s*,\s*(?:COLUMN\s+)?{_IDENT})*)""",
    re.IGNORECASE | re.VERBOSE,
)
_RENAME_COLUMN_RE = re.compile(
    rf"""\bALTER\s+TABLE\s+(?P<table>{_QUALIFIED})\s+
        RENAME\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+TO\s+(?P<new>{_IDENT})""",
    re.IGNORECASE | re.VERBOSE,
)
_RETYPE_RE = re.compile(
    rf"""\bALTER\s+TABLE\s+(?P<table>{_QUALIFIED})\s+
        (?:ALTER|MODIFY)\s+(?:COLUMN\s+)?(?P<col>{_IDENT})\s+
        (?:SET\s+DATA\s+TYPE\s+|TYPE\s+)?(?P<type>[A-Za-z][\w\s().,]*?)\s*;?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_DROP_TABLE_RE = re.compile(
    rf"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<table>{_QUALIFIED})",
    re.IGNORECASE,
)


def unquote(identifier: str) -> str:
    """Strip quoting and surrounding whitespace from one identifier."""
    ident = identifier.strip()
    if len(ident) >= 2 and ident[0] in "\"`[" and ident[-1] in "\"`]":
        ident = ident[1:-1]
    return ident.strip()


def normalise_table(raw: str) -> str:
    """``"my_db" . Public.FCT_ORDERS`` -> ``my_db.public.fct_orders``."""
    parts = [unquote(p) for p in re.split(r"\s*\.\s*", raw.strip())]
    return ".".join(p.lower() for p in parts if p)


def table_leaf(table: str) -> str:
    """Last path segment of a (possibly qualified) table name."""
    return table.rsplit(".", 1)[-1]


def parse_ddl(sql: str, dialect: str | None = None) -> SchemaChange:
    """Parse a single DDL statement.

    Raises:
        ValueError: if the statement is not one of the four supported changes,
            or if it is not valid SQL in ``dialect``.
    """
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise ValueError("empty change: nothing to analyse")

    _validate_sql(statement, dialect)

    if match := _RENAME_COLUMN_RE.search(statement):
        return SchemaChange(
            operation=Operation.RENAME_COLUMN,
            table=normalise_table(match.group("table")),
            columns=[unquote(match.group("col")).lower()],
            new_name=unquote(match.group("new")).lower(),
            source=statement,
            dialect=dialect,
        )

    if match := _DROP_COLUMN_RE.search(statement):
        raw_cols = re.split(r"\s*,\s*", match.group("cols"))
        cols = [
            unquote(re.sub(r"^COLUMN\s+", "", c, flags=re.IGNORECASE)).lower()
            for c in raw_cols
        ]
        return SchemaChange(
            operation=Operation.DROP_COLUMN,
            table=normalise_table(match.group("table")),
            columns=[c for c in cols if c],
            source=statement,
            dialect=dialect,
        )

    if match := _RETYPE_RE.search(statement):
        return SchemaChange(
            operation=Operation.RETYPE_COLUMN,
            table=normalise_table(match.group("table")),
            columns=[unquote(match.group("col")).lower()],
            new_type=" ".join(match.group("type").split()).lower(),
            source=statement,
            dialect=dialect,
        )

    if match := _DROP_TABLE_RE.search(statement):
        return SchemaChange(
            operation=Operation.DROP_TABLE,
            table=normalise_table(match.group("table")),
            source=statement,
            dialect=dialect,
        )

    raise ValueError(
        "unsupported change. Supported: ALTER TABLE ... DROP COLUMN / "
        "RENAME COLUMN ... TO ... / ALTER COLUMN ... TYPE ..., and DROP TABLE"
    )


def _validate_sql(statement: str, dialect: str | None) -> None:
    """Reject input that is not parseable SQL.

    sqlglot is the referee, but a few real dialect spellings (Databricks'
    ``ALTER COLUMN c SET DATA TYPE``, for one) parse only in their own dialect,
    so a failure in the *default* dialect is not fatal on its own.
    """
    try:
        sqlglot.parse_one(statement, read=dialect)
    except ParseError as exc:
        if dialect:
            raise ValueError(f"not valid {dialect} SQL: {exc}") from exc
        for fallback in ("postgres", "snowflake", "databricks", "bigquery"):
            try:
                sqlglot.parse_one(statement, read=fallback)
                return
            except ParseError:
                continue
        raise ValueError(f"not valid SQL: {exc}") from exc


_DIFF_SELECT_ALIAS_RE = re.compile(
    rf"""^-\s*(?:.*\bAS\s+(?P<alias>{_IDENT})|(?P<bare>{_IDENT}))\s*,?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_SQL_KEYWORDS = {
    "select", "from", "where", "group", "order", "by", "having", "join", "left",
    "right", "inner", "outer", "on", "with", "as", "and", "or", "case", "when",
    "then", "else", "end", "union", "all", "distinct", "limit", "qualify",
}


def parse_dbt_diff(diff_text: str, model_name: str | None = None) -> SchemaChange:
    """Read a unified diff of a dbt model and treat removed columns as drops.

    Args:
        diff_text: unified diff (``git diff`` output is fine).
        model_name: overrides the model name inferred from the ``---``/``+++``
            header.

    Raises:
        ValueError: if no removed column can be identified.
    """
    name = model_name
    if not name:
        header = re.search(r"^[-+]{3}\s+\S*?([\w./-]+)\.sql", diff_text, re.MULTILINE)
        if header:
            name = Path(header.group(1)).stem
    if not name:
        raise ValueError("could not infer the dbt model name; pass --table")

    removed: list[str] = []
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if not line.startswith(("-", "+")):
            continue
        match = _DIFF_SELECT_ALIAS_RE.match("-" + line[1:])
        if not match:
            continue
        token = unquote(match.group("alias") or match.group("bare") or "").lower()
        if not token or token in _SQL_KEYWORDS:
            continue
        (removed if line.startswith("-") else added).append(token)

    gone = [c for c in removed if c not in added]
    if not gone:
        raise ValueError(
            "no removed columns found in the diff -- if this is a rename, pass "
            "the equivalent DDL with --change instead"
        )

    renamed_to = [c for c in added if c not in removed]
    if len(gone) == 1 and len(renamed_to) == 1:
        return SchemaChange(
            operation=Operation.RENAME_COLUMN,
            table=normalise_table(name),
            columns=[gone[0]],
            new_name=renamed_to[0],
            source=diff_text.strip(),
        )

    return SchemaChange(
        operation=Operation.DROP_COLUMN,
        table=normalise_table(name),
        columns=gone,
        source=diff_text.strip(),
    )


def parse_change(
    text: str, dialect: str | None = None, table: str | None = None
) -> SchemaChange:
    """Parse either DDL or a dbt diff -- whichever the text looks like."""
    stripped = text.strip()
    looks_like_diff = stripped.startswith(("---", "diff --git", "@@")) or (
        "\n-" in stripped and "\n+" in stripped
    )
    if looks_like_diff:
        return parse_dbt_diff(stripped, model_name=table)
    change = parse_ddl(stripped, dialect=dialect)
    if table:
        change.table = normalise_table(table)
    return change
