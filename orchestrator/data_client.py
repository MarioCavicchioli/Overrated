"""Client for the data service (the mock in data/mock, or person 1's real one).

The base URL comes from DATA_SERVICE_URL, so switching services is one variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

TIMEOUT_S = float(os.getenv("DATA_SERVICE_TIMEOUT_S", "10"))


class NotFound(Exception):
    pass


class DataClient:
    def __init__(self, base_url: str | None = None):
        base_url = base_url or os.getenv("DATA_SERVICE_URL")
        if not base_url:
            raise RuntimeError("DATA_SERVICE_URL is not set")
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT_S)

    def _get(self, path: str, **params: Any) -> Any:
        params = {k: (str(v).lower() if isinstance(v, bool) else v)
                  for k, v in params.items() if v is not None}
        r = self.http.get(path, params=params)
        if r.status_code == 404:
            raise NotFound(r.json().get("detail", "not found"))
        r.raise_for_status()
        return r.json()

    def search_properties(self, street: str | None = None, use: str | None = None,
                          postcode: str | None = None, limit: int | None = None) -> list[dict]:
        return self._get("/search_properties", street=street, use=use,
                         postcode=postcode, limit=limit)

    def get_valuation(self, uarn: str) -> dict:
        return self._get(f"/get_valuation/{uarn}")

    def comparables(self, uarn: str, max_distance: float | None = None,
                    same_use: bool | None = None, limit: int | None = None) -> list[dict]:
        return self._get(f"/comparables/{uarn}", max_distance=max_distance,
                         same_use=same_use, limit=limit)

    def street_tone(self, street_key: str) -> dict:
        return self._get(f"/street_tone/{street_key}")
