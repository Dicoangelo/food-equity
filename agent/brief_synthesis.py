"""Evidence cards Gemini can select, with facts resolved after the clock recheck.

The model reasons about emphasis and tradeoffs. It returns references rather
than inventing factual prose or copying a stale number into a dispatch draft.
"""
import math
import re


def positive_quote(posting, quote, *, product=False):
    """Require the containing statement, not a clipped affirmative substring."""
    if not quote:
        return False
    matches = list(re.finditer(re.escape(quote), posting, re.I))
    if not matches:
        return False
    boundaries = [0, *[m.end() for m in re.finditer(r"(?<!\d)[.!?]|[;\n]", posting)], len(posting)]
    clauses = []
    for match in matches:
        left = max(x for x in boundaries if x <= match.start())
        right = min(x for x in boundaries if x >= match.end())
        clauses.append(posting[left:right])
    uncertain = r"\b(?:no|not|cannot|can't|don't|do not|isn't|without|never|unless|if|maybe|might|could|possibly)\b"
    if product:
        uncertain += r"|\b(?:contains?|ingredients?|allergens?|allergies|avoid|free.of)\b|[- ]free\b"
    return all(not re.search(uncertain, clause, re.I) for clause in clauses)


def literal_food_terms(terms, posting):
    if not isinstance(terms, list):
        return []
    output = []
    for term in terms[:3]:
        if not isinstance(term, str):
            continue
        term = term.strip()
        if (2 <= len(term) <= 80 and re.fullmatch(r"[\w '-]+", term)
                and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", posting, re.I)
                and positive_quote(posting, term, product=True)
                and term.casefold() not in [value.casefold() for value in output]):
            output.append(term)
    return output


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt(value):
    return f"{value:.2f}" if finite(value) else "unknown"


def evidence_cards(ranking, context):
    """Build a bounded fact catalog from real rows, never model-produced claims."""
    winner = ranking.get("winner")
    if not winner:
        return []
    cards = []

    def add(key, text, source):
        cards.append({"id": key, "text": text, "source": source})

    add("winner.capacity",
        f"{winner['name']} lists {fmt(winner.get('max_pallets'))} pallets of capacity. "
        "This is a stored profile limit; available space still needs confirmation.",
        "MCP vector_search_recipients / recipient profile")
    buffer = ranking.get("min_buffer_hours", .25)
    margin = winner.get("time_margin_hours")
    usable = margin - buffer if finite(margin) and finite(buffer) else None
    add("winner.clock",
        f"{winner['name']}'s estimated margin after the coordination buffer is {fmt(usable)}h. "
        "The estimate uses the stored pickup timing; an actual committed ETA is still required.",
        "Final deterministic deadline recheck")
    for index, candidate in enumerate(ranking.get("viable_runners_up", [])[:2]):
        prefix = f"alternative.{index}"
        for dimension, field, unit, meaning in (
            ("fit", "semantic_distance", "", "semantic distance (lower is closer)"),
            ("equity", "tract_poverty_pct", "%", "stored tract poverty indicator"),
            ("capacity", "max_pallets", " pallets", "profile capacity"),
            ("clock", "time_margin_hours", "h", "raw estimated deadline margin"),
        ):
            a, b = winner.get(field), candidate.get(field)
            if finite(a) and finite(b):
                direction = "higher" if a > b else "lower" if a < b else "equal"
                add(f"{prefix}.{dimension}",
                    f"Compared with {candidate['name']}, {winner['name']} has {direction} {meaning}: "
                    f"{fmt(a)}{unit} versus {fmt(b)}{unit}. Both remain proposals; "
                    "the combined ranking balances fit, neighborhood evidence and logistics.",
                    "Final ranked MCP candidates")
    for index, candidate in enumerate(ranking.get("eliminated_decoys", [])[:2]):
        reasons = "; ".join(candidate.get("elimination_reasons", []))
        if reasons:
            add(f"excluded.{index}", f"{candidate['name']} was excluded: {reasons}",
                "Deterministic operational constraints")
    for index, entry in enumerate(context.get("tract_equity", [])):
        data = entry.get("data") or {}
        if entry.get("status") == "ready":
            add(f"tract.{index}",
                f"Freshly retrieved stored tract {entry['request']['tract_id']} indicators: poverty rate "
                f"{fmt(data.get('poverty_rate'))}, no-vehicle rate {fmt(data.get('no_vehicle_rate'))} "
                "(fractions from 0 to 1). Observation year is unknown. This approximate tract "
                "association does not establish recipient service area or individual need.",
                "MCP get_tract_equity / ACS-derived starter snapshot")
        else:
            add(f"tract.{index}", "Additional tract evidence is unavailable or ambiguous; "
                "do not treat missing indicators as zero need.", "MCP get_tract_equity")
    for index, entry in enumerate(context.get("foodkeeper", [])):
        data = entry.get("data") or {}
        if entry.get("status") in {"ready", "options"} and data.get("products"):
            names = ", ".join(str(p.get("name", "Unknown product")) for p in data["products"][:3])
            add(f"shelf.{index}",
                f"FoodKeeper options for {entry['request']['query']}: {names}. Select the exact product "
                "and confirm its state, package dates and temperature history before applying "
                "storage advice. This lookup does not measure remaining freshness.",
                "MCP lookup_shelf_life / pinned USDA FoodKeeper reference")
        else:
            add(f"shelf.{index}", f"No verified product guidance was established for {entry['request']['query']}. "
                "Confirm the exact product and storage history; the donor deadline remains fixed.",
                "MCP lookup_shelf_life")
    return cards


def selected_cards(plan, cards):
    lookup = {card["id"]: card for card in cards}
    ids = list(dict.fromkeys(plan.get("evidence_order", [])))
    if any(key not in lookup for key in ids):
        raise ValueError("Synthesis cited evidence outside the final match")
    return [lookup[key] for key in ids[:5]]


def personalized_sms(plan, donor, winner, terms):
    """Vary the handoff around literal products and Gemini's selected priority."""
    if not terms:
        return None
    product = ", ".join(terms)[:40]
    name = lambda text: text if len(text) <= 40 else text[:39] + "…"
    priority = {
        "pickup_eta": "Can you collect before the deadline?",
        "storage": "Can you maintain the required storage?",
        "capacity": "Can your team take this quantity?",
        "transport": "Can your team provide suitable transport?",
        "receiving_hours": "Will your receiving team be available?",
        "acceptance": "Would this offer fit your program?",
    }[plan["confirmation_focus"][0]]
    return (
        f"{'Hello' if plan['tone'] == 'warm' else 'To'} {name(winner['name'])}: "
        f"{name(donor['name'])} offers {product}, {fmt(donor['pallet_count'])} pallets; "
        f"{donor['required_storage']} storage. Deadline {donor['deadline_at']}. {priority} "
        "Confirm acceptance, storage, capacity, pickup ETA, receiving hours and transport. "
        "Proposal only; nothing booked."
    )
