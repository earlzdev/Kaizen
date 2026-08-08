# =============================================================================
# Unit tests — tools/cheapest_flights (the auth-error message path)
# =============================================================================
# WHAT: the cheapest_flights handler's error paths — above all that a rejected
#       Travelpayouts token reaches the agent as an ACTIONABLE "Error: ..."
#       message, not an empty-tailed generic failure.
# WHY: FlightsAuthError used to be raised bare and uncaught; the agent saw
#       "failed: " with nothing after it (ARCHITECTURE_REVIEW.md §2.2-8).
# HOW: the module-level _flights singleton is swapped for a fake via
#       monkeypatch — no network.
# =============================================================================

import tools.cheapest_flights.tool as flights_tool
from tools.cheapest_flights.flights import FlightsAuthError


class FakeFlights:
    def __init__(self, cheapest_exc=None, offers=None):
        self.has_token = True
        self._exc = cheapest_exc
        self._offers = offers or []

    async def resolve_city(self, name, language="en"):
        return (name[:3].upper(), name)

    async def cheapest(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._offers


async def test_auth_error_reaches_agent_with_its_message(monkeypatch):
    exc = FlightsAuthError("Travelpayouts rejected the token (401/403). Regenerate it.")
    monkeypatch.setattr(flights_tool, "_flights", FakeFlights(cheapest_exc=exc))
    result = await flights_tool.cheapest_flights("Уфа", "Москва", "2026-08-01")
    assert result.startswith("Error:")
    assert "Travelpayouts" in result  # the actionable part survives


def test_auth_error_default_message_is_not_empty():
    # The exception is raised in flights.py with an explicit message; this
    # guards against a regression back to the bare `raise FlightsAuthError()`.
    import inspect

    from tools.cheapest_flights import flights

    source = inspect.getsource(flights)
    assert "raise FlightsAuthError()" not in source


async def test_bad_date_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(flights_tool, "_flights", FakeFlights())
    result = await flights_tool.cheapest_flights("Уфа", "Москва", "01.08.2026")
    assert result.startswith("Error:")
    assert "YYYY-MM-DD" in result


async def test_no_offers_is_not_an_error(monkeypatch):
    monkeypatch.setattr(flights_tool, "_flights", FakeFlights(offers=[]))
    result = await flights_tool.cheapest_flights("Уфа", "Москва", "2026-08-01")
    assert not result.startswith("Error:")
    assert "No flight offers" in result
