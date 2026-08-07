"""Turn findings into code you can actually merge.

Renames are mechanical and we do them properly (AST rewrite, every reference,
including qualified ones). Drops and type changes are only *sometimes*
mechanical: removing a column from a SELECT list is safe, but a column used in
a WHERE, JOIN or GROUP BY encodes intent we cannot invent -- those come back
marked ``review`` with a TODO at the top, never silently "fixed".

Confidence is part of the output for that reason:

``mechanical``  the rewrite preserves semantics; review is a formality.
``review``      we changed what we could and flagged what needs a decision.
"""

from __future__ import annotations

import difflib

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from .extract import alias_map, matches_table
from .models import ImpactedAsset, ImpactReport, Operation, Patch, SchemaChange, Verdict

TODO = "-- TODO(blast-radius): "

# Clauses where a column carries intent a rewrite cannot preserve.
_INTENT_CLAUSES = (
    exp.Where,
    exp.Join,
    exp.Group,
    exp.Order,
    exp.Having,
    exp.Qualify,
    exp.Window,
)


def _is_target(node: exp.Column, change: SchemaChange, aliases: dict[str, str]) -> bool:
    if node.name.lower() != (change.column or "").lower():
        return False
    qualifier = node.table.lower() if node.table else None
    if qualifier is None:
        return any(matches_table(v, change.table) for v in aliases.values())
    return matches_table(aliases.get(qualifier, qualifier), change.table)


def _in_intent_clause(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, _INTENT_CLAUSES):
            return True
        parent = parent.parent
    return False


def rewrite_sql(
    sql: str, change: SchemaChange, dialect: str | None = None
) -> tuple[str, str, str]:
    """Rewrite one statement for ``change``.

    Returns ``(updated_sql, confidence, note)``. On a parse failure the original
    SQL comes back untouched with a TODO header -- we never emit a guess.
    """
    dialect = dialect or change.dialect
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (ParseError, TokenError) as exc:
        return (
            f"{TODO}could not parse this statement ({exc}); migrate by hand\n{sql}",
            "review",
            "unparseable SQL",
        )

    aliases = alias_map(tree)
    notes: list[str] = []

    if change.operation is Operation.RENAME_COLUMN and change.new_name:
        renamed = 0

        def rename(node: exp.Expression) -> exp.Expression:
            nonlocal renamed
            if isinstance(node, exp.Column) and _is_target(node, change, aliases):
                renamed += 1
                new = node.copy()
                new.set("this", exp.to_identifier(change.new_name))
                # Keep the old name visible downstream unless it is already aliased.
                if isinstance(node.parent, exp.Select):
                    return exp.alias_(new, node.name)
                return new
            return node

        updated = tree.transform(rename)
        if not renamed:
            return sql, "mechanical", "no reference to rename"
        notes.append(
            f"renamed {renamed} reference{'s' if renamed != 1 else ''}; "
            f"kept '{change.column}' as an output alias so consumers of this "
            "asset keep working"
        )
        return updated.sql(dialect=dialect, pretty=True), "mechanical", "; ".join(notes)

    if change.operation is Operation.RETYPE_COLUMN and change.new_type:
        wrapped = 0

        def recast(node: exp.Expression) -> exp.Expression:
            nonlocal wrapped
            if isinstance(node, exp.Column) and _is_target(node, change, aliases):
                wrapped += 1
                return exp.cast(node.copy(), change.new_type)
            return node

        updated = tree.transform(recast)
        if not wrapped:
            return sql, "mechanical", "no reference to re-cast"
        header = (
            f"{TODO}{change.column} becomes {change.new_type}; the casts below keep "
            "this query compiling -- confirm precision/overflow expectations\n"
        )
        return (
            header + updated.sql(dialect=dialect, pretty=True),
            "review",
            f"wrapped {wrapped} reference{'s' if wrapped != 1 else ''} in an explicit CAST",
        )

    if change.operation is Operation.DROP_COLUMN:
        removed = 0
        blocked = 0

        for column in list(tree.find_all(exp.Column)):
            if not _is_target(column, change, aliases):
                continue
            if _in_intent_clause(column):
                blocked += 1
                continue
            projection = column
            while projection.parent is not None and not isinstance(
                projection.parent, exp.Select
            ):
                projection = projection.parent
            if isinstance(projection.parent, exp.Select) and projection in (
                projection.parent.expressions or []
            ):
                # Only drop a projection whose *only* input is the dead column.
                inputs = {c.name.lower() for c in projection.find_all(exp.Column)}
                if inputs == {(change.column or "").lower()}:
                    projection.pop()
                    removed += 1
                    continue
            blocked += 1

        # Only a projected star matters: "SELECT *" or "t.*" carries the column
        # through. COUNT(*) does not, and must not be mistaken for it.
        star = any(
            isinstance(node.parent, (exp.Select, exp.Column))
            for node in tree.find_all(exp.Star)
        )
        if removed:
            notes.append(
                f"removed {removed} projection{'s' if removed != 1 else ''} of "
                f"{change.column}"
            )
        if star:
            notes.append(
                "this query uses SELECT * -- its output schema changes even though "
                "the SQL still runs"
            )
        if blocked:
            notes.append(
                f"{blocked} reference{'s' if blocked != 1 else ''} left in place: the "
                "column is used in a filter/join/grouping, so removing it would change "
                "results"
            )
            header = (
                f"{TODO}{change.column} is being dropped but still drives this query's "
                "logic -- decide the replacement before merging\n"
            )
            return (
                header + tree.sql(dialect=dialect, pretty=True),
                "review",
                "; ".join(notes),
            )
        if not removed and not star:
            return sql, "mechanical", "no reference to remove"
        return (
            tree.sql(dialect=dialect, pretty=True),
            "mechanical" if removed and not star else "review",
            "; ".join(notes) or "no change required",
        )

    # DROP TABLE: there is no rewrite, only a decision.
    return (
        f"{TODO}{change.table} is being dropped; repoint or retire this query\n{sql}",
        "review",
        "dropping a table has no mechanical rewrite",
    )


def _diff(original: str, updated: str, label: str) -> str:
    # Both sides must end in a newline or difflib runs the last "-" line into
    # the first "+" line, which makes the diff unreadable (and unappliable).
    if not original.endswith("\n"):
        original += "\n"
    if not updated.endswith("\n"):
        updated += "\n"
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=3,
        )
    )


def patches_for(
    report: ImpactReport, max_per_asset: int = 3, dialect: str | None = None
) -> list[Patch]:
    """Generate patches for every asset with quotable SQL evidence."""
    out: list[Patch] = []
    for asset in report.assets:
        if asset.verdict is Verdict.SAFE:
            continue
        out.extend(_patches_for_asset(asset, report.change, max_per_asset, dialect))
    return out


def _patches_for_asset(
    asset: ImpactedAsset, change: SchemaChange, limit: int, dialect: str | None
) -> list[Patch]:
    made: list[Patch] = []
    seen_statements: set[str] = set()
    for index, evidence in enumerate(asset.evidence):
        if len(made) >= limit:
            break
        if evidence.kind not in {"query", "unparsed_sql"} or not evidence.statement:
            continue
        original = evidence.statement.strip()
        if original in seen_statements:
            continue
        seen_statements.add(original)
        updated, confidence, note = rewrite_sql(original, change, dialect=dialect)
        if updated.strip() == original.strip():
            continue
        label = f"{asset.name}.{index}.sql"
        made.append(
            Patch(
                target=asset.urn,
                title=f"{asset.name}: adapt to {change.describe()}",
                language="sql",
                original=original,
                updated=updated,
                diff=_diff(original, updated, label),
                confidence=confidence,
                note=note,
            )
        )
    return made
