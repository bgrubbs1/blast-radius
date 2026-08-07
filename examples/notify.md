# Notify

## maya.iyer

- `dim_customer_ltv` (breaking) — a query on this asset references analytics.public.fct_orders.discount_amount

## sam.okafor

- `mart_orders_flat` (breaking) — a query on this asset references analytics.public.fct_orders.discount_amount

## (unowned)

- `Finance Exec Overview` (at_risk) — dashboard consuming the change -- its definition lives outside DataHub's SQL index, so a human must confirm

## finance-analytics

- `rpt_daily_revenue` (at_risk) — exposes a column named 'discount_amount' of its own -- likely propagated
