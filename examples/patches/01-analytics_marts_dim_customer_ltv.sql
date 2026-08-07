SELECT
  o.customer_id,
  COUNT(*) AS orders,
  SUM(o.net_amount) AS ltv
FROM analytics.public.fct_orders AS o
GROUP BY
  o.customer_id