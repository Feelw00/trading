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
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        *,
        page_count: int = 100,
        pblntf_ty: str | None = None,
        page_no: int | None = None,
    ) -> list[dict[str, Any]]:
        """[bgn_de~end_de] 공시 목록(YYYYMMDD). 데이터 없으면 빈 리스트.

        실호출 확인 2026-09-01: ``pblntf_ty="B"``(주요사항보고)로 필터하면 분할·합병 등
        주요사항보고서만 온다(LG화학 2020~21: 전체 181건 → B 2건). 응답은 페이징
        (total_page/page_no) — 넓은 창 조회는 ``disclosures_all``을 쓸 것."""
        params = {
            "crtfc_key": self._key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_count": str(page_count),
        }
        if pblntf_ty is not None:
            params["pblntf_ty"] = pblntf_ty
        if page_no is not None:
            params["page_no"] = str(page_no)
        return self._rows(self._json(f"{DART_BASE}/list.json?{urlencode(params)}"))

    def disclosures_all(
        self, corp_code: str, bgn_de: str, end_de: str, *, pblntf_ty: str | None = None
    ) -> list[dict[str, Any]]:
        """공시 목록 전 페이지 순회 — total_page 기준(실호출 확인 2026-09-01)."""
        params = {
            "crtfc_key": self._key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_count": "100",
        }
        if pblntf_ty is not None:
            params["pblntf_ty"] = pblntf_ty
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._json(f"{DART_BASE}/list.json?{urlencode({**params, 'page_no': str(page)})}")
            out.extend(self._rows(data))
            total = int(data.get("total_page") or 1) if isinstance(data, dict) else 1
            if page >= total:
                return out
            page += 1

    def alot_matter(
        self, corp_code: str, bsns_year: str, reprt_code: str = "11011"
    ) -> list[dict[str, Any]]:
        """배당에 관한 사항(alotMatter) — 실호출 관측 확정(2026-09-01, 삼성전자 2025):
        행 필드 se(지표명: "주당 현금배당금(원)"·"현금배당수익률(%)"·"(연결)현금배당성향(%)" 등)·
        stock_knd(보통주/우선주/'-')·thstrm/frmtrm/lwfr(쉼표 천단위 문자열, 결측 '-')."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            }
        )
        return self._rows(self._json(f"{DART_BASE}/alotMatter.json?{q}"))

    def treasury_stock(
        self, corp_code: str, bsns_year: str, reprt_code: str = "11011"
    ) -> list[dict[str, Any]]:
        """자기주식 취득·처분 현황(tesstkAcqsDspsSttus) — 실호출 관측 확정(2026-09-01,
        삼성전자 2025): 행 필드 acqs_mth1/2/3(취득 방법 계층)·stock_knd·bsis_qy(기초)·
        change_qy_acqs(취득)·change_qy_dsps(처분)·change_qy_incnr(소각)·trmend_qy(기말),
        수량은 쉼표 천단위 문자열·결측 '-'."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            }
        )
        return self._rows(self._json(f"{DART_BASE}/tesstkAcqsDspsSttus.json?{q}"))

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

    def financials_all(
        self, corp_code: str, bsns_year: str, reprt_code: str, fs_div: str
    ) -> list[dict[str, Any]]:
        """단일회사 전체 재무제표(fnlttSinglAcntAll) — 실호출 관측 확정(2026-09-01,
        KG케미칼 2025 CFS 293행): 행 필드 sj_div(BS/IS/…)·account_id(IFRS 태그,
        예: ifrs-full_EquityAttributableToOwnersOfParent="지배기업 소유주지분")·
        account_nm·thstrm_amount. fs_div 필수(CFS/OFS)."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            }
        )
        return self._rows(self._json(f"{DART_BASE}/fnlttSinglAcntAll.json?{q}"))

    def stock_totals(
        self, corp_code: str, bsns_year: str, reprt_code: str = "11011"
    ) -> list[dict[str, Any]]:
        """주식의 총수 현황(stockTotqySttus) — 실호출 관측 확정(2026-08-31, P-17 ①):
        행 필드 se(보통주/우선주/합계)·istc_totqy(발행주식 총수)·tesstk_co(자기주식)·
        distb_stock_co(유통주식)·stlm_dt(결산기준일, 쉼표 천단위·결측 '-')."""
        q = urlencode(
            {
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            }
        )
        return self._rows(self._json(f"{DART_BASE}/stockTotqySttus.json?{q}"))


__all__ = ["DartClient"]
