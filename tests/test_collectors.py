"""수집기 어댑터(base/FRED/ECOS/macro) 단위 테스트 — 네트워크 없이 주입 fetch로 검증."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.collectors.base import CollectedFact, CollectError, fetch_json, write_facts
from trading.collectors.ecos import EcosClient
from trading.collectors.fred import FredClient
from trading.collectors.macro import MacroItem, collect_macro

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


def test_collect_macro_fred_ok_ecos_no_client_blocked() -> None:
    def fetch(url: str) -> Any:
        return {"observations": [{"date": "2026-06-04", "value": "100.0"}]}

    summary = collect_macro(FredClient("k", fetch=fetch), None)
    assert summary.verified == 5  # FRED 5종
    assert summary.collected == 5
    assert len(summary.blocked) == 4  # ECOS 4종 — 키 미설정(코드는 확정됨)
    assert all("ECOS_API_KEY 미설정" in b for b in summary.blocked)


def test_collect_macro_ecos_code_unset_is_blocked() -> None:
    # 통계코드 미설정 항목은 호출 전에 blocked(추측 금지 분기)
    item = MacroItem("X", "rate", "macro", "ECOS", region="KR")
    summary = collect_macro(None, None, items=[item])
    assert summary.collected == 0
    assert len(summary.blocked) == 1
    assert "통계코드 미설정" in summary.blocked[0]
