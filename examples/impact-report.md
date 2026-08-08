# Blast radius: DROP COLUMN analytics.public.fct_orders.discount_amount

**2 assets will break.** Do not ship this change as-is.

- **Change**: `DROP COLUMN analytics.public.fct_orders.discount_amount`
- **Dataset**: `urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)`
- **Downstream assets examined**: 5 (2 breaking, 2 at risk, 1 safe)
- **Generated**: 2026-08-08 19:50 UTC by [blast-radius](https://github.com/bgrubbs1/blast-radius)

## Breaking

| asset | type | hops | owner | why |
| --- | --- | --- | --- | --- |
| `dim_customer_ltv` | dataset (snowflake) | 1 | maya.iyer | a query on this asset references analytics.public.fct_orders.discount_amount — `o.discount_amount` |
| `mart_orders_flat` | dataset (snowflake) | 1 | sam.okafor | a query on this asset references analytics.public.fct_orders.discount_amount — `discount_amount` |

## At risk (unproven — needs a human)

| asset | type | hops | owner | why |
| --- | --- | --- | --- | --- |
| `Finance Exec Overview` | dashboard | 1 | (unowned) | dashboard consuming the change -- its definition lives outside DataHub's SQL index, so a human must confirm |
| `rpt_daily_revenue` | dataset (snowflake) | 1 | finance-analytics | exposes a column named 'discount_amount' of its own -- likely propagated |

## Who to notify

- **maya.iyer** — BREAKING: `dim_customer_ltv`
- **sam.okafor** — BREAKING: `mart_orders_flat`
- **(unowned)** — AT RISK: `Finance Exec Overview`
- **finance-analytics** — AT RISK: `rpt_daily_revenue`

## Rollout order

1. **Announce.** Append a warning to `analytics.public.fct_orders.discount_amount` in DataHub and save a linked impact analysis (`blast-radius plan ... --write-back` does this for you).
2. **Migrate the 2 breaking consumers:** `dim_customer_ltv`, `mart_orders_flat`. Patches for the queries we could rewrite are in `patches/`.
3. **Get eyes on 2 unproven assets.** These are downstream but we could not prove a reference — ask the owners listed below to confirm before you proceed.
4. **Re-run and require zero breaking.** `blast-radius plan --change <ddl> --fail-on breaking` in CI; when it exits 0, nothing indexed in DataHub still depends on the old shape.
5. **Contract.** Only now apply the DROP: `ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount`

## Generated patches

3 patches — 1 mechanical, 2 needing a decision. Files are in `patches/`.

#### dim_customer_ltv: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*mechanical* — removed 1 projection of discount_amount

```diff
--- a/dim_customer_ltv.0.sql
+++ b/dim_customer_ltv.0.sql
@@ -1,6 +1,7 @@
-SELECT o.customer_id,
-       COUNT(*) AS orders,
-       SUM(o.discount_amount) AS total_discount,
-       SUM(o.net_amount) AS ltv
-FROM analytics.public.fct_orders o
-GROUP BY o.customer_id
+SELECT
+  o.customer_id,
+  COUNT(*) AS orders,
+  SUM(o.net_amount) AS ltv
+FROM analytics.public.fct_orders AS o
+GROUP BY
+  o.customer_id
```

#### mart_orders_flat: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*needs review* — 1 reference left in place: the column is used in a filter/join/grouping, so removing it would change results

```diff
--- a/mart_orders_flat.0.sql
+++ b/mart_orders_flat.0.sql
@@ -1,3 +1,8 @@
-SELECT order_id, customer_id, net_amount AS total
+-- TODO(blast-radius): discount_amount is being dropped but still drives this query's logic -- decide the replacement before merging
+SELECT
+  order_id,
+  customer_id,
+  net_amount AS total
 FROM analytics.public.fct_orders
-WHERE discount_amount > 0
+WHERE
+  discount_amount > 0
```

#### rpt_daily_revenue: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*needs review* — this query uses SELECT * -- its output schema changes even though the SQL still runs

```diff
--- a/rpt_daily_revenue.1.sql
+++ b/rpt_daily_revenue.1.sql
@@ -1 +1,5 @@
-SELECT * FROM analytics.public.fct_orders WHERE order_ts >= CURRENT_DATE - 30
+SELECT
+  *
+FROM analytics.public.fct_orders
+WHERE
+  order_ts >= CURRENT_DATE - 30
```

## Cleared

| asset | type | hops | owner | why |
| --- | --- | --- | --- | --- |
| `stg_orders_audit` | dataset (snowflake) | 1 | maya.iyer | 1 indexed query parsed; none reference discount_amount |

## How this was computed

Every verdict above comes from DataHub metadata read over MCP (11 tool calls). A verdict of *breaking* means a query indexed in DataHub names the changed column; *at risk* means the asset is downstream but the reference could not be proven either way.

<details><summary>MCP tool calls</summary>

```
[replay] search(query='/q analytics+public+fct+orders', num_results=25, filter='entity_type = dataset')
[replay] get_lineage(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)', upstream=False, max_hops=2, column=None)
[replay] get_entities(urns=['urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)', 'urn:li:dashboard:(looker,finance_exec_overview)'])
[replay] list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)', limit=200)
[replay] get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)', count=25)
[replay] list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)', limit=200)
[replay] get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)', count=25)
[replay] list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)', limit=200)
[replay] get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)', count=25)
[replay] list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)', limit=200)
[replay] get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)', count=25)
```
</details>
