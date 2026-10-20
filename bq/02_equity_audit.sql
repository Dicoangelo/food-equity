-- =====================================================================
-- 02_equity_audit.sql
-- READ-ONLY SELECTED-RECIPIENT BASELINE DIAGNOSTIC, not a match/equity audit.
-- Recipient 91 is an example lookup, not a claimed audit winner.
-- Read the canonical live corpus; do not depend on the legacy proposal in 01.
--
-- The baseline is every supplied tract_demographics row, without map bounds.
-- Region coverage comes from starter metadata, not a hardcoded Seattle mean.
-- Means are equal-tract, not population/household weighted. Missing/invalid
-- rates are excluded per metric and the included denominator is shown.
-- Profile operations are generated/planted; ZIP-centroid tract association
-- is approximate. This query establishes neither delivery nor causal equity.
--
-- For the reproducible ten-posting MCP audit use scripts/audit_postings.py
-- with an explicitly reviewed scenario manifest and evaluation timestamp.
-- =====================================================================
DECLARE selected_recipient_id STRING DEFAULT '91';

WITH valid_rates AS (
  SELECT
    geo_id,
    IF(poverty_rate BETWEEN 0 AND 1, poverty_rate, NULL) AS poverty_rate,
    IF(no_vehicle_rate BETWEEN 0 AND 1, no_vehicle_rate, NULL) AS no_vehicle_rate,
    IF(assistance_rate BETWEEN 0 AND 1, assistance_rate, NULL) AS assistance_rate
  FROM `a4i_food.tract_demographics`
),
selected_profile AS (
  SELECT recipient_id, tract_id, profile_source
  FROM `a4i_food.recipient_embeddings`
  WHERE CAST(recipient_id AS STRING) = selected_recipient_id
),
selected_tract AS (
  SELECT t.*, r.profile_source
  FROM selected_profile r
  JOIN valid_rates t
    ON CAST(r.tract_id AS STRING) = CAST(t.geo_id AS STRING)
)
SELECT
  CONCAT('Selected profile ', selected_recipient_id, ' tract diagnostic') AS cohort,
  (SELECT COUNT(*) FROM selected_profile) AS selected_profile_rows,
  COALESCE(STRING_AGG(DISTINCT profile_source, ', '), 'no joined profile') AS profile_source,
  COUNT(*) AS tract_rows,
  COUNT(poverty_rate) AS poverty_observations,
  COUNT(no_vehicle_rate) AS no_vehicle_observations,
  COUNT(assistance_rate) AS assistance_observations,
  ROUND(AVG(poverty_rate) * 100, 1) AS poverty_pct,
  ROUND(AVG(no_vehicle_rate) * 100, 1) AS no_vehicle_pct,
  ROUND(AVG(assistance_rate) * 100, 1) AS assistance_pct
FROM selected_tract
UNION ALL
SELECT
  'Full supplied tract dataset: equal-tract baseline' AS cohort,
  CAST(NULL AS INT64) AS selected_profile_rows,
  'not applicable' AS profile_source,
  COUNT(*) AS tract_rows,
  COUNT(poverty_rate) AS poverty_observations,
  COUNT(no_vehicle_rate) AS no_vehicle_observations,
  COUNT(assistance_rate) AS assistance_observations,
  ROUND(AVG(poverty_rate) * 100, 1) AS poverty_pct,
  ROUND(AVG(no_vehicle_rate) * 100, 1) AS no_vehicle_pct,
  ROUND(AVG(assistance_rate) * 100, 1) AS assistance_pct
FROM valid_rates;
