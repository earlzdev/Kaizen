# =============================================================================
# Tracker HTTP client — brain/tracker_client.py
# =============================================================================
# WHAT: A thin read-only client to the tracker Hub's HTTP API, used by the
#       mobile dashboard (GET /admin/tracker/*) to proxy tracker data through
#       Brain. Mirrors the style of _backup_proxy in brain/server.py.
#
# WHY a proxy through Brain instead of the phone calling tracker directly:
#       the phone should only ever hold ONE credential — Brain's admin token.
#       Tracker's own admin token (settings.tracker_admin_token) stays
#       server-side in Brain's config and is never shipped to the browser.
#
# WHY this raises on failure instead of swallowing errors like Notifier does:
#       Notifier is fire-and-forget (a missed "PR is ready" ping isn't fatal).
#       A dashboard load is a request the owner is actively waiting on — it
#       must surface "tracker unreachable", not hang or silently show nothing.
# =============================================================================

import asyncio

import aiohttp


class TrackerUnreachable(Exception):
    """The tracker's HTTP API could not be reached or returned an error."""


class TrackerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 8.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def get(self, path: str, params: dict | None = None) -> dict | list:
        url = self._base_url + path
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        raise TrackerUnreachable(f"tracker returned HTTP {resp.status}")
                    return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # aiohttp raises a bare asyncio.TimeoutError (not a ClientError
            # subclass) when the total timeout fires — a wedged tracker must
            # 503 the same as a refused connection, not 500.
            raise TrackerUnreachable(f"tracker unreachable: {e}") from e
