-- =====================================================================
-- 01_embed_profiles.sql
-- LEGACY SANDBOX PROPOSAL ONLY: text-embedding-004, 768 dimensions.
-- Not part of deployment or the live broker's data path.
--
-- The live corpus is a4i_food.recipient_embeddings (gemini-embedding-2,
-- 3072 dimensions); agent/tools.yaml embeds queries with that same model.
-- Never search that corpus with vectors produced by this proposal, and
-- never rename these proposal tables to replace the live corpus.
--
-- Manual prerequisites, not verified by this script: the selected project
-- has the starter recipients/tract_demographics tables in the US dataset,
-- an existing US connection us.vertex_ai_conn, and permission to invoke
-- text-embedding-004. This script does not create a connection or grant IAM.
-- It creates isolated proposal objects and may incur embedding/query usage.
-- CREATE intentionally fails if an object exists; it never replaces one.
--
-- Operational profiles are generated/planted. Recipient coordinates are
-- approximate ZIP centroids; nearest tract is context, not a catchment.
-- =====================================================================
-- 1. Preserve the teammate's alternative remote-model experiment.
CREATE MODEL `a4i_food.embedding_model_legacy_004_proposal`
REMOTE WITH CONNECTION `us.vertex_ai_conn`
OPTIONS (ENDPOINT = 'text-embedding-004');

-- 2. Retain only successful, correctly sized proposal vectors.
-- Compare row counts with recipients before treating the proposal as complete.
CREATE TABLE `a4i_food.recipients_embedded_legacy_004_proposal` AS
SELECT
  * EXCEPT(content, ml_generate_embedding_result),
  ml_generate_embedding_result AS embedding
FROM ML.GENERATE_EMBEDDING(
  MODEL `a4i_food.embedding_model_legacy_004_proposal`,
  (SELECT *, profile_text AS content FROM `a4i_food.recipients`),
  STRUCT('SEMANTIC_SIMILARITY' AS task_type)
)
WHERE ml_generate_embedding_status = ''
  AND ARRAY_LENGTH(ml_generate_embedding_result) = 768;

-- 3. Attach the nearest valid tract centroid, preserving profile_source.
-- Missing/invalid coordinates are excluded, not treated as zero distance.
CREATE TABLE `a4i_food.recipients_final_legacy_004_proposal` AS
WITH ranked_tracts AS (
  SELECT
    r.*,
    t.geo_id AS tract_id,
    t.poverty_rate,
    t.no_vehicle_rate,
    t.assistance_rate,
    ROW_NUMBER() OVER (
      PARTITION BY r.recipient_id
      ORDER BY ST_DISTANCE(
        ST_GEOGPOINT(r.longitude, r.latitude),
        ST_GEOGPOINT(t.tract_lon, t.tract_lat)
      ) ASC, t.geo_id ASC
    ) AS rank_num
  FROM `a4i_food.recipients_embedded_legacy_004_proposal` r
  CROSS JOIN `a4i_food.tract_demographics` t
  WHERE r.latitude BETWEEN -90 AND 90
    AND r.longitude BETWEEN -180 AND 180
    AND t.tract_lat BETWEEN -90 AND 90
    AND t.tract_lon BETWEEN -180 AND 180
)
SELECT * EXCEPT(rank_num)
FROM ranked_tracts
WHERE rank_num = 1;
