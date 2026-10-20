"""Read-only FoodKeeper reference search; never calculates remaining freshness."""
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "foodkeeper.json"
ADVISORY_NOTICE = (
    "FoodKeeper is consumer storage and quality guidance from the committed reference dataset. "
    "A listed duration is not measured remaining freshness, a donation safety clearance, "
    "or permission to extend the donor's pickup deadline. Confirm the exact product and state, "
    "package instructions and storage history before applying any guidance."
)
UNKNOWNS_REQUIRED = (
    "Exact product and subtype must be confirmed.",
    "Product state must be confirmed: unopened, opened, prepared or thawed, as applicable.",
    "Relevant purchase, opening, preparation and thawing timestamps must be supplied.",
    "Package label, package date and use-by date or manufacturer instructions must be checked.",
    "Storage and temperature history, including any cold-chain interruption, must be confirmed.",
)
STORAGE_FIELDS = (
    "pantry_output_display_only",
    "pantry_tips",
    "from_date_of_purchase_pantry_output_display_only",
    "from_date_of_purchase_pantry_tips",
    "pantry_after_opening_output_display_only",
    "refrigerate_output_display_only",
    "refrigerate_tips",
    "from_date_of_purchase_refrigerate_output_display_only",
    "from_date_of_purchase_refrigerate_tips",
    "refrigerate_after_opening_output_display_only",
    "refrigerate_after_thawing_output_display_only",
    "freeze_output_display_only",
    "freeze_tips",
    "from_date_of_purchase_freeze_output_display_only",
    "from_date_of_purchase_freeze_tips",
)


def _terms(value: str) -> set[str]:
    return set(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _field_context(field: str) -> tuple[str, str]:
    storage = (
        "refrigerated" if "refrigerate" in field else
        "frozen" if "freeze" in field else "pantry"
    )
    basis = (
        "from_purchase" if field.startswith("from_date_of_purchase_") else
        "after_opening" if "_after_opening_" in field else
        "after_thawing" if "_after_thawing_" in field else "unspecified"
    )
    return storage, basis


class FoodKeeperService:
    """Literal, bounded search over committed records with explicit product selection."""

    def __init__(self, data_path: str | Path | None = None):
        path = Path(data_path) if data_path is not None else DEFAULT_DATA_PATH
        content = path.read_bytes()
        records = json.loads(content)["product_data"]
        if not isinstance(records, list):
            raise ValueError("FoodKeeper product_data must be a list")
        self._records = {}
        self._terms = {}
        for record in records:
            product_id = record.get("id")
            if not isinstance(product_id, str) or product_id in self._records:
                raise ValueError("FoodKeeper identifiers must be unique strings")
            self._records[product_id] = record
            self._terms[product_id] = _terms(" ".join(
                record.get(field) or "" for field in ("name", "name_subtitle", "keywords")
            ))
        self._source = {
            "dataset": "FoodKeeper committed reference",
            "path": "data/foodkeeper.json",
            "sha256": sha256(content).hexdigest(),
            "product_count": len(records),
        }

    def _product(self, record: dict) -> dict:
        # Nulls and original field names remain visible; unspecified is never inferred unopened.
        raw = {field: record.get(field) for field in STORAGE_FIELDS}
        advice = []
        for field, value in raw.items():
            if value is None or value == "":
                continue
            storage, basis = _field_context(field)
            advice.append({
                "storage": storage, "basis": basis,
                "kind": "tip" if field.endswith("_tips") else "guidance",
                "raw_field": field, "raw_value": value,
            })
        return {
            "product_id": record["id"],
            "name": record.get("name"),
            "subtitle": record.get("name_subtitle"),
            "category_id": record.get("category_id"),
            "category": record.get("category_name_display_only"),
            "subcategory": record.get("subcategory_name_display_only"),
            "keywords": record.get("keywords"),
            "storage_advice": advice,
            "raw_storage_fields": raw,
            "applicability": "unconfirmed_product_state_and_history",
            "remaining_freshness_hours": None,
            "unknowns_required": list(UNKNOWNS_REQUIRED),
        }

    def _response(self, **fields) -> dict:
        return {
            **fields,
            "source": dict(self._source),
            "advisory_notice": ADVISORY_NOTICE,
            "unknowns_required": list(UNKNOWNS_REQUIRED),
            "remaining_freshness_hours": None,
            "donor_deadline_modified": False,
        }

    def search(self, query: str, limit: int = 5) -> dict:
        """Return all-term literal matches, not fuzzy guesses or automatic selections."""
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120:
            raise ValueError("query must contain 2–120 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("limit must be an integer from 1 to 10")
        query = query.strip()
        terms = _terms(query)
        if not terms:
            raise ValueError("query must contain searchable letters or numbers")
        matches = [
            record for product_id, record in self._records.items()
            if terms <= self._terms[product_id]
        ]
        matches.sort(key=lambda record: (
            (record.get("name") or "").casefold(),
            (record.get("name_subtitle") or "").casefold(),
            record["id"],
        ))
        return self._response(
            status="options" if matches else "no_match",
            query=query, match_method="all_query_terms_literal",
            selection_required=True, selected_product_id=None,
            total_matches=len(matches), truncated=len(matches) > limit,
            products=[self._product(record) for record in matches[:limit]],
        )

    def get_product(self, product_id: str) -> dict:
        """Explicit ID selection identifies a reference; it does not confirm applicability."""
        if not isinstance(product_id, str) or not re.fullmatch(r"[0-9]{1,6}", product_id):
            raise ValueError("product_id must be an exact numeric string of 1–6 digits")
        record = self._records.get(product_id)
        return self._response(
            status="product" if record else "not_found",
            match_method="exact_product_id", requested_product_id=product_id,
            selection_required=record is None,
            selected_product_id=product_id if record else None,
            total_matches=1 if record else 0, truncated=False,
            products=[self._product(record)] if record else [],
        )
