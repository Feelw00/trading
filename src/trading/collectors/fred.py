"""FRED(세인트루이스 연은) 어댑터 — 해외지수·유가 최신 관측값.

요청형식(공식): GET ``/fred/series/observations?series_id=..&api_key=..&file_type=json&sort_order=desc&limit=1``.
시리즈ID는 COLLECT-2 확정: SP500 / NASDAQCOM / NASDAQSOX / DCOILWTICO / DCOILBRENTEU.
값 결측은 ``"."`` 마커 → None.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from trading.collectors.base import fetch_json

FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"

Fetch = Callable[[str], Any]


def _real_fetch(url: str) -> Any:
    return fetch_json(url)


class FredClient:
    def __init__(self, api_key: str, *, fetch: Fetch = _real_fetch) -> None:
        self._key = api_key
        self._fetch = fetch

    def latest(self, series_id: str) -> tuple[str, str] | None:
        """(date, value) 최신 관측. 값 없음('.')이면 None."""
        query = urlencode(
            {
                "series_id": series_id,
                "api_key": self._key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": "1",
            }
        )
        data = self._fetch(f"{FRED_OBS}?{query}")
        obs = data.get("observations") if isinstance(data, dict) else None
        if not obs:
            return None
        first = obs[0]
        value = first.get("value")
        if value in (None, "", "."):
            return None
        return str(first.get("date")), str(value)


__all__ = ["FredClient"]
