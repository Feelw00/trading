"""토스 종목 사실 축적 — 당일 잠정 제외·멱등·오류 격리 테스트(응답 형태는 8/28 실관측)."""

from pathlib import Path
from typing import Any

from trading.collectors.toss_facts import DAILY_KINDS, TossFactsStore, collect_stock_facts

TODAY = "2026-08-28"


def _payload(dates: list[str]) -> dict[str, Any]:
    # 2026-08-28 실호출 관측 봉투 축약(short-selling 형태)
    return {
        "nextUntil": "2026-08-24",
        "records": [
            {"date": d, "updatedAt": f"{d}T18:14:08.000+09:00", "shortSellingVolume": "914065"}
            for d in dates
        ],
    }


class _FakeClient:
    def __init__(self, fail_symbol: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail_symbol

    def _serve(self, kind: str, symbol: str) -> dict[str, Any]:
        self.calls.append((kind, symbol))
        if symbol == self._fail:
            raise RuntimeError("boom")
        return _payload([TODAY, "2026-08-27", "2026-08-26"])  # 당일 잠정 포함

    def stock_short_selling(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]:
        return self._serve("short-selling", symbol)

    def stock_securities_lending(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]:
        return self._serve("securities-lending", symbol)

    def stock_credit_trades(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]:
        return self._serve("credit-trades", symbol)


def test_collect_skips_provisional_today_and_is_idempotent(tmp_path: Path) -> None:
    store = TossFactsStore(tmp_path / "t.sqlite")
    client = _FakeClient()
    added, calls, errors = collect_stock_facts(client, store, ["005930"], today=TODAY)
    assert calls == len(DAILY_KINDS) and errors == []
    assert added == len(DAILY_KINDS) * 2  # 3일 응답 중 당일 잠정 제외 → 2일만 적재

    dates = [d for d, _p in store.series("short-selling", "005930")]
    assert TODAY not in dates and dates == ["2026-08-27", "2026-08-26"]

    added2, _, _ = collect_stock_facts(client, store, ["005930"], today=TODAY)
    assert added2 == 0  # append-only + UNIQUE — 재실행 무변화(멱등)
    cov = store.coverage()
    assert cov["securities-lending"] == (1, 2, "2026-08-27")
    store.close()


def test_one_symbol_failure_does_not_block_others(tmp_path: Path) -> None:
    store = TossFactsStore(tmp_path / "t.sqlite")
    client = _FakeClient(fail_symbol="000001")
    added, calls, errors = collect_stock_facts(client, store, ["000001", "005930"], today=TODAY)
    assert calls == len(DAILY_KINDS) * 2
    assert len(errors) == len(DAILY_KINDS)  # 실패 종목의 kind별 오류 기록
    assert added == len(DAILY_KINDS) * 2    # 정상 종목은 전부 적재
    store.close()
