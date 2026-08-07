# =============================================================================
# Traffic Service — app/services/traffic.py
# =============================================================================
# WHAT: The current traffic-jam score ("пробки N баллов", 0-10) for a Russian
#       city, read live from Yandex Maps through the headless browser.
#
# WHY a dedicated service with a city→geo-id map:
#   Yandex shows the score only on a city's own Maps page, and — verified by
#   testing — the CITY is decided by Yandex's numeric geo-id in the URL, not by
#   the name in it (a wrong id silently shows a different city's traffic). So we
#   can't just paste the city name: we map the name to the correct geo-id, open
#   that page in a real browser (the score is rendered by JavaScript), and read
#   the "пробки N баллов" it draws.
#
# HOW: city_score(name) → (score, label, url) or None if the city isn't mapped
#      or the page didn't render. The scores were spot-checked live (Ufa 2,
#      Novosibirsk 1, SPb 3, Moscow 3) so the ids are real, not guessed.
# =============================================================================

import logging
import re

logger = logging.getLogger(__name__)

# City (lowercase Russian name) → Yandex Maps geo-id + latin slug. The geo-id is
# authoritative; the slug is cosmetic. Add cities here — that's the whole map.
_CITY_GEO: dict[str, tuple[int, str]] = {
    "москва": (213, "moscow"),
    "санкт-петербург": (2, "saint-petersburg"),
    "спб": (2, "saint-petersburg"),
    "питер": (2, "saint-petersburg"),
    "уфа": (172, "ufa"),
    "казань": (43, "kazan"),
    "новосибирск": (65, "novosibirsk"),
    "екатеринбург": (54, "yekaterinburg"),
    "нижний новгород": (47, "nizhny-novgorod"),
    "челябинск": (56, "chelyabinsk"),
    "самара": (51, "samara"),
    "омск": (66, "omsk"),
    "ростов-на-дону": (39, "rostov-na-donu"),
    "ростов": (39, "rostov-na-donu"),
    "красноярск": (62, "krasnoyarsk"),
    "пермь": (50, "perm"),
    "воронеж": (193, "voronezh"),
    "волгоград": (38, "volgograd"),
    "краснодар": (35, "krasnodar"),
    "саратов": (194, "saratov"),
    "тюмень": (55, "tyumen"),
    "ижевск": (44, "izhevsk"),
    "ульяновск": (195, "ulyanovsk"),
    "барнаул": (197, "barnaul"),
    "иркутск": (63, "irkutsk"),
    "оренбург": (48, "orenburg"),
    "кемерово": (64, "kemerovo"),
    "рязань": (11, "ryazan"),
    "астрахань": (37, "astrakhan"),
    "пенза": (49, "penza"),
    "липецк": (9, "lipetsk"),
    "киров": (46, "kirov"),
    "чебоксары": (45, "cheboksary"),
    "тула": (15, "tula"),
    "калининград": (22, "kaliningrad"),
    "курск": (8, "kursk"),
    "ставрополь": (36, "stavropol"),
    "тверь": (14, "tver"),
    "сочи": (239, "sochi"),
    "ярославль": (16, "yaroslavl"),
    "владивосток": (75, "vladivostok"),
    "хабаровск": (76, "khabarovsk"),
    "томск": (67, "tomsk"),
}

_SCORE_RE = re.compile(r"пробк[а-я]*\s*(\d+)\s*балл", re.IGNORECASE)


def _label(score: int) -> str:
    if score <= 3:
        return "свободно"
    if score <= 6:
        return "средняя загруженность"
    return "серьёзные пробки"


class TrafficService:
    """Reads a city's Yandex traffic score via the headless browser."""

    def __init__(self, browser) -> None:  # BrowserService
        self._browser = browser

    def known(self, city: str) -> bool:
        return self._normalise(city) in _CITY_GEO

    @staticmethod
    def _normalise(city: str) -> str:
        return city.strip().lower().replace("ё", "е")

    async def city_score(self, city: str) -> tuple[int, str, str] | None:
        """(score 0-10, label, url) for a mapped city, or None."""
        key = self._normalise(city)
        geo = _CITY_GEO.get(key)
        if geo is None:
            return None
        geo_id, slug = geo
        url = f"https://yandex.ru/maps/{geo_id}/{slug}/?l=trf"

        text = await self._browser.render(url)
        if not text:
            return None
        m = _SCORE_RE.search(text)
        if not m:
            return None
        score = int(m.group(1))
        return score, _label(score), url
