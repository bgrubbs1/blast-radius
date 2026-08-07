SELECT
  *
FROM analytics.public.fct_orders
WHERE
  order_ts >= CURRENT_DATE - 30