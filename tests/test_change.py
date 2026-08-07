from __future__ import annotations

import pytest

from blastradius.change import parse_change, parse_dbt_diff, parse_ddl
from blastradius.models import Operation


def test_drop_column_qualified_and_quoted():
    change = parse_ddl('ALTER TABLE "Analytics".public.FCT_ORDERS DROP COLUMN "Discount_Amount";')
    assert change.operation is Operation.DROP_COLUMN
    assert change.table == "analytics.public.fct_orders"
    assert change.columns == ["discount_amount"]


def test_drop_multiple_columns():
    change = parse_ddl("ALTER TABLE orders DROP COLUMN a, COLUMN b")
    assert change.columns == ["a", "b"]


def test_drop_column_if_exists():
    change = parse_ddl("alter table orders drop column if exists legacy_flag")
    assert change.columns == ["legacy_flag"]


def test_rename_column():
    change = parse_ddl("ALTER TABLE db.orders RENAME COLUMN amt TO amount_usd")
    assert change.operation is Operation.RENAME_COLUMN
    assert (change.column, change.new_name) == ("amt", "amount_usd")


def test_retype_column():
    change = parse_ddl("ALTER TABLE orders ALTER COLUMN qty TYPE BIGINT")
    assert change.operation is Operation.RETYPE_COLUMN
    assert change.new_type == "bigint"


def test_drop_table():
    change = parse_ddl("DROP TABLE IF EXISTS analytics.legacy_orders")
    assert change.operation is Operation.DROP_TABLE
    assert change.table == "analytics.legacy_orders"


def test_rename_wins_over_drop_when_both_words_appear():
    # "DROP" appears nowhere, but the regex order matters for RENAME vs DROP:
    # a rename must never be read as a drop of the old column.
    change = parse_ddl("ALTER TABLE t RENAME COLUMN old_col TO new_col")
    assert change.operation is Operation.RENAME_COLUMN


def test_unsupported_statement_rejected():
    with pytest.raises(ValueError, match="unsupported change"):
        parse_ddl("ALTER TABLE orders ADD COLUMN extra INT")


def test_garbage_is_rejected_before_analysis():
    with pytest.raises(ValueError):
        parse_ddl("this is not sql at all")


def test_empty_change_rejected():
    with pytest.raises(ValueError, match="empty change"):
        parse_ddl("   ")


DBT_DROP_DIFF = """--- a/models/marts/fct_orders.sql
+++ b/models/marts/fct_orders.sql
@@ -3,7 +3,6 @@ select
     order_id,
     customer_id,
-    discount_amount,
     net_amount
 from {{ ref('stg_orders') }}
"""

DBT_RENAME_DIFF = """--- a/models/marts/fct_orders.sql
+++ b/models/marts/fct_orders.sql
@@ -3,7 +3,7 @@ select
     order_id,
-    amt,
+    amount_usd,
     net_amount
"""


def test_dbt_diff_detects_dropped_column():
    change = parse_dbt_diff(DBT_DROP_DIFF)
    assert change.operation is Operation.DROP_COLUMN
    assert change.table == "fct_orders"
    assert change.columns == ["discount_amount"]


def test_dbt_diff_detects_rename():
    change = parse_dbt_diff(DBT_RENAME_DIFF)
    assert change.operation is Operation.RENAME_COLUMN
    assert (change.column, change.new_name) == ("amt", "amount_usd")


def test_dbt_diff_ignores_sql_keywords():
    diff = """--- a/models/x.sql
+++ b/models/x.sql
-select
-from
+select
+from
"""
    with pytest.raises(ValueError, match="no removed columns"):
        parse_dbt_diff(diff)


def test_parse_change_dispatches_on_shape():
    assert parse_change(DBT_DROP_DIFF).table == "fct_orders"
    assert parse_change("DROP TABLE t").operation is Operation.DROP_TABLE


def test_table_override_applies():
    change = parse_change("ALTER TABLE t DROP COLUMN c", table="warehouse.public.t")
    assert change.table == "warehouse.public.t"
