"""Thin async Taiga REST client.

The caller's own Taiga application token is passed on every call
(``Authorization: Application <token>``); nothing is stored. Only GET, POST
and PATCH are exposed: there is deliberately no way to issue a DELETE.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .errors import (AuthError, BadRequest, NotFound, PermissionDenied,
                     TaigaUnavailable, VersionConflict)

# What Taiga 6 answers to a PATCH carrying a stale version:
#   HTTP 400 {"version": "The version doesn't match with the current one"}
VERSION_CONFLICT_MARKER = "version doesn't match"


def is_version_conflict(status: int, body: Any) -> bool:
    return (
        status == 400
        and isinstance(body, dict)
        and isinstance(body.get("version"), str)
        and VERSION_CONFLICT_MARKER in body["version"]
    )


class TaigaClient:
    def __init__(self, base_url: str, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url + "/api/v1", timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, token: str, *, what: str,
                    params: dict | None = None, body: dict | None = None
                    ) -> httpx.Response:
        headers = {
            "Authorization": f"Application {token}",
            "x-disable-pagination": "True",
        }
        try:
            r = await self._http.request(method, path, params=params,
                                         json=body, headers=headers)
        except httpx.HTTPError as e:
            raise TaigaUnavailable(
                f"Cannot reach Taiga at {self.base_url} ({type(e).__name__}).") from e
        if r.status_code < 400:
            return r
        try:
            data = r.json()
        except ValueError:
            data = r.text[:200]
        if r.status_code == 401:
            raise AuthError(
                "Taiga rejected this agent's token (invalid, revoked or expired). "
                "Check the token in your Authorization header.")
        if r.status_code == 403:
            raise PermissionDenied(
                f"Permission denied: this agent's Taiga role does not allow "
                f"{what}. Ask the household admin to grant it.")
        if r.status_code == 404:
            raise NotFound(f"Not found while trying to {what}.")
        if is_version_conflict(r.status_code, data):
            raise VersionConflict(
                f"The item was changed by someone else while {what}.")
        if r.status_code == 400:
            raise BadRequest(f"Taiga refused the request to {what}: "
                             f"{json.dumps(data)[:400]}")
        raise TaigaUnavailable(f"Taiga returned HTTP {r.status_code} "
                               f"while trying to {what}.")

    async def get(self, path: str, token: str, *, what: str,
                  params: dict | None = None) -> Any:
        return (await self._send("GET", path, token, what=what, params=params)).json()

    async def get_list(self, path: str, token: str, *, what: str,
                       params: dict | None = None) -> list:
        """GET a collection, never silently truncated to one page."""
        params = dict(params or {})
        out: list = []
        page = 1
        while True:
            r = await self._send("GET", path, token, what=what, params=params)
            out.extend(r.json())
            # With x-disable-pagination Taiga sends everything and no next
            # link. If a proxy or version ignored it, follow the pages.
            if r.headers.get("x-pagination-next"):
                page += 1
                params["page"] = page
                continue
            return out

    async def post(self, path: str, token: str, body: dict, *, what: str) -> Any:
        return (await self._send("POST", path, token, what=what, body=body)).json()

    async def patch(self, path: str, token: str, body: dict, *, what: str) -> Any:
        return (await self._send("PATCH", path, token, what=what, body=body)).json()
