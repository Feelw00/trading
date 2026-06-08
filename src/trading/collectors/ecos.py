"""한국은행 ECOS 어댑터 — 금리·환율 최신값.

요청형식(공식): GET ``/api/StatisticSearch/{KEY}/json/kr/1/100/{통계표코드}/{주기}/{검색시작}/{검색종료}/{항목코드}``.
응답: ``StatisticSearch.row[]`` 의 TIME/DATA_VALUE/UNIT_NAME. 오류는 ``RESULT.CODE/MESSAGE``.

주의(CLAUDE.md rule #1 / OPEN_QUESTIONS COLLECT-2): **통계표·항목 코드는 미확정**이라
이 어댑터에 하드코딩하지 않는다. 코드는 ``macro.py`` 레지스트리에서 주입하며, 확정 전엔
호출 자체를 하지 않는다(blocked). 잘못된 코드는 ECOS RESULT 에러 → CollectError로 드러난다.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from trading.collectors.base import CollectError, fetch_json

ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

Fetch = Callable[[str], Any]


def _real_fetch(url: str) -> Any:
    return fetch_json(url)


class EcosClient:
    def __init__(self, api_key: str, *, fetch: Fetch = _real_fetch) -> None:
        self._key = api_key
        self._fetch = fetch

    def latest(
        self, stat_code: str, item_code: str, cycle: str, start: str, end: str
    ) -> tuple[str, str, str] | None:
        """(TIME, DATA_VALUE, UNIT_NAME) — 윈도우 내 최신 유효값. RESULT 에러는 CollectError."""
        parts = [self._key, "json", "kr", "1", "100", stat_code, cycle, start, end, item_code]
        url = ECOS_BASE + "/" + "/".join(quote(p, safe="") for p in parts)
        data = self._fetch(url)
        if isinstance(data, dict) and "RESULT" in data:
            res = data["RESULT"]
            raise CollectError(f"ECOS {res.get('CODE')}: {res.get('MESSAGE')}")
        rows = data.get("StatisticSearch", {}).get("row") if isinstance(data, dict) else None
        if not rows:
            return None
        valid = [r for r in rows if r.get("DATA_VALUE") not in (None, "", "-")]
        if not valid:
            return None
        latest = max(valid, key=lambda r: str(r.get("TIME", "")))
        return (
            str(latest.get("TIME")),
            str(latest.get("DATA_VALUE")),
            str(latest.get("UNIT_NAME") or ""),
        )


__all__ = ["EcosClient"]
