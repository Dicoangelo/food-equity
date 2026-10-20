cat << 'EOF' > bq/02_equity_audit.sql
-- =====================================================================
-- 02_equity_audit.sql
-- Description: Compares demographics of matched recipient tract 
--              against the overall Seattle Metro baseline.
-- =====================================================================
WITH matched_tract AS (
  SELECT 
    tract_id,
    poverty_rate,
    no_vehicle_rate,
    assistance_rate
  FROM `a4i_food.recipients_final`
  WHERE recipient_id = 91 -- Congregations For The Homeless (Eastgate)
)
SELECT 
  'Matched Recipient Tract' AS cohort,
  ROUND(poverty_rate * 100, 1) AS poverty_pct,
  ROUND(no_vehicle_rate * 100, 1) AS no_vehicle_pct,
  ROUND(assistance_rate * 100, 1) AS assistance_pct
FROM matched_tract
UNION ALL
SELECT 
  'Seattle Metro Baseline' AS cohort,
  ROUND(AVG(poverty_rate) * 100, 1) AS poverty_pct,
  ROUND(AVG(no_vehicle_rate) * 100, 1) AS no_vehicle_pct,
  ROUND(AVG(assistance_rate) * 100, 1) AS assistance_pct
FROM `a4i_food.tract_demographics`;
EOF