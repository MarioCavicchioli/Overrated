"""Clients for the data service. Pick one with make_data_client().

DATA_BACKEND=mock (default): data/mock, the fake Hackney streets, for tests and demos.
DATA_BACKEND=http: Person 1's data service over the real VOA lists (data/service.py),
  at DATA_SERVICE_URL, with the X-API-Key header from DATA_SERVICE_KEY.

Both return the same normalised shapes, so the builder, the advocate and their
code checks don't care which is behind them:

  valuation: uarn, address, postcode, street_key (the street segment), use (text),
             use_class (what "same use" compares: the VOA scheme code for real data),
             zone_a_price, rv, itza, floor_area_m2, zones [{zone, area_m2, price_per_m2}]
  comparable: the same identity fields plus distance_m (postcode centres for real data)
  tone:       street_key, count, median_zone_a, mean_zone_a, mad, spread
              (spread = 1.4826 x MAD, at least 5% of the median, everywhere)

The API key is sent as a header only; it is never logged or put in an error.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

TIMEOUT_S = float(os.getenv("DATA_SERVICE_TIMEOUT_S") or "20")


class NotFound(Exception):
    pass


def segment(value: Any) -> str:
    """One URL path segment. UARNs and street keys can come from an agent's tool call,
    so "/", "?", "#" and "%" are encoded (quote with safe=""), and so are dots: quote
    leaves them, and a bare ".." segment would be resolved away by the URL parser,
    reaching a different endpoint on the data service."""
    return quote(str(value), safe="").replace(".", "%2E")


class DataClient:
    """The mock data service (data/mock)."""

    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None):
        if http is not None:
            self.http = http
            return
        base_url = base_url or os.getenv("DATA_SERVICE_URL")
        if not base_url:
            raise RuntimeError("DATA_SERVICE_URL is not set")
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT_S)

    def _get(self, path: str, **params: Any) -> Any:
        params = {k: (str(v).lower() if isinstance(v, bool) else v)
                  for k, v in params.items() if v is not None and v != ""}
        r = self.http.get(path, params=params)
        if r.status_code == 404:
            try:
                detail = r.json().get("detail", "not found")
            except ValueError:
                detail = "not found"
            raise NotFound(detail)
        if r.status_code >= 400:
            # Never include request headers (the API key) in the error.
            raise RuntimeError(f"data service: HTTP {r.status_code} on {path}")
        return r.json()

    def search_properties(self, street: str | None = None, use: str | None = None,
                          postcode: str | None = None, limit: int | None = None) -> list[dict]:
        return self._get("/search_properties", street=street, use=use,
                         postcode=postcode, limit=limit)

    def get_valuation(self, uarn: str) -> dict:
        return self._get(f"/get_valuation/{segment(uarn)}")

    def comparables(self, uarn: str, max_distance: float | None = None,
                    same_use: bool | None = None, limit: int | None = None) -> list[dict]:
        return self._get(f"/comparables/{segment(uarn)}", max_distance=max_distance,
                         same_use=same_use, limit=limit)

    def street_tone(self, street_key: str) -> dict:
        return self._get(f"/street_tone/{segment(street_key)}")


# -- Person 1's data service -------------------------------------------------------

# Summary valuation line descriptions -> zone, as data/build.py reads them.
ZONE_LINES = {"Retail Zone A": "A", "Retail Zone B": "B", "Retail Zone C": "C",
              "Retail Zone D": "D", "Remaining Retail Zone": "D"}
ZONE_FRACTION = {"A": 1.0, "B": 0.5, "C": 0.25, "D": 0.125}
SEARCH_MAX, COMPARABLES_MAX = 100, 20   # the service's own caps


class VoaDataClient(DataClient):
    """Person 1's data service, normalised to the shapes above."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 http: httpx.Client | None = None):
        if http is None:
            base_url = base_url or os.getenv("DATA_SERVICE_URL")
            api_key = api_key or os.getenv("DATA_SERVICE_KEY")
            if not base_url or not api_key:
                raise RuntimeError("DATA_SERVICE_URL and DATA_SERVICE_KEY must be set")
            http = httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT_S,
                                headers={"X-API-Key": api_key})
        super().__init__(http=http)

    @staticmethod
    def _identity(shop: dict) -> dict:
        return {
            "uarn": str(shop["uarn"]),
            "address": shop.get("address"),
            "postcode": shop.get("postcode"),
            "street_key": shop.get("segment_key") or shop.get("street_key"),
            "street": shop.get("street_key"),
            "use": shop.get("use") or shop.get("desc_text"),
            "use_class": shop.get("scat_code"),
            "zone_a_price": shop.get("zone_a_price"),
            "rv": shop.get("rv"),
            "itza": shop.get("itza"),
        }

    def search_properties(self, street: str | None = None, use: str | None = None,
                          postcode: str | None = None, limit: int | None = None) -> list[dict]:
        rows = self._get("/search_properties", q=street, postcode=postcode,
                         limit=min(limit or 20, SEARCH_MAX))
        out = [{**self._identity(r), "street_key": r.get("street_key")} for r in rows]
        if use:
            want = use.lower()
            out = [r for r in out if want in (r["use"] or "").lower() or want == r["use_class"]]
        return out

    def get_valuation(self, uarn: str) -> dict:
        raw = self._get(f"/get_valuation/{segment(uarn)}")
        shop, entry = raw.get("shop"), raw["entry"]
        if shop is None:
            # On the list, but no zoned shop valuation: not something we can compare.
            raise NotFound(f"{uarn} has no zoned shop valuation")
        zones = []
        for line in raw.get("lines") or []:
            zone = ZONE_LINES.get(line.get("description"))
            if zone and line.get("area"):
                zones.append({"zone": zone, "area_m2": line["area"],
                              "price_per_m2": line.get("price")})
        summary = raw.get("summary") or {}
        return {
            **self._identity(shop),
            "use": entry.get("desc_text") or shop.get("desc_text"),
            "zones": zones,
            "floor_area_m2": summary.get("total_area") or shop.get("total_area"),
        }

    def comparables(self, uarn: str, max_distance: float | None = None,
                    same_use: bool | None = None, limit: int | None = None) -> list[dict]:
        raw = self._get(f"/comparables/{segment(uarn)}", n=COMPARABLES_MAX)
        subject_class = raw["subject"].get("scat_code")
        out = []
        for row in raw["comparables"]:
            if not row.get("same_segment"):
                continue                     # comparables come from the subject's street segment
            if same_use and row.get("scat_code", subject_class) != subject_class:
                continue
            dist = row.get("distance_m")
            if max_distance is not None and (dist is None or dist > max_distance):
                continue
            out.append({**self._identity(row), "distance_m": dist})
        # No distance when a postcode can't be located: those go last.
        out.sort(key=lambda c: c["distance_m"] if c["distance_m"] is not None else float("inf"))
        return out[:limit] if limit else out

    def street_tone(self, street_key: str) -> dict:
        raw = self._get("/street_tone", segment_key=street_key)
        seg = raw["segments"][0]
        t = seg["tone"]
        return {"street_key": seg["segment_key"], "count": t["n"],
                "median_zone_a": t["median"], "mean_zone_a": t.get("mean"),
                "mad": t.get("mad"), "spread": t["spread"]}


def make_data_client() -> DataClient:
    backend = os.getenv("DATA_BACKEND") or "mock"
    if backend == "mock":
        return DataClient()
    if backend == "http":
        return VoaDataClient()
    raise ValueError(f"unknown DATA_BACKEND {backend!r} (mock or http)")
