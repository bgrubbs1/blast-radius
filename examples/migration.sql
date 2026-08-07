-- The change analysed in examples/impact-report.md.
--
--   blast-radius plan --change examples/migration.sql --depth 2 --out out/
--
-- Two downstream consumers provably break, three cannot be cleared, one is safe.
ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount;
