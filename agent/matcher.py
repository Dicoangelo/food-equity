"""
Seattle Food Equity & Surplus Broker - Matching & Constraint Filtering Engine
Challenge 2: Resilient Food Equity & Surplus Broker (Agents for Impact 2026)

This module implements the core differentiator logic:
1. Semantic Candidate Generation (BigQuery Vector Search via AI.EMBED)
2. Hard Constraint Filtering (Cold storage, Pallet capacity, Expiry clock)
3. Equity & Logistics Scoring (Census tract demographics: no-vehicle rate & poverty rate)
4. Audit Trail (Defending why viable recipients were chosen and why semantic decoys were eliminated)
"""

import json
import logging
from typing import Dict, List, Any, Optional
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT_ID = "qwiklabs-gcp-04-b992b9561e32"
DATASET_ID = "a4i_food"


class SurplusMatcher:
    def __init__(self, project_id: str = PROJECT_ID, dataset_id: str = DATASET_ID):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.client = bigquery.Client(project=project_id)

    def search_candidates(self, query_text: str, top_k: int = 15) -> List[Dict[str, Any]]:
        """
        Executes BigQuery VECTOR_SEARCH using AI.EMBED with gemini-embedding-2.
        Includes attached census tract equity metrics.
        """
        sql = f"""
        SELECT 
          base.recipient_id,
          base.name,
          base.street,
          base.city,
          base.zip_code,
          base.archetype,
          base.cold_storage,
          base.frozen_storage,
          base.loading_dock,
          base.refrigerated_transport,
          base.max_pallets,
          base.pickup_window_hours,
          base.weekly_households,
          base.service_days,
          base.profile_text,
          base.latitude,
          base.longitude,
          base.tract_id,
          base.tract_poverty_rate,
          base.tract_no_vehicle_rate,
          base.tract_median_income,
          base.tract_dist_km,
          distance
        FROM VECTOR_SEARCH(
          TABLE `{self.project_id}.{self.dataset_id}.recipient_embeddings`, 'embedding',
          (SELECT AI.EMBED(@query_text, endpoint => 'gemini-embedding-2').result AS embedding),
          top_k => @top_k,
          options => '{{"use_brute_force":true}}'
        )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_text", "STRING", query_text),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        query_job = self.client.query(sql, job_config=job_config)
        rows = query_job.result()
        return [dict(row) for row in rows]

    def evaluate_constraints(
        self,
        candidate: Dict[str, Any],
        required_storage: Optional[str] = "cold",
        pallet_count: float = 1.0,
        hours_remaining: float = 6.0,
    ) -> Dict[str, Any]:
        """
        Evaluates physical and operational feasibility for a candidate.
        Returns: { 'viable': bool, 'reasons': List[str], 'warnings': List[str] }
        """
        rejection_reasons = []
        warnings = []
        cold_storage = (candidate.get("cold_storage") or "none").lower()

        # 1. Cold Chain Constraint (Reject Decoys)
        if required_storage == "cold":
            if cold_storage in ["none", "", "dry", "ambient"]:
                rejection_reasons.append(
                    f"Physical failure: requires cold storage, but organization has '{cold_storage}' refrigeration."
                )
        elif required_storage == "frozen":
            if not candidate.get("frozen_storage"):
                rejection_reasons.append(
                    "Physical failure: requires freezer, but organization has no frozen storage."
                )

        # 2. Pallet / Volume Capacity Constraint
        max_pallets = candidate.get("max_pallets") or 0
        if pallet_count > max_pallets:
            rejection_reasons.append(
                f"Capacity failure: posting is {pallet_count} pallets, organization capacity is {max_pallets} pallets."
            )

        # 3. Clock & Logistics Feasibility
        pickup_window = candidate.get("pickup_window_hours") or 24
        if pickup_window > (hours_remaining * 1.5):
            if not candidate.get("refrigerated_transport"):
                rejection_reasons.append(
                    f"Clock failure: pickup window ({pickup_window}h) exceeds remaining shelf life ({hours_remaining}h) with no reefer transport."
                )
            else:
                warnings.append(
                    f"Tight clock: pickup window is {pickup_window}h for {hours_remaining}h remaining, but organization has own reefer transport."
                )

        return {
            "viable": len(rejection_reasons) == 0,
            "reasons": rejection_reasons,
            "warnings": warnings,
        }

    def score_and_rank(
        self,
        candidates: List[Dict[str, Any]],
        required_storage: str = "cold",
        pallet_count: float = 1.0,
        hours_remaining: float = 6.0,
    ) -> Dict[str, Any]:
        """
        Applies operational filtering, equity scoring, and ranks all candidates.
        """
        viable = []
        eliminated = []

        for c in candidates:
            eval_res = self.evaluate_constraints(
                c,
                required_storage=required_storage,
                pallet_count=pallet_count,
                hours_remaining=hours_remaining,
            )

            # Cosine semantic proximity (1 - distance)
            distance = c.get("distance", 1.0)
            semantic_score = max(0.0, 1.0 - distance)

            # Equity score (Poverty rate + No vehicle rate)
            poverty_rate = c.get("tract_poverty_rate") or 0.0
            no_vehicle_rate = c.get("tract_no_vehicle_rate") or 0.0
            equity_score = (poverty_rate * 0.5) + (no_vehicle_rate * 0.5)

            # Operational readiness score
            logistics_score = 0.5
            if c.get("refrigerated_transport"):
                logistics_score += 0.3
            if c.get("loading_dock"):
                logistics_score += 0.2

            composite_score = (
                (0.40 * semantic_score) +
                (0.35 * equity_score) +
                (0.25 * logistics_score)
            )

            c_info = {
                "recipient_id": c.get("recipient_id"),
                "name": c.get("name"),
                "street": c.get("street") or "On File",
                "city": c.get("city") or "Seattle",
                "zip_code": c.get("zip_code") or "",
                "archetype": c.get("archetype"),
                "cold_storage": c.get("cold_storage"),
                "max_pallets": c.get("max_pallets"),
                "pickup_window_hours": c.get("pickup_window_hours"),
                "weekly_households": c.get("weekly_households"),
                "service_days": c.get("service_days"),
                "profile_text": c.get("profile_text"),
                "latitude": c.get("latitude"),
                "longitude": c.get("longitude"),
                "tract_id": c.get("tract_id"),
                "tract_poverty_pct": round(poverty_rate * 100, 1),
                "tract_no_vehicle_pct": round(no_vehicle_rate * 100, 1),
                "semantic_distance": round(distance, 4),
                "composite_score": round(composite_score, 4),
                "warnings": eval_res["warnings"],
            }

            if eval_res["viable"]:
                viable.append(c_info)
            else:
                c_info["elimination_reasons"] = eval_res["reasons"]
                eliminated.append(c_info)

        # Sort viable candidates by composite score descending
        viable.sort(key=lambda x: x["composite_score"], reverse=True)

        return {
            "winner": viable[0] if viable else None,
            "viable_runners_up": viable[1:4] if len(viable) > 1 else [],
            "eliminated_decoys": eliminated[:5],
            "total_candidates_evaluated": len(candidates),
        }
