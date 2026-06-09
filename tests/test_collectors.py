"""수집기 어댑터(base/FRED/ECOS/macro) 단위 테스트 — 네트워크 없이 주입 fetch로 검증."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.collectors.base import CollectedFact, CollectError, fetch_json, write_facts
from trading.collectors.data_go_kr import DataGoKrIndexClient, DataGoKrStockClient
from trading.collectors.ecos import EcosClient
from trading.collectors.fred import FredClient
from trading.collectors.macro import MacroItem, collect_macro
from trading.collectors.market import MarketStore

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
