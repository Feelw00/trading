"""수집기 어댑터(base/FRED/ECOS/macro) 단위 테스트 — 네트워크 없이 주입 fetch로 검증."""

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.collectors.base import CollectedFact, CollectError, fetch_json, write_facts
from trading.collectors.data_go_kr import DataGoKrIndexClient, DataGoKrStockClient
from trading.collectors.ecos import EcosClient
from trading.collectors.fred import FredClient
from trading.collectors.macro import MacroItem, collect_macro
from trading.collectors.market import (
    MarketStore,
    heal_gaps,
    missing_trading_days,
    split_pending,
)
from trading.market_calendar.calendar import MarketCalendar

KST = ZoneInfo("Asia/Seoul")
FETCHED = datetime(2026, 6, 8, 15, 0, tzinfo=KST)


def test_fetch_json_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def opener(url: str, timeout: float) -> bytes:
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("일시 오류")
        return b'{"ok": true}'

    out = fetch_json("http://x", opener=opener, sleeper=lambda _s: None)
    assert out == {"ok": True}
    assert calls["n"] == 2


def test_fetch_json_passes_headers_to_opener() -> None:
    seen: dict[str, Any] = {}

    def opener(url: str, timeout: float, headers: dict[str, str] | None = None) -> bytes:
        seen["headers"] = headers
        return b'{"ok": true}'

    fetch_json("http://x", opener=opener, headers={"X-Naver-Client-Id": "id"})
    assert seen["headers"] == {"X-Naver-Client-Id": "id"}


def test_fetch_json_exhausts_to_collecterror() -> None:
    def opener(url: str, timeout: float) -> bytes:
        raise OSError("down")

    with pytest.raises(CollectError):
        fetch_json("http://x", opener=opener, sleeper=lambda _s: None, retries=2)


def test_collectedfact_rejects_naive_fetched_at() -> None:
    with pytest.raises(ValidationError):
        CollectedFact(
            cluster="c",
            name="n",
            metric="m",
            source="s",
            as_of="2026-06-08",
            fetched_at=datetime(2026, 6, 8, 15, 0),  # naive
        )


def test_write_facts_roundtrip(tmp_path: Path) -> None:
    fact = CollectedFact(
        cluster="macro_indicators",
        name="SOX",
        metric="index_level",
        source="FRED:NASDAQSOX",
        as_of="2026-06-04",
        fetched_at=FETCHED,
        value="5123.4",
        unit="pt",
        region="US",
        asset_class="index",
        verified=True,
    )
    db = tmp_path / "x.sqlite"
    assert write_facts(db, [fact]) == 1
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT name, value, verified FROM facts").fetchall()
    conn.close()
    assert rows == [("SOX", "5123.4", 1)]


def test_fred_latest_parses() -> None:
    def fetch(url: str) -> Any:
        assert "series_id=SP500" in url and "sort_order=desc" in url
        return {"observations": [{"date": "2026-06-04", "value": "5300.1"}]}

    assert FredClient("k", fetch=fetch).latest("SP500") == ("2026-06-04", "5300.1")


def test_fred_latest_missing_value() -> None:
    def fetch(url: str) -> Any:
        return {"observations": [{"date": "2026-06-04", "value": "."}]}

    assert FredClient("k", fetch=fetch).latest("DCOILWTICO") is None


def test_ecos_result_error_raises() -> None:
    def fetch(url: str) -> Any:
        return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "데이터 없음"}}

    with pytest.raises(CollectError):
        EcosClient("k", fetch=fetch).latest("731Y001", "0000001", "D", "20260101", "20260108")


def test_ecos_latest_picks_newest() -> None:
    def fetch(url: str) -> Any:
        return {
            "StatisticSearch": {
                "row": [
                    {"TIME": "20260605", "DATA_VALUE": "1531.8", "UNIT_NAME": "원"},
                    {"TIME": "20260608", "DATA_VALUE": "1553.2", "UNIT_NAME": "원"},
                ]
            }
        }

    res = EcosClient("k", fetch=fetch).latest("731Y001", "0000001", "D", "20260101", "20260108")
    assert res == ("20260608", "1553.2", "원")


def test_datagokr_latest_picks_newest() -> None:
    def fetch(url: str) -> Any:
        assert "idxNm=" in url and "beginBasDt=" in url
        return {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": [
                            {"basDt": "20260604", "idxNm": "코스피", "clpr": "8639.41"},
                            {"basDt": "20260605", "idxNm": "코스피", "clpr": "8160.59"},
                        ]
                    }
                },
            }
        }

    res = DataGoKrIndexClient("k", fetch=fetch).latest("코스피", "20260525", "20260609")
    assert res == ("20260605", "8160.59")


def test_datagokr_single_item_dict() -> None:
    def fetch(url: str) -> Any:
        return {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": {"basDt": "20260605", "idxNm": "코스닥", "clpr": "1002.44"}}},
            }
        }

    assert DataGoKrIndexClient("k", fetch=fetch).latest("코스닥", "20260525", "20260609") == (
        "20260605",
        "1002.44",
    )


def test_datagokr_stock_latest() -> None:
    def fetch(url: str) -> Any:
        assert "likeSrtnCd=005930" in url and "getStockPriceInfo" in url
        return {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": [
                            {"basDt": "20260604", "srtnCd": "005930", "itmsNm": "삼성전자",
                             "mrktCtg": "KOSPI", "clpr": "71000", "fltRt": "-2.1", "trqu": "12345678"},
                            {"basDt": "20260605", "srtnCd": "005930", "itmsNm": "삼성전자",
                             "mrktCtg": "KOSPI", "clpr": "70000", "fltRt": "-1.4", "trqu": "11111111"},
                        ]
                    }
                },
            }
        }

    q = DataGoKrStockClient("k", fetch=fetch).latest("005930", "20260525", "20260609")
    assert q is not None
    assert q.bas_dt == "20260605" and q.clpr == "70000"
    assert q.name == "삼성전자" and q.market == "KOSPI" and q.trqu == "11111111"


def test_datagokr_all_by_date() -> None:
    def fetch(url: str) -> Any:
        assert "basDt=20260605" in url
        return {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": [
                            {"basDt": "20260605", "srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "329000"},
                            {"basDt": "20260605", "srtnCd": "000660", "itmsNm": "SK하이닉스", "clpr": "2070000"},
                        ]
                    }
                },
            }
        }

    rows = DataGoKrStockClient("k", fetch=fetch).all_by_date("20260605")
    assert len(rows) == 2 and rows[0]["srtnCd"] == "005930"


def test_marketstore_upsert_idempotent(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    rows: list[dict[str, Any]] = [
        {"basDt": "20260605", "srtnCd": "005930", "itmsNm": "삼성전자", "mrktCtg": "KOSPI", "clpr": "329000"},
        {"basDt": "20260605", "srtnCd": "000660", "itmsNm": "SK하이닉스", "mrktCtg": "KOSPI", "clpr": "2070000"},
    ]
    assert store.upsert(rows) == 2
    assert store.upsert(rows) == 0  # 중복 → IGNORE
    assert store.count() == 2
    assert store.dates() == ["20260605"]
    store.close()


def test_marketstore_upsert_sectors(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    items: list[dict[str, Any]] = [
        {"srtn_cd": "005930", "name": "삼성전자", "sectors": ["semiconductor"], "confidence": 0.95},
        {"srtn_cd": "373220", "name": "LG에너지솔루션", "sectors": ["battery_cell", "battery_materials"], "confidence": 0.8},
        {"srtn_cd": "999999", "name": "무명", "sectors": [], "confidence": 0.2},  # 미분류
    ]
    n = store.upsert_sectors(items, source="llm-cls-v1", as_of="20260605")
    assert n == 4  # 1 + 2 + 1(unclassified)
    counts = dict(store.sector_counts("llm-cls-v1"))
    assert counts["battery_cell"] == 1 and counts["unclassified"] == 1
    assert store.upsert_sectors(items, source="llm-cls-v1", as_of="20260605") == 0  # idempotent
    store.close()


def test_datagokr_resultcode_error() -> None:
    def fetch(url: str) -> Any:
        return {"response": {"header": {"resultCode": "30", "resultMsg": "KEY_NOT_REGISTERED"}}}

    with pytest.raises(CollectError):
        DataGoKrIndexClient("k", fetch=fetch).latest("코스피", "20260525", "20260609")


def test_collect_macro_fred_ok_others_blocked() -> None:
    def fetch(url: str) -> Any:
        return {"observations": [{"date": "2026-06-04", "value": "100.0"}]}

    summary = collect_macro(FredClient("k", fetch=fetch), None)  # ecos·datagokr 없음
    assert summary.verified == 5  # FRED 5종
    assert summary.collected == 5
    assert len(summary.blocked) == 6  # ECOS 4 + DATAGOKR 2 (키 미설정)
    assert sum("ECOS_API_KEY 미설정" in b for b in summary.blocked) == 4
    assert sum("DATA_GO_KR_API_KEY 미설정" in b for b in summary.blocked) == 2


def test_collect_macro_ecos_code_unset_is_blocked() -> None:
    # 통계코드 미설정 항목은 호출 전에 blocked(추측 금지 분기)
    item = MacroItem("X", "rate", "macro", "ECOS", region="KR")
    summary = collect_macro(None, None, items=[item])
    assert summary.collected == 0
    assert len(summary.blocked) == 1
    assert "통계코드 미설정" in summary.blocked[0]


# --- 연속성 가드 -----------------------------------------------------------

def _seed_days(store: MarketStore, days: list[str]) -> None:
    store.upsert([{"basDt": d, "srtnCd": "005930", "itmsNm": "삼성전자"} for d in days])


def test_missing_trading_days_detects_interior_gap(tmp_path: Path) -> None:
    """최신일만 신선하고 중간이 빈 상태를 잡는다 — latest_date()로는 안 보이던 결함.

    회귀 대상: 6/12~7/3 한 달 미가동 중 main()이 '최근 7일'만 수집해
    중간 16거래일이 영구 결측으로 남고 아무도 알리지 않던 버그.
    """
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_days(store, ["20260611", "20260706", "20260707", "20260708", "20260709"])
    cal = MarketCalendar.default()
    latest = store.latest_date()
    missing = missing_trading_days(store, date(2026, 6, 11), date(2026, 7, 9), cal)
    store.close()

    assert latest == "20260709"                  # 최신일은 신선 — 그래도 중간이 비어 있다
    assert date(2026, 6, 12) in missing          # 금요일 = 거래일인데 결측
    assert date(2026, 6, 15) in missing
    assert date(2026, 7, 3) in missing
    assert date(2026, 6, 13) not in missing      # 토요일 = 비거래일이라 결측 아님
    assert date(2026, 7, 6) not in missing       # 보유분
    assert len(missing) == 16                    # 실측 백필분과 일치


def test_missing_trading_days_empty_when_continuous(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_days(store, ["20260706", "20260707", "20260708", "20260709"])  # 월~목 연속
    missing = missing_trading_days(store, date(2026, 7, 6), date(2026, 7, 9))
    store.close()
    assert missing == []


def test_split_pending_treats_unpublished_eod_as_waiting_not_gap() -> None:
    """EOD는 +1영업일 공개 → 아직 나올 때가 아닌 날은 갭(⚠️)이 아니라 대기."""
    cal = MarketCalendar.default()
    today = date(2026, 7, 11)  # 토요일
    gaps, pending = split_pending([date(2026, 6, 12), date(2026, 7, 10)], today, cal)
    assert gaps == [date(2026, 6, 12)]      # 한참 전 → 진짜 갭
    assert pending == [date(2026, 7, 10)]   # 금요일 EOD는 월요일 공개 → 대기(경고 금지)


class _EmptyClient:
    """승인 소스가 '정상 응답 + 무자료'로 답하는 상황(휴장). 장애면 CollectError를 raise한다."""

    def all_by_date(self, bas_dt: str) -> list[dict[str, Any]]:
        return []


def test_heal_gaps_marks_no_data_day_and_stops_rewarning(tmp_path: Path) -> None:
    """달력 미등록 휴장일(추석·설·대체공휴일)은 관측을 박제해 매번 재경고하지 않는다(CAL-1).

    달력을 추측으로 고치지 않는다 — 승인 소스가 '무자료'라고 답한 사실만 기록한다.
    """
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_days(store, ["20260302"])  # 3/2만 보유(실제로는 대체공휴일이지만 시드로 존재 가정)
    client = cast(Any, _EmptyClient())
    gap = date(2026, 3, 3)

    filled, newly_closed = heal_gaps(client, store, [gap])
    assert filled == {} and newly_closed == [gap]          # 1회차: 무자료 관측 → 신규 박제
    assert "20260303" in store.no_data_days()

    filled2, newly_closed2 = heal_gaps(client, store, [gap])
    assert newly_closed2 == []                             # 2회차: 이미 아는 휴장 → 재보고 없음

    # 연속성 점검에서도 더 이상 갭으로 세지 않는다
    missing = missing_trading_days(store, date(2026, 3, 2), date(2026, 3, 3))
    store.close()
    assert missing == []


def test_pending_day_is_never_marked_as_holiday(tmp_path: Path) -> None:
    """공개대기(EOD 미공개)를 휴장으로 박제하면 그 날은 영영 수집되지 않는다 — 분리 확인."""
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_days(store, ["20260709"])
    cal = MarketCalendar.default()
    missing = missing_trading_days(store, date(2026, 7, 9), date(2026, 7, 11), cal)
    gaps, pending = split_pending(missing, date(2026, 7, 11), cal)
    assert gaps == [] and pending == [date(2026, 7, 10)]

    # heal_gaps에는 gaps만 넘어간다(main 계약) → 대기일은 박제되지 않는다
    heal_gaps(cast(Any, _EmptyClient()), store, gaps)
    no_data = store.no_data_days()
    store.close()
    assert "20260710" not in no_data
