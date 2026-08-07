from __future__ import annotations

from blastradius.extract import (
    all_urns,
    dataset_name,
    display_name,
    domain_of,
    find_column_references,
    owners_of,
    platform_of,
    schema_field_names,
    sql_statements,
)

TABLE = "analytics.public.fct_orders"
URN = f"urn:li:dataset:(urn:li:dataPlatform:snowflake,{TABLE},PROD)"


def kinds(sql: str, column: str = "discount_amount", table: str = TABLE) -> list[str]:
    return [kind for kind, _ in find_column_references(sql, column, table)]


def test_qualified_reference_via_alias():
    sql = "SELECT o.discount_amount FROM analytics.public.fct_orders o"
    assert kinds(sql) == ["column_ref"]


def test_unqualified_reference_when_table_in_scope():
    sql = "SELECT discount_amount FROM fct_orders"
    assert kinds(sql) == ["column_ref"]


def test_unqualified_reference_ignored_for_other_tables():
    # Same column name, different table: not our problem.
    sql = "SELECT discount_amount FROM marketing.promo_codes"
    assert kinds(sql) == []


def test_count_star_is_not_select_star():
    sql = "SELECT COUNT(*) FROM analytics.public.fct_orders"
    assert kinds(sql) == []


def test_select_star_flags_schema_drift():
    sql = "SELECT * FROM analytics.public.fct_orders"
    assert kinds(sql) == ["star_select"]


def test_qualified_star_flags_schema_drift():
    sql = "SELECT o.* FROM analytics.public.fct_orders o JOIN dim_customer c ON c.id = o.customer_id"
    assert "star_select" in kinds(sql)


def test_reference_in_where_is_found():
    sql = "SELECT order_id FROM analytics.public.fct_orders WHERE discount_amount > 0"
    assert kinds(sql) == ["column_ref"]


def test_unparseable_sql_is_reported_not_swallowed():
    sql = "SELCT discount_amount FRM ???"
    assert kinds(sql) == ["unparsed_sql"]


def test_clean_query_returns_nothing():
    sql = "SELECT order_id, net_amount FROM analytics.public.fct_orders"
    assert kinds(sql) == []


def test_urn_helpers():
    assert dataset_name(URN) == TABLE
    assert platform_of(URN) == "snowflake"
    # The dataset URN is returned whole -- the dataPlatform URN nested inside it
    # is part of the identifier, not a separate asset to report on.
    assert all_urns({"a": [URN, {"b": "urn:li:corpuser:x"}]}) == [
        URN,
        "urn:li:corpuser:x",
    ]


def test_display_name_prefers_metadata():
    payload = {"entities": [{"urn": URN, "name": "Orders fact"}]}
    assert display_name(URN, payload) == "Orders fact"
    assert display_name(URN, None) == TABLE


def test_owners_and_domain_extraction():
    payload = {
        "entities": [
            {
                "urn": URN,
                "ownership": {
                    "owners": [
                        {
                            "owner": {"urn": "urn:li:corpuser:sam", "username": "sam"},
                            "type": "TECHNICAL_OWNER",
                        }
                    ]
                },
                "domain": {"urn": "urn:li:domain:(finance)"},
            }
        ]
    }
    assert owners_of(URN, payload) == [
        ("urn:li:corpuser:sam", "sam", "TECHNICAL_OWNER")
    ]
    assert domain_of(URN, payload) == "finance"


def test_schema_field_names_handles_v2_field_paths():
    payload = {
        "fields": [
            {"fieldPath": "[version=2.0].[type=struct].[type=string].order_id"},
            {"fieldPath": "discount_amount"},
        ]
    }
    assert schema_field_names(payload) == ["order_id", "discount_amount"]


def test_sql_statements_skips_non_sql_strings():
    payload = {
        "queries": [
            {"urn": "urn:li:query:1", "statement": "SELECT a FROM b"},
            {"urn": "urn:li:query:2", "statement": "hello"},
        ]
    }
    assert sql_statements(payload) == [("SELECT a FROM b", "urn:li:query:1")]
