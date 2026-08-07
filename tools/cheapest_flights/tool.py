# =============================================================================
# cheapest_flights tool — tools/cheapest_flights/tool.py
# =============================================================================
# WHAT: Finds the cheapest real flight options between two cities across a window
#       of departure dates. Wraps the ported FlightsService (flights.py).
#
# WHY it resolves city names to airports: the agent gives free-text cities; the
#       service maps them to IATA codes before querying.
#
# HOW: exports `TOOL`; the loader registers it. `has_token` is a PROPERTY.
# =============================================================================

import datetime

from tools.cheapest_flights.flights import FlightsAuthError, FlightsService
from tools.contract import ToolDef

_flights = FlightsService()


async def cheapest_flights(
    origin: str, destination: str, depart_date: str, days_window: int = 5
) -> str:
    if not _flights.has_token:  # property, not a method
        return "Error: flight search isn't configured (no Travelpayouts token)."
    try:
        depart = datetime.date.fromisoformat(depart_date)
    except ValueError:
        return f"Error: depart_date '{depart_date}' must be YYYY-MM-DD."
    o = await _flights.resolve_city(origin)
    d = await _flights.resolve_city(destination)
    if o is None:
        return f"Error: couldn't resolve origin city '{origin}'."
    if d is None:
        return f"Error: couldn't resolve destination city '{destination}'."
    try:
        offers = await _flights.cheapest(
            o[0], d[0], depart, days_window=max(1, min(days_window, 14))
        )
    except FlightsAuthError as e:
        # Caught HERE (not left to the servicer's catch-all) so the agent gets
        # the actionable config message instead of a generic "tool failed".
        return f"Error: {e}"
    if not offers:
        return f"No flight offers found {origin}→{destination} around {depart_date}."
    return f"Cheapest {origin}→{destination} near {depart_date}:\n" + "\n".join(
        of.describe() for of in offers
    )


TOOL = ToolDef(
    name="cheapest_flights",
    description=(
        "Find the cheapest real flight options between two cities across a window of "
        "departure dates. Call when the owner wants ticket prices. Cities are free "
        "text (resolved to airports); depart_date is YYYY-MM-DD."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "depart_date": {"type": "string", "description": "YYYY-MM-DD"},
            "days_window": {"type": "integer", "description": "days to shift (1-14)"},
        },
        "required": ["origin", "destination", "depart_date"],
    },
    handler=cheapest_flights,
)
