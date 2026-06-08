"""공공데이터포털 금융위 시세 어댑터 — 국내지수(지수시세) + 국내 종목(주식시세).

요청형식(공식, 실호출 확인 2026-06-08):
  지수: .../GetMarketIndexInfoService/getStockMarketIndex   (idxNm=코스피/코스닥)
  종목: .../GetStockSecuritiesInfoService/getStockPriceInfo (likeSrtnCd=단축코드)
공통 응답 봉투: response.header.resultCode("00"=정상) + response.body.items.item[].
  - serviceKey는 Decoding 키를 URL 인코딩해 전달.
  - 갱신: EOD, 기준일자 +1영업일 13시 이후(금요일분은 월요일). 최신 basDt가 당일이 아닐 수 있음(정상).
  - items.item: 1건이면 dict, 다건이면 list(공공데이터포털 공통 특성) — 둘 다 처리.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from trading.collectors.base import CollectError, fetch_json

INDEX_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
)
STOCK_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
)

Fetch = Callable[[str], Any]


def _real_fetch(url: str) -> Any:
    return fetch_json(url)


def _rows(data: Any) -> list[Any]:
    """공통 봉투 파싱 — resultCode≠00은 CollectError, items.item을 list로 정규화."""
    resp = data.get("response", {}) if isinstance(data, dict) else {}
    code = resp.get("header", {}).get("resultCode")
    if code not in (None, "00"):
        raise CollectError(f"data.go.kr {code}: {resp.get('header', {}).get('resultMsg')}")
    items = resp.get("body", {}).get("items")
    item = items.get("item") if isinstance(items, dict) else None
    if not item:
        return []
    return item if isinstance(item, list) else [item]


def _latest(rows: list[Any], match_key: str, match_val: str) -> dict[str, Any] | None:
    """match_key가 match_val과 정확히 일치하고 clpr 있는 행 중 최신 basDt."""
    valid = [
        r
        for r in rows
        if isinstance(r, dict) and r.get(match_key) == match_val and r.get("clpr") not in (None, "")
    ]
    if not valid:
        return None
    return max(valid, key=lambda r: str(r.get("basDt", "")))


class DataGoKrIndexClient:
    def __init__(self, service_key: str, *, fetch: Fetch = _real_fetch) -> None:
        self._key = service_key
        self._fetch = fetch

    def latest(self, idx_nm: str, begin: str, end: str) -> tuple[str, str] | None:
        """(basDt, clpr) — 윈도우 내 idxNm 정확일치 중 최신 종가."""
        params = {
            "serviceKey": self._key,
            "resultType": "json",
            "numOfRows": "30",
            "pageNo": "1",
            "idxNm": idx_nm,
            "beginBasDt": begin,
            "endBasDt": end,
        }
        row = _latest(_rows(self._fetch(f"{INDEX_ENDPOINT}?{urlencode(params)}")), "idxNm", idx_nm)
        if row is None:
            return None
        return str(row.get("basDt")), str(row.get("clpr"))


@dataclass(frozen=True)
class StockQuote:
    """종목 일별 시세(EOD). 값은 원시 문자열(타입화는 R2)."""

    bas_dt: str
    name: str
    market: str | None  # KOSPI | KOSDAQ ...
    clpr: str  # 종가
    flt_rt: str | None  # 등락률(%)
    mkp: str | None  # 시가
    hipr: str | None  # 고가
    lopr: str | None  # 저가
    trqu: str | None  # 거래량


class DataGoKrStockClient:
    def __init__(self, service_key: str, *, fetch: Fetch = _real_fetch) -> None:
        self._key = service_key
        self._fetch = fetch

    def latest(self, srtn_cd: str, begin: str, end: str) -> StockQuote | None:
        """단축코드(srtn_cd) 종목의 윈도우 내 최신 일별 시세. resultCode≠00은 CollectError."""
        params = {
            "serviceKey": self._key,
            "resultType": "json",
            "numOfRows": "30",
            "pageNo": "1",
            "likeSrtnCd": srtn_cd,
            "beginBasDt": begin,
            "endBasDt": end,
        }
        row = _latest(_rows(self._fetch(f"{STOCK_ENDPOINT}?{urlencode(params)}")), "srtnCd", srtn_cd)
        if row is None:
            return None
        return StockQuote(
            bas_dt=str(row.get("basDt")),
            name=str(row.get("itmsNm")),
            market=row.get("mrktCtg"),
            clpr=str(row.get("clpr")),
            flt_rt=row.get("fltRt"),
            mkp=row.get("mkp"),
            hipr=row.get("hipr"),
            lopr=row.get("lopr"),
            trqu=row.get("trqu"),
        )

    def all_by_date(self, bas_dt: str) -> list[dict[str, Any]]:
        """해당 거래일의 전 상장종목 행(원시 dict). 비거래일이면 빈 리스트. 전종목 ≈ 1콜."""
        params = {
            "serviceKey": self._key,
            "resultType": "json",
            "numOfRows": "5000",
            "pageNo": "1",
            "basDt": bas_dt,
        }
        rows = _rows(self._fetch(f"{STOCK_ENDPOINT}?{urlencode(params)}"))
        return [r for r in rows if isinstance(r, dict)]


__all__ = ["DataGoKrIndexClient", "DataGoKrStockClient", "StockQuote"]
