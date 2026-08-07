# Blast radius: DROP COLUMN analytics.public.fct_orders.discount_amount

**2 assets will break.** Do not ship this change as-is.

- **Change**: `DROP COLUMN analytics.public.fct_orders.discount_amount`
- **Dataset**: `urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)`
- **Downstream assets examined**: 6 (2 breaking, 3 at risk, 1 safe)
- **Generated**: 2026-08-07 13:15 UTC by [blast-radius](https://github.com/bgrubbs1/blast-radius)
- **In DataHub**: http://localhost:9002/dataset/urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)

## Breaking

| asset | type | hops | owner | why |
| --- | --- | --- | --- | --- |
| `analytics.marts.dim_customer_ltv` | dataset (snowflake) | 1 | maya.iyer | a query on this asset references analytics.public.fct_orders.discount_amount — `o.discount_amount` |
| `analytics.marts.mart_orders_flat` | dataset (snowflake) | 1 | sam.okafor | a query on this asset references analytics.public.fct_orders.discount_amount — `discount_amount` |

## At risk (unproven — needs a human)

| asset | type | hops | owner | why |
| --- | --- | --- | --- | --- |
| `analytics.marts.rpt_daily_revenue` | dataset (snowflake) | 1 | finance-analytics | exposes a column named 'discount_amount' of its own -- likely propagated |
| `Finance Exec Overview` | dashboard | 2 | finance-analytics | dashboard consuming the change -- its definition lives outside DataHub's SQL index, so a human must confirm |
| `order_propensity_v3` | mlFeatureTable (feast) | 2 | (unowned) | mlFeatureTable consuming the change -- its definition lives outside DataHub's SQL index, so a human must confirm |

## Who to notify

- **maya.iyer** — BREAKING: `analytics.marts.dim_customer_ltv`
- **sam.okafor** — BREAKING: `analytics.marts.mart_orders_flat`
- **finance-analytics** — AT RISK: `analytics.marts.rpt_daily_revenue`, `Finance Exec Overview`
- **(unowned)** — AT RISK: `order_propensity_v3`

## Rollout order

1. **Announce.** Mark `analytics.public.fct_orders.discount_amount` deprecated in DataHub so the catalog warns anyone who finds it next (`blast-radius plan ... --write-back` does this for you).
2. **Migrate the 2 breaking consumers:** `analytics.marts.dim_customer_ltv`, `analytics.marts.mart_orders_flat`. Patches for the queries we could rewrite are in `patches/`.
3. **Get eyes on 3 unproven assets.** These are downstream but we could not prove a reference — ask the owners listed below to confirm before you proceed.
4. **Re-run and require zero breaking.** `blast-radius plan --change <ddl> --fail-on breaking` in CI; when it exits 0, nothing indexed in DataHub still depends on the old shape.
5. **Contract.** Only now apply the DROP: `ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount`

## Generated patches

3 patches — 1 mechanical, 2 needing a decision. Files are in `patches/`.

#### analytics.marts.dim_customer_ltv: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*mechanical* — removed 1 projection of discount_amount

```diff
--- a/analytics.marts.dim_customer_ltv.0.sql
+++ b/analytics.marts.dim_customer_ltv.0.sql
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

#### analytics.marts.mart_orders_flat: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*needs review* — 1 reference left in place: the column is used in a filter/join/grouping, so removing it would change results

```diff
--- a/analytics.marts.mart_orders_flat.0.sql
+++ b/analytics.marts.mart_orders_flat.0.sql
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

#### analytics.marts.rpt_daily_revenue: adapt to DROP COLUMN analytics.public.fct_orders.discount_amount

*needs review* — this query uses SELECT * -- its output schema changes even though the SQL still runs

```diff
--- a/analytics.marts.rpt_daily_revenue.1.sql
+++ b/analytics.marts.rpt_daily_revenue.1.sql
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
| `analytics.staging.stg_orders_audit` | dataset (snowflake) | 1 | maya.iyer | 1 indexed query parsed; none reference discount_amount |

## How this was computed

Every verdict above comes from DataHub metadata read over MCP (11 tool calls). A verdict of *breaking* means a query indexed in DataHub names the changed column; *at risk* means the asset is downstream but the reference could not be proven either way.

<details><summary>MCP tool calls</summary>

```
search(query='analytics.public.fct_orders', limit=25, entity_types=['DATASET'])
get_lineage(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)', direction='DOWNSTREAM', max_hops=2)
get_entities(urns=['urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.fct_orders,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)', 'urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)', 'urn:li:dashboard:(looker,finance_exec_overview)', 'urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,order_propensity_v3)'])
list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)')
get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.dim_customer_ltv,PROD)', limit=25)
list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)')
get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.rpt_daily_revenue,PROD)', limit=25)
list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)')
get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.marts.mart_orders_flat,PROD)', limit=25)
list_schema_fields(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)')
get_dataset_queries(urn='urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.staging.stg_orders_audit,PROD)', limit=25)
```
</details>
