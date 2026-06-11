-- Database views for the bills API
-- Run against utility_bills database to create/update views
-- These are used by the /api/bills/monthly-totals and /api/bills/trends endpoints

CREATE OR REPLACE VIEW v_monthly_totals AS
SELECT
    vendor_category,
    YEAR(invoice_date) AS year,
    MONTH(invoice_date) AS month,
    SUM(total_amount) AS total_amount,
    COUNT(*) AS bill_count,
    SUM(energy_kwh) AS energy_kwh,
    SUM(gas_m3) AS gas_m3,
    SUM(water_m3) AS water_m3,
    SUM(other_units) AS other_units
FROM parsed_bills
WHERE status = 'success'
  AND invoice_date IS NOT NULL
GROUP BY vendor_category, YEAR(invoice_date), MONTH(invoice_date);

CREATE OR REPLACE VIEW v_yoy_comparison AS
SELECT
    c.vendor_category,
    c.year AS current_year,
    p.year AS previous_year,
    c.month,
    c.total_amount AS current_amount,
    p.total_amount AS previous_amount,
    ROUND(c.total_amount - COALESCE(p.total_amount, 0), 2) AS diff,
    CASE
        WHEN p.total_amount IS NOT NULL AND p.total_amount > 0
        THEN ROUND((c.total_amount - p.total_amount) / p.total_amount * 100, 1)
        ELSE NULL
    END AS pct_change
FROM v_monthly_totals c
LEFT JOIN v_monthly_totals p
    ON c.vendor_category = p.vendor_category
   AND c.month = p.month
   AND c.year = p.year + 1;
