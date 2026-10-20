cat << 'EOF' > bq/01_embed_profiles.sql
-- =====================================================================
-- 01_embed_profiles.sql
-- Description: Generates vector embeddings for Seattle recipients and 
--              attaches census tract demographics via spatial distance.
-- =====================================================================
-- 1. Create Remote Embedding Model pointing to Vertex AI
CREATE OR REPLACE MODEL `a4i_food.embedding_model`
REMOTE WITH CONNECTION `us.vertex_ai_conn`
OPTIONS (ENDPOINT = 'text-embedding-004');
-- 2. Materialize Embeddings for Recipient Profiles (768-dim)
CREATE OR REPLACE TABLE `a4i_food.recipients_embedded` AS
SELECT 
  * EXCEPT(content),
  ml_generate_embedding_result AS embedding
FROM ML.GENERATE_EMBEDDING(
  MODEL `a4i_food.embedding_model`,
  (SELECT *, profile_text AS content FROM `a4i_food.recipients`),
  STRUCT('SEMANTIC_SIMILARITY' AS task_type)
);
-- 3. Spatially Join Nearest Census Tract (Tract ID, Poverty & Vehicle Rates)
CREATE OR REPLACE TABLE `a4i_food.recipients_final` AS
WITH ranked_tracts AS (
  SELECT 
    r.*,
    t.geo_id AS tract_id,
    t.poverty_rate,
    t.no_vehicle_rate,
    t.assistance_rate,
    ROW_NUMBER() OVER (
      PARTITION BY r.recipient_id 
      ORDER BY ST_DISTANCE(ST_GEOGPOINT(r.longitude, r.latitude), ST_GEOGPOINT(t.tract_lon, t.tract_lat)) ASC
    ) AS rank_num
  FROM `a4i_food.recipients_embedded` r
  CROSS JOIN `a4i_food.tract_demographics` t
)
SELECT * EXCEPT(rank_num)
FROM ranked_tracts
WHERE rank_num = 1;
EOF