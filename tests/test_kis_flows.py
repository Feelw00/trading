"""KIS 클라이언트·수급(flows) 수집 — 토큰 캐시·TR 호출·FlowStore·FactPack 통합.

픽스처 필드는 2026-06-11 실호출 응답에서 발췌(관측 기반 — 추측 아님).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.collectors.base import CollectError
from trading.collectors.flows import (
    FlowStore,
    collect,
    intraday_lines,
    latest_settled_bas_dt,
    report_lines,
)
from trading.collectors.kis import (
    TR_INVESTOR_BY_MARKET,
    TR_INVESTOR_BY_STOCK,
    TR_INVESTOR_INTRADAY,
    KisClient,
)

KST = ZoneInfo("Asia/Seoul")

# 실응답(2026-06-11, 삼성전자 20260610) 핵심 필드 발췌
STOCK_ROW: dict[str, Any] = {
    "stck_bsop_date": "20260610",
    "stck_clpr": "302500",
    "frgn_ntby_qty": "-3840270",
    "prsn_ntby_qty": "6424717",
    "orgn_ntby_qty": "-2851841",
    "frgn_ntby_tr_pbmn": "-1169229",
    "prsn_ntby_tr_pbmn": "1950408",
    "orgn_ntby_tr_pbmn": "-862333",
    "fund_ntby_tr_pbmn": "-67884",
}
MARKET_ROW: dict[str, Any] = {
    "stck_bsop_date": "20260610",
    "bstp_nmix_prpr": "7730.82",
    "frgn_ntby_qty": "-91587",
    "prsn_ntby_qty": "176917",
    "orgn_ntby_qty": "-82454",
    "frgn_ntby_tr_pbmn": "-2775389",
    "prsn_ntby_tr_pbmn": "4864291",
    "orgn_ntby_tr_pbmn": "-2266525",
    "fund_ntby_tr_pbmn": "-264189",
}
TOKEN_RESP = {
    "access_token": "tok-1",
    "access_token_token_expired": "2099-01-01 00:00:00",
    "token_type": "Bearer",
    "expires_in": 86400,
}


class _FakeHttp:
    """주입형 HTTP — 호출 기록 + URL별 응답."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self, url: str, data: bytes | None, headers: dict[str, str], timeout: float
    ) -> bytes:
        self.calls.append((url, headers))
        for frag, resp in self.responses.items():
            if frag in url:
                return json.dumps(resp).encode()
        raise AssertionError(f"unexpected url: {url}")


def _client(tmp_path: Path, http: _FakeHttp) -> KisClient:
    return KisClient(
        "app-key", "app-secret",
        token_cache=tmp_path / "token.json", http=http, sleeper=lambda _s: None,
    )


def test_token_issued_once_and_cached(tmp_path: Path) -> None:
    http = _FakeHttp({"/oauth2/tokenP": TOKEN_RESP})
    c = _client(tmp_path, http)
    assert c.token() == "tok-1"
    assert c.token() == "tok-1"
    assert len(http.calls) == 1  # 두 번째는 메모리 캐시

    # 새 클라이언트는 파일 캐시 재사용(재발급 없음 — 알림톡·동일토큰 정책)
    http2 = _FakeHttp({})
    c2 = _client(tmp_path, http2)
    assert c2.token() == "tok-1"
    assert http2.calls == []


def test_expired_cache_reissues(tmp_path: Path) -> None:
    (tmp_path / "token.json").write_text(
        json.dumps({"access_token": "old", "access_token_token_expired": "2020-01-01 00:00:00"})
    )
    http = _FakeHttp({"/oauth2/tokenP": TOKEN_RESP})
    assert _client(tmp_path, http).token() == "tok-1"
    assert len(http.calls) == 1


def test_investor_flows_by_stock_parses_output2(tmp_path: Path) -> None:
    http = _FakeHttp({
        "/oauth2/tokenP": TOKEN_RESP,
        "investor-trade-by-stock-daily": {"rt_cd": "0", "output2": [STOCK_ROW]},
    })
    rows = _client(tmp_path, http).investor_flows_by_stock("005930", "20260610")
    assert rows == [STOCK_ROW]
    url, headers = http.calls[-1]
    assert headers["tr_id"] == TR_INVESTOR_BY_STOCK
    assert headers["custtype"] == "P"
    assert "FID_INPUT_ISCD=005930" in url


def test_investor_flows_by_market_param_pairs(tmp_path: Path) -> None:
    http = _FakeHttp({
        "/oauth2/tokenP": TOKEN_RESP,
        "inquire-investor-daily-by-market": {"rt_cd": "0", "output": [MARKET_ROW]},
    })
    c = _client(tmp_path, http)
    assert c.investor_flows_by_market("KOSPI", "20260610") == [MARKET_ROW]
    url, headers = http.calls[-1]
    assert headers["tr_id"] == TR_INVESTOR_BY_MARKET
    assert "FID_INPUT_ISCD=0001" in url and "FID_INPUT_ISCD_1=KSP" in url
    c.investor_flows_by_market("KOSDAQ", "20260610")
    url, _ = http.calls[-1]
    assert "FID_INPUT_ISCD=1001" in url and "FID_INPUT_ISCD_1=KSQ" in url
    with pytest.raises(ValueError):
        c.investor_flows_by_market("NASDAQ", "20260610")


def test_rt_cd_error_raises_collect_error(tmp_path: Path) -> None:
    http = _FakeHttp({
        "/oauth2/tokenP": TOKEN_RESP,
        "investor-trade-by-stock-daily": {"rt_cd": "1", "msg1": "기간이 올바르지 않습니다"},
    })
    with pytest.raises(CollectError):
        _client(tmp_path, http).investor_flows_by_stock("005930", "20260610")


def test_account_type_validation() -> None:
    with pytest.raises(ValueError):
        KisClient("k", "s", account_type="PAPER")


# --- FlowStore ---


def test_flow_store_idempotent_and_recent(tmp_path: Path) -> None:
    store = FlowStore(tmp_path / "flows.sqlite")
    rows = [STOCK_ROW, {**STOCK_ROW, "stck_bsop_date": "20260609"}]
    assert store.upsert("stock", "005930", "삼성전자", rows) == 2
    assert store.upsert("stock", "005930", "삼성전자", rows) == 0  # 멱등
    assert store.upsert("stock", "005930", "삼성전자", [{"no_date": "x"}]) == 0  # bas_dt 없으면 버림
    recent = store.recent_for("stock", "005930", limit=1)
    assert recent == [("20260610", "1950408", "-1169229", "-862333", "-67884")]
    assert store.latest_date() == "20260610"
    assert store.count() == 2
    store.close()


def test_collect_continues_on_partial_failure(tmp_path: Path) -> None:
    class _HalfBroken:
        def investor_flows_by_market(self, market: str, bas_dt: str) -> list[dict[str, Any]]:
            if market == "KOSDAQ":
                raise CollectError("down")
            return [MARKET_ROW]

        def investor_flows_by_stock(self, code: str, bas_dt: str) -> list[dict[str, Any]]:
            return [STOCK_ROW]

    store = FlowStore(tmp_path / "flows.sqlite")
    result = collect(_HalfBroken(), store, [("005930", "삼성전자")], "20260610")  # type: ignore[arg-type]
    assert result["KOSPI"] == 1 and result["KOSDAQ"] == -1 and result["005930"] == 1
    store.close()


def test_report_lines_summary_and_units(tmp_path: Path) -> None:
    store = FlowStore(tmp_path / "flows.sqlite")
    assert report_lines(store) == ["수급 데이터 없음 — collect-flows 미실행(추측 금지)"]
    store.upsert("market", "KOSPI", "KOSPI", [MARKET_ROW])
    store.upsert("stock", "005930", "삼성전자", [STOCK_ROW])
    lines = report_lines(store)
    assert lines[0].startswith("투자자별 거래실적 as_of=20260610")
    # 백만원→억원: 4864291백만원 = +48,643억 / 연기금 -264189 = -2,642억
    # 기관(연기금外) = 기관계 -2266525 − 기금 -264189 = -2002336백만원 = -20,023억
    kospi = next(ln for ln in lines if "KOSPI" in ln)
    assert "개인 +48,643" in kospi and "연기금 -2,642" in kospi and "기관(연기금外) -20,023" in kospi
    assert any("KOSDAQ" in ln and "데이터 없음" in ln for ln in lines)  # 미수집은 결측 명시
    samsung = next(ln for ln in lines if "삼성전자" in ln)
    assert "외국인 -11,692" in samsung and "연기금 -679" in samsung and "기관(연기금外) -7,944" in samsung
    # 기금 필드 없는 행은 분리하지 않고 기관계만(임의 산술 금지)
    store.upsert("stock", "000001", "기금없는종목", [{
        "stck_bsop_date": "20260610", "prsn_ntby_tr_pbmn": "100",
        "frgn_ntby_tr_pbmn": "-100", "orgn_ntby_tr_pbmn": "200",
    }])
    nofund = next(ln for ln in report_lines(store) if "기금없는종목" in ln)
    assert "기관계 +2" in nofund and "연기금 분리불가" in nofund
    store.close()


def test_intraday_param_pairs_and_parse(tmp_path: Path) -> None:
    http = _FakeHttp({
        "/oauth2/tokenP": TOKEN_RESP,
        "inquire-investor-time-by-market": {"rt_cd": "0", "output": [MARKET_ROW]},
    })
    c = _client(tmp_path, http)
    assert c.investor_flows_intraday("KOSPI") == MARKET_ROW
    url, headers = http.calls[-1]
    assert headers["tr_id"] == TR_INVESTOR_INTRADAY
    assert "FID_INPUT_ISCD=999" in url and "FID_INPUT_ISCD_2=S001" in url
    c.investor_flows_intraday("KOSDAQ")
    url, _ = http.calls[-1]
    assert "FID_INPUT_ISCD_2=S101" in url
    with pytest.raises(ValueError):
        c.investor_flows_intraday("NIKKEI")


def test_latest_settled_bas_dt_calendar_based() -> None:
    """수급 기준일은 캘린더 기준 — 마감(15:40)후=당일, 장중·장전=직전 거래일, 주말=금요일."""
    after_close = datetime(2026, 6, 11, 16, 5, tzinfo=KST)   # 목 마감 후
    in_session = datetime(2026, 6, 11, 10, 0, tzinfo=KST)    # 목 장중
    pre_open = datetime(2026, 6, 11, 8, 0, tzinfo=KST)       # 목 장전
    saturday = datetime(2026, 6, 13, 12, 0, tzinfo=KST)      # 토
    assert latest_settled_bas_dt(after_close) == "20260611"
    assert latest_settled_bas_dt(in_session) == "20260610"
    assert latest_settled_bas_dt(pre_open) == "20260610"
    assert latest_settled_bas_dt(saturday) == "20260612"     # 금요일


def test_intraday_lines_session_gate() -> None:
    class _FakeKis:
        def investor_flows_intraday(self, market: str) -> dict[str, Any]:
            if market == "KOSDAQ":
                raise CollectError("down")
            return dict(MARKET_ROW)

    fake = _FakeKis()
    in_session = datetime(2026, 6, 11, 10, 30, tzinfo=KST)   # 목요일 장중
    off_session = datetime(2026, 6, 11, 18, 0, tzinfo=KST)   # 마감 후
    weekend = datetime(2026, 6, 13, 10, 30, tzinfo=KST)      # 토요일

    lines = intraday_lines(fake, now=in_session)  # type: ignore[arg-type]
    assert lines[0].startswith("[당일 잠정] 2026-06-11 10:30")
    assert any("KOSPI" in ln and "연기금 -2,642" in ln for ln in lines)
    assert any("KOSDAQ" in ln and "조회 실패" in ln for ln in lines)  # 실패 격리
    assert intraday_lines(fake, now=off_session) == []  # type: ignore[arg-type]
    assert intraday_lines(fake, now=weekend) == []  # type: ignore[arg-type]


# --- FactPack 통합 ---


def test_fact_pack_includes_flows(tmp_path: Path) -> None:
    from trading.collectors.market import MarketStore
    from trading.factpack import build_fact_pack
    from trading.screener import Candidate, SignalSet

    sig = SignalSet(tr_value_surge=1.0, mom_short=0.0, mom_long=0.0, high_proximity=0.9)
    cand = Candidate(
        srtn_cd="003220", name="대원제약", market="KOSPI", clpr=18000.0, score=0.9, signals=sig
    )
    mstore = MarketStore(tmp_path / "m.sqlite")
    mstore.upsert(
        [{"basDt": "20260608", "srtnCd": "003220", "itmsNm": "대원제약", "mrktCtg": "KOSPI",
          "clpr": "18000", "mrktTotAmt": "606374887932"}]
    )
    flow_store = FlowStore(tmp_path / "flows.sqlite")
    flow_store.upsert("stock", "003220", "대원제약", [{**STOCK_ROW, "stck_bsop_date": "20260608"}])

    class _NoDart:
        def disclosures(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, object]]:
            return []

        def financials(
            self, corp_code: str, bsns_year: str, reprt_code: str
        ) -> list[dict[str, object]]:
            return []

    pack = build_fact_pack(cand, mstore, _NoDart(), {}, [], None, flow_store)
    assert len(pack.flows) == 1
    line = pack.flows[0]
    assert line.bas_dt == "20260608"
    assert line.prsn_ntby_mn == 1950408.0
    assert line.frgn_ntby_mn == -1169229.0
    assert line.fund_ntby_mn == -67884.0  # 기금(연기금) — raw_json에서 추출
    assert pack.sources["flows"].startswith("flows.sqlite")
    assert all("수급 미수집" not in n for n in pack.notes)

    empty = build_fact_pack(cand, mstore, _NoDart(), {}, [], None, None)
    assert empty.flows == []
    assert any("수급 미수집" in n for n in empty.notes)
    flow_store.close()
    mstore.close()
