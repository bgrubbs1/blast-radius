-- TODO(blast-radius): discount_amount is being dropped but still drives this query's logic -- decide the replacement before merging
SELECT
  order_id,
  customer_id,
  net_amount AS total
FROM analytics.public.fct_orders
WHERE
  discount_amount > 0