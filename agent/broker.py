"""
Seattle Food Equity & Surplus Broker - Core Agent & Brief Synthesizer
Uses Vertex AI Gemini and BigQuery SurplusMatcher to produce actionable Match Briefs.
"""

import json
import logging
from typing import Dict, Any, Optional
from google import genai
from matcher import SurplusMatcher

logger = logging.getLogger(__name__)

PROJECT_ID = "qwiklabs-gcp-04-b992b9561e32"
LOCATION = "us-central1"


class FoodBrokerAgent:
    def __init__(self, project_id: str = PROJECT_ID, location: str = LOCATION):
        self.project_id = project_id
        self.location = location
        self.matcher = SurplusMatcher(project_id=project_id)
        self.ai_client = genai.Client(vertexai=True, project=project_id, location=location)

    def broker_posting(
        self,
        posting_text: str,
        required_storage: str = "cold",
        pallet_count: float = 1.0,
        hours_remaining: float = 6.0,
        donor_name: str = "Seattle Metro Grocer",
        donor_lat: float = 47.6062,
        donor_lon: float = -122.3321,
    ) -> Dict[str, Any]:
        """
        Executes end-to-end brokering:
        1. BigQuery Vector Search candidate generation
        2. Hard constraint filtering & decoy elimination
        3. Census tract equity scoring
        4. Gemini synthesis of Match Brief & outreach draft
        """
        # 1. Search candidates in BigQuery
        candidates = self.matcher.search_candidates(posting_text, top_k=12)

        # 2. Filter constraints & rank
        ranking = self.matcher.score_and_rank(
            candidates,
            required_storage=required_storage,
            pallet_count=pallet_count,
            hours_remaining=hours_remaining,
        )

        winner = ranking.get("winner")
        runners_up = ranking.get("viable_runners_up", [])
        decoys = ranking.get("eliminated_decoys", [])

        if not winner:
            return {
                "status": "no_viable_match",
                "message": "No organizations met the physical and operational constraints.",
                "eliminated_decoys": decoys,
            }

        # 3. Gemini Prompt for Match Brief Synthesis
        prompt = f"""
You are the Lead Coordinator for the Seattle Food Equity & Surplus Broker.
Synthesize the technical match results into an actionable, professional Match Brief for a volunteer driver and recipient coordinator.

DONOR POSTING:
- Donor: {donor_name}
- Offer: "{posting_text}"
- Required Storage: {required_storage}
- Volume: {pallet_count} pallet(s)
- Clock: {hours_remaining} hours remaining before landfill compactor

SELECTED RECIPIENT (WINNER):
- Name: {winner['name']}
- Address: {winner['street']}, {winner['city']}, WA {winner['zip_code']}
- Archetype: {winner['archetype']}
- Cold Storage: {winner['cold_storage']}
- Max Pallets: {winner['max_pallets']}
- Pickup Window: {winner['pickup_window_hours']} hours
- Service Days: {winner['service_days']}
- Census Tract: {winner['tract_id']} (Poverty: {winner['tract_poverty_pct']}%, No-Vehicle: {winner['tract_no_vehicle_pct']}%)
- Mission: {winner['profile_text']}

ELIMINATED TOP SEMANTIC DECOYS (for defense / justification):
{json.dumps([{'name': d['name'], 'reasons': d.get('elimination_reasons', [])} for d in decoys[:2]], indent=2)}

Generate a response in JSON format with exactly the following keys:
1. "match_justification": 2-3 sentences explaining why this organization won over the alternatives, specifically contrasting against the eliminated decoys that lacked refrigeration or capacity.
2. "operational_verification": 1-2 sentences certifying physical cold storage and pallet capacity.
3. "clock_action_plan": Clear statement verifying pickup window against the {hours_remaining}h deadline.
4. "equity_rationale": 1 sentence explaining the neighborhood census tract need (vehicle access/poverty).
5. "drafted_sms_outreach": Short, urgent SMS message (under 160 characters) to the recipient coordinator.
6. "drafted_email_outreach": Professional, ready-to-send email to recipient leadership detailing the load, timing, and dock instructions.
"""

        try:
            response = self.ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            brief_content = json.loads(response.text)
        except Exception as e:
            logger.warning(f"Gemini generation fallback: {e}")
            brief_content = {
                "match_justification": f"Selected {winner['name']} based on confirmed {winner['cold_storage']} storage and high equity need in tract {winner['tract_id']}.",
                "operational_verification": f"Verified capacity for {pallet_count} pallet(s) (limit: {winner['max_pallets']}) and {winner['cold_storage']} refrigeration.",
                "clock_action_plan": f"Pickup window of {winner['pickup_window_hours']}h is feasible within the {hours_remaining}h deadline.",
                "equity_rationale": f"Serves neighborhood with {winner['tract_no_vehicle_pct']}% vehicle-less households and {winner['tract_poverty_pct']}% poverty rate.",
                "drafted_sms_outreach": f"URGENT: Surplus {posting_text[:30]} available from {donor_name}. Can you accept within {hours_remaining}h? Reply YES to claim.",
                "drafted_email_outreach": f"Subject: Surplus Food Allocation: {posting_text[:40]}\n\nHello {winner['name']} Team,\n\nWe have secured {posting_text} from {donor_name}. Please confirm your team can receive this delivery within {hours_remaining} hours.",
            }

        # 4. Assembled Hand-Off Package
        return {
            "status": "success",
            "donor": {
                "name": donor_name,
                "latitude": donor_lat,
                "longitude": donor_lon,
                "posting_text": posting_text,
                "required_storage": required_storage,
                "pallet_count": pallet_count,
                "hours_remaining": hours_remaining,
            },
            "winner": winner,
            "runners_up": runners_up,
            "eliminated_decoys": decoys,
            "match_brief": brief_content,
            "map_markers": {
                "donor": {"name": donor_name, "lat": donor_lat, "lon": donor_lon, "type": "donor"},
                "winner": {
                    "name": winner["name"],
                    "lat": winner["latitude"],
                    "lon": winner["longitude"],
                    "address": f"{winner['street']}, {winner['city']}",
                    "storage": winner["cold_storage"],
                    "capacity": winner["max_pallets"],
                    "score": winner["composite_score"],
                    "type": "winner",
                },
                "viable": [
                    {
                        "name": r["name"],
                        "lat": r["latitude"],
                        "lon": r["longitude"],
                        "address": f"{r['street']}, {r['city']}",
                        "storage": r["cold_storage"],
                        "score": r["composite_score"],
                        "type": "viable",
                    }
                    for r in runners_up
                ],
                "decoys": [
                    {
                        "name": d["name"],
                        "lat": d["latitude"],
                        "lon": d["longitude"],
                        "reasons": d.get("elimination_reasons", []),
                        "type": "decoy",
                    }
                    for d in decoys
                ],
            },
        }
