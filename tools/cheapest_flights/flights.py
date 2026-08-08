# =============================================================================
# Flights Service — app/services/flights.py
# =============================================================================
# WHAT: Real airfare lookup via the Aviasales / Travelpayouts data API.
#
# WHY an API and not "read the Aviasales website":
#   Aviasales and Tutu render prices with JavaScript in the browser, so a plain
#   page fetch (WebFetch) comes back with an empty skeleton — no prices. That
#   is exactly why the assistant used to hedge ("couldn't get the exact price")
#   and bounce the question back to the user. The Travelpayouts data API returns
#   prices as JSON directly: no browser, no scraping, no captcha, and we can
#   check a whole spread of dates in one go and pick the cheapest — which is the
#   independent behaviour the user asked for.
#
# HOW it works:
#   1. resolve_city() turns "Lisbon" / "Уфа" into an IATA code (LIS / UFA).
#      This endpoint needs NO token, so city resolution always works.
#   2. cheapest() calls prices_for_dates across a window of departure dates and
#      returns the cheapest concrete options (date + price + airline). This
#      needs a token (free from travelpayouts.com); without one we say so
#      clearly instead of pretending.
# =============================================================================

import asyncio
import datetime
import logging

import httpx

from tools.config import settings

logger = logging.getLogger(__name__)

_AUTOCOMPLETE_URL = "https://autocomplete.travelpayouts.com/places2"
_PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
_TIMEOUT = 20.0


class FlightsAuthError(Exception):
    """The prices API rejected the token (missing, invalid, or expired).

    Distinct from "no fares found" on purpose: a rejected token is an
    actionable config problem (regenerate it), not an empty result — and
    conflating the two is exactly what made the tool say "no flights" when the
    real problem was the token.
    """


class FlightOffer:
    """One concrete flight option — a real date and a real price."""

    def __init__(self, date: str, price: int, airline: str, currency: str, link: str) -> None:
        self.date = date
        self.price = price
        self.airline = airline
        self.currency = currency
        self.link = link

    def describe(self) -> str:
        line = f"{self.date}: {self.price} {self.currency}"
        if self.airline:
            line += f" ({self.airline})"
        if self.link:
            line += f" — {self.link}"
        return line


class FlightsService:
    """Airfare lookup over the Travelpayouts data API."""

    def __init__(self, token: str | None = None, marker: str | None = None) -> None:
        self._token = token if token is not None else settings.travelpayouts_token
        self._marker = marker if marker is not None else settings.travelpayouts_marker

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    async def resolve_city(self, name: str, language: str = "en") -> tuple[str, str] | None:
        """City name (any language) → (IATA code, canonical name). Token-free.

        Returns None if nothing matched, so the caller can tell the user the
        place wasn't recognised rather than silently guessing. `language`
        only affects which language the canonical name comes back in — the
        input city name itself is understood in whatever language it's given.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _AUTOCOMPLETE_URL,
                    params={"term": name, "locale": language, "types[]": "city"},
                )
                resp.raise_for_status()
                results = resp.json()
        except Exception:
            logger.exception("City resolve failed for %r", name)
            return None

        for item in results:
            if item.get("code"):
                return item["code"], item.get("name", name)
        return None

    async def cheapest(
        self,
        origin: str,
        destination: str,
        depart_from: datetime.date,
        days_window: int = 5,
        one_way: bool = True,
        max_offers: int = 6,
        language: str = "en",
    ) -> list[FlightOffer]:
        """Cheapest concrete options across a window of departure dates.

        WHY a window and not a single date: the user explicitly wanted the
        assistant to be independent — "get the cheapest by shifting two or
        three days". So we probe several consecutive dates concurrently and
        return the cheapest, each tied to a real date.
        """
        if not self._token:
            return []

        dates = [depart_from + datetime.timedelta(days=i) for i in range(max(1, days_window))]
        auth_failed = False
        currency_code, currency_symbol = ("rub", "₽") if language == "ru" else ("usd", "$")

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async def one(day: datetime.date) -> list[FlightOffer]:
                nonlocal auth_failed
                try:
                    resp = await client.get(
                        _PRICES_URL,
                        params={
                            "origin": origin,
                            "destination": destination,
                            "departure_at": day.isoformat(),
                            "one_way": "true" if one_way else "false",
                            "currency": currency_code,
                            "sorting": "price",
                            "limit": 3,
                            "token": self._token,
                        },
                    )
                except Exception:
                    logger.debug("Price fetch failed for %s", day, exc_info=True)
                    return []

                # A rejected token is a config problem, not "no fares" — flag it
                # so the caller can tell the user to regenerate the token.
                if resp.status_code in (401, 403):
                    auth_failed = True
                    return []
                if resp.status_code >= 400:
                    logger.debug("Price API %s for %s", resp.status_code, day)
                    return []
                try:
                    payload = resp.json()
                except Exception:
                    return []

                offers = []
                for row in payload.get("data", []):
                    price = row.get("price")
                    if not price:
                        continue
                    link = row.get("link", "")
                    if link and link.startswith("/"):
                        link = "https://www.aviasales.ru" + link
                    offers.append(
                        FlightOffer(
                            date=row.get("departure_at", day.isoformat())[:10],
                            price=int(price),
                            airline=row.get("airline", ""),
                            currency=currency_symbol,
                            link=link,
                        )
                    )
                return offers

            batches = await asyncio.gather(*(one(d) for d in dates))

        if auth_failed:
            # A message is REQUIRED here: this exception used to be raised bare,
            # fall through to the servicer's generic handler, and reach the agent
            # as "failed: " with an empty tail — hiding the one actionable fact.
            raise FlightsAuthError(
                "Travelpayouts rejected the token (401/403). Regenerate it at "
                "travelpayouts.com and update TRAVELPAYOUTS_TOKEN in .env."
            )

        flat = [offer for batch in batches for offer in batch]
        flat.sort(key=lambda o: o.price)
        # De-dup by date, keeping the cheapest per date, then cap.
        seen: set[str] = set()
        unique = []
        for offer in flat:
            if offer.date in seen:
                continue
            seen.add(offer.date)
            unique.append(offer)
        return unique[:max_offers]
