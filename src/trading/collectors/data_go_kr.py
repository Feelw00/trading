"""공공데이터포털 금융위 지수시세정보 어댑터 — 국내지수(KOSPI/KOSDAQ) 일별 종가.

요청형식(공식, 실호출 확인 2026-06-08):
  GET .../GetMarketIndexInfoService/getStockMarketIndex
    ?serviceKey=..&resultType=json&numOfRows=..&idxNm=코스피&beginBasDt=YYYYMMDD&endBasDt=YYYYMMDD
응답: response.body.items.item[] 의 basDt/idxNm/clpr(종가)/fltRt 등. header.resultCode "00"=정상.
- serviceKey는 Decoding 키를 URL 인코딩해 전달.
- 갱신: EOD, 기준일자 +1영업일 13시 이후(금요일분은 월요일). → 최신 basDt가 당일이 아닐 수 있음(정상).
- items.item: 결과 1건이면 dict, 다건이면 list(공공데이터포털 공통 특성) — 둘 다 처리.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from trading.collectors.base import CollectError, fetch_json

INDEX_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
)

Fetch = Callable[[str], Any]


def _real_fetch(url: str) -> Any:
    return fetch_json(url)


class DataGoKrIndexClient:
    def __init__(self, service_key: str, *, fetch: Fetch = _real_fetch) -> None:
        self._key = service_key
        self._fetch = fetch

    def latest(self, idx_nm: str, begin: str, end: str) -> tuple[str, str] | None:
        """(basDt, clpr) — 윈도우 내 idxNm 정확일치 중 최신 종가. resultCode≠00은 CollectError."""
        params = {
            "serviceKey": self._key,
            "resultType": "json",
            "numOfRows": "30",
            "pageNo": "1",
            "idxNm": idx_nm,
            "beginBasDt": begin,
            "endBasDt": end,
        }
        data = self._fetch(f"{INDEX_ENDPOINT}?{urlencode(params)}")
        resp = data.get("response", {}) if isinstance(data, dict) else {}
        code = resp.get("header", {}).get("resultCode")
        if code not in (None, "00"):
            raise CollectError(f"data.go.kr {code}: {resp.get('header', {}).get('resultMsg')}")
        items = resp.get("body", {}).get("items")
        item = items.get("item") if isinstance(items, dict) else None
        if not item:
            return None
        rows = item if isinstance(item, list) else [item]
        valid = [
            r
            for r in rows
            if isinstance(r, dict) and r.get("idxNm") == idx_nm and r.get("clpr") not in (None, "")
        ]
        if not valid:
            return None
        latest = max(valid, key=lambda r: str(r.get("basDt", "")))
        return str(latest.get("basDt")), str(latest.get("clpr"))


__all__ = ["DataGoKrIndexClient"]
