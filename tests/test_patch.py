from __future__ import annotations

from blastradius.change import parse_ddl
from blastradius.patch import TODO, rewrite_sql

TABLE = "analytics.public.fct_orders"


def test_rename_rewrites_every_reference_and_keeps_an_alias():
    change = parse_ddl(f"ALTER TABLE {TABLE} RENAME COLUMN discount_amount TO discount_usd")
    sql = (
        "SELECT o.discount_amount FROM analytics.public.fct_orders o "
        "WHERE o.discount_amount > 0"
    )
    updated, confidence, note = rewrite_sql(sql, change)
    assert confidence == "mechanical"
    assert "discount_usd" in updated
    # The projection keeps the old name so this asset's consumers do not break.
    assert "AS discount_amount" in updated
    assert "renamed 2 references" in note
    # No stale reference survives in the WHERE clause.
    assert "o.discount_amount >" not in updated


def test_drop_removes_a_projection_cleanly():
    change = parse_ddl(f"ALTER TABLE {TABLE} DROP COLUMN discount_amount")
    sql = "SELECT order_id, discount_amount, net_amount FROM analytics.public.fct_orders"
    updated, confidence, note = rewrite_sql(sql, change)
    assert confidence == "mechanical"
    assert "discount_amount" not in updated
    assert "order_id" in updated and "net_amount" in updated
    assert "removed 1 projection" in note


def test_drop_used_in_where_is_flagged_not_silently_fixed():
    change = parse_ddl(f"ALTER TABLE {TABLE} DROP COLUMN discount_amount")
    sql = "SELECT order_id FROM analytics.public.fct_orders WHERE discount_amount > 0"
    updated, confidence, note = rewrite_sql(sql, change)
    assert confidence == "review"
    assert updated.startswith(TODO)
    # The filter is preserved: dropping it would change the result set.
    assert "discount_amount > 0" in updated
    assert "filter/join/grouping" in note


def test_drop_of_a_composite_projection_is_not_removed():
    change = parse_ddl(f"ALTER TABLE {TABLE} DROP COLUMN discount_amount")
    sql = (
        "SELECT gross_amount - discount_amount AS net FROM analytics.public.fct_orders"
    )
    updated, confidence, _ = rewrite_sql(sql, change)
    assert confidence == "review"
    assert "gross_amount" in updated


def test_retype_wraps_references_in_a_cast():
    change = parse_ddl(f"ALTER TABLE {TABLE} ALTER COLUMN qty TYPE BIGINT")
    sql = "SELECT qty FROM analytics.public.fct_orders"
    updated, confidence, note = rewrite_sql(sql, change)
    assert confidence == "review"
    assert "CAST(" in updated.upper()
    assert "wrapped 1 reference" in note


def test_drop_table_has_no_mechanical_rewrite():
    change = parse_ddl(f"DROP TABLE {TABLE}")
    updated, confidence, _ = rewrite_sql("SELECT * FROM analytics.public.fct_orders", change)
    assert confidence == "review"
    assert updated.startswith(TODO)


def test_unparseable_sql_comes_back_untouched_with_a_todo():
    change = parse_ddl(f"ALTER TABLE {TABLE} DROP COLUMN discount_amount")
    updated, confidence, note = rewrite_sql("SELCT discount_amount FRM ???", change)
    assert confidence == "review"
    assert updated.startswith(TODO)
    assert note == "unparseable SQL"


def test_query_on_an_unrelated_table_is_left_alone():
    change = parse_ddl(f"ALTER TABLE {TABLE} DROP COLUMN discount_amount")
    sql = "SELECT discount_amount FROM marketing.promo_codes"
    updated, _, note = rewrite_sql(sql, change)
    assert updated == sql
    assert note == "no reference to remove"
