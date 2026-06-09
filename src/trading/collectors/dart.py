"""DART OpenDART 어댑터 — 전자공시·재무(후보 전망 분석의 "현실 데이터").

요청형식(공식, 실호출 확인 2026-06-08):
  corpCode.xml  : 전 상장사 단축코드↔corp_code 매핑(ZIP→XML). DART는 자체 corp_code 사용.
  list.json     : 공시 목록(corp_code, bgn_de~end_de). 필드 report_nm/rcept_dt/rcept_no/flr_nm 등.
  fnlttSinglAcnt.json : 단일회사 주요계정 재무(연결/별도, 당기 thstrm_amount/전기 frmtrm_amount).
  company.json  : 회사개황(단일 객체, list 아님). induty_code=KSIC 업종코드(3~5자리)·corp_cls 등.
응답 status: "000"=정상, "013"=데이터없음(→빈), 그 외(020 한도초과·100 키오류 등)=CollectError.
무료·공개(설계서 🟢). LLM은 여기서 가져온 실데이터만 근거로 판단(추측 금지).
"""

import io
import zipfile
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from trading.collectors.base import CollectError, fetch_bytes, fetch_json

DART_BASE = "https://opendart.fss.or.kr/api"

JsonFetch = Callable[[str], Any]
BytesFetch = Callable[[str], bytes]


def _real_json(url: str) -> Any:
    return fetch_json(url)


def _real_bytes(url: str) -> bytes:
    return fetch_bytes(url)


class DartClient:
    def __init__(
        self, api_key: str, *, json_fetch: JsonFetch = _real_json, bytes_fetch: BytesFetch = _real_bytes
    ) -> None:
        self._key = api_key
        self._json = json_fetch
        self._bytes = bytes_fetch

    def corp_code_map(self) -> dict[str, tuple[str, str]]:
        """단축코드 → (corp_code, corp_name). 상장사만(stock_code 있는 것)."""
        raw = self._bytes(f"{DART_BASE}/corpCode.xml?crtfc_key={self._key}")
        if raw[:2] != b"PK":
            raise CollectError("DART corpCode: ZIP 응답 아님(키/한도 확인)")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
        out: dict[str, tuple[str, str]] = {}
        for el in root.iter("list"):
            sc = (el.findtext("stock_code") or "").strip()
            if sc:
                out[sc] = (el.findtext("corp_code") or "", el.findtext("corp_name") or "")
        return out

    @staticmethod
    def _rows(data: Any) -> list[dict[str, Any]]:
        status = data.get("status") if isinstance(data, dict) else None
        if status == "000":
            rows = data.get("list")
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        if status == "013":  # 조회된 데이터 없음
            return []
        raise CollectError(f"DART {status}: {data.get('message') if isinstance(data, dict) else ''}")

    def disclosures(
        self, corp_code: str, bgn_de: str, end_de: str, *, page_count: int = 100
    ) -> list[dict[str, Any]]:
        """[bgn_de~end_de] 공시 목록(YYYYMMDD). 데이터 없으면 빈 리스트."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_count": str(page_count),
            }
        )
        return self._rows(self._json(f"{DART_BASE}/list.json?{q}"))

    def company_profile(self, corp_code: str) -> dict[str, Any]:
        """회사개황(업종코드 induty_code·corp_cls 등). 단일 객체 응답. 데이터 없으면 빈 dict.

        실호출 확인 2026-06-09: company.json은 list가 아닌 단일 dict(status+필드 평면).
        induty_code는 KSIC 등록업종(법적)이라 다각화·테마와 어긋날 수 있음 — 매핑은 ``sectors`` 참조.
        """
        q = urlencode({"crtfc_key": self._key, "corp_code": corp_code})
        data = self._json(f"{DART_BASE}/company.json?{q}")
        status = data.get("status") if isinstance(data, dict) else None
        if status == "000":
            return data  # type: ignore[no-any-return]
        if status == "013":  # 조회된 데이터 없음
            return {}
        raise CollectError(f"DART {status}: {data.get('message') if isinstance(data, dict) else ''}")

    def financials(self, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict[str, Any]]:
        """단일회사 주요계정(reprt_code: 11011 사업/11012 반기/11013 1분기/11014 3분기)."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            }
        )
        return self._rows(self._json(f"{DART_BASE}/fnlttSinglAcnt.json?{q}"))


__all__ = ["DartClient"]
