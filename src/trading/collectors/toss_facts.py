"""토스 종목별 일별 사실 축적 — 공매도·대차·신용 (data/toss_facts.sqlite, PIVOT-10).

v0.3 용도: 수급 네거티브 스크린 보조·심사 패킷의 관측 가능 사실(공매도 잔량 비중,
대차잔고 추이, 신용융자 잔고). **판정 게이트 아님** — 축적·표기 전용(정책 결재 전).

규율:
- 응답은 2026-08-28 실호출 관측 봉투 기준(``TossClient._stock_series`` 주석) — 필드 추측 금지,
  원문 payload를 그대로 박제하고 소비자가 관측 필드만 읽는다.
- **당일 행은 잠정이라 적재 제외**(individual null·장중 updatedAt 관측 — v0.2 KIS 잠정
  수급 오판 사건과 같은 함정).
- append-only: UNIQUE(kind, symbol, date) INSERT OR IGNORE — 확정치 첫 관측을 박제.
- investor-trading은 일일 축적 대상이 아니다(수급은 KIS가 정본 — 교차 검증 시 메서드 직접 호출).
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from trading.collectors.base import now_kst

DEFAULT_DB = Path("data") / "toss_facts.sqlite"
SOURCE = "toss:stock-series"

# 일일 축적 kind — KIS에 없는 신규 축만(수급 제외)
DAILY_KINDS = ("short-selling", "securities-lending", "credit-trades")

_DDL = """
CREATE TABLE IF NOT EXISTS stock_daily (
  kind TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL,
  updated_at TEXT, payload TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  UNIQUE(kind, symbol, date)
);
"""


class SeriesClient(Protocol):
    """TossClient의 종목 시계열 부분 — 테스트 대역용 최소 표면."""

    def stock_short_selling(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]: ...
    def stock_securities_lending(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]: ...
    def stock_credit_trades(self, symbol: str, *, count: int = 10, until: str | None = None) -> dict[str, Any]: ...


class TossFactsStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def upsert(self, kind: str, symbol: str, records: list[dict[str, Any]], *, today: str) -> int:
        """확정 일자 행만 적재(당일 잠정 제외). 신규 행 수 반환."""
        rows = [
            (
                kind,
                symbol,
                str(r.get("date")),
                str(r.get("updatedAt") or ""),
                json.dumps(r, ensure_ascii=False),
                SOURCE,
                now_kst().isoformat(),
            )
            for r in records
            if r.get("date") and str(r["date"]) < today  # 당일 잠정 제외(관측 근거는 모듈 주석)
        ]
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO stock_daily VALUES (?,?,?,?,?,?,?)", rows
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def series(self, kind: str, symbol: str, *, limit: int = 60) -> list[tuple[str, dict[str, Any]]]:
        """[(date, payload)] 최신순."""
        rows = self._conn.execute(
            "SELECT date, payload FROM stock_daily WHERE kind=? AND symbol=? "
            "ORDER BY date DESC LIMIT ?",
            (kind, symbol, limit),
        ).fetchall()
        return [(str(r[0]), json.loads(str(r[1]))) for r in rows]

    def coverage(self) -> dict[str, tuple[int, int, str | None]]:
        """kind별 (종목 수, 일자 수, 최신일)."""
        out: dict[str, tuple[int, int, str | None]] = {}
        for r in self._conn.execute(
            "SELECT kind, COUNT(DISTINCT symbol), COUNT(DISTINCT date), MAX(date) "
            "FROM stock_daily GROUP BY kind"
        ):
            out[str(r[0])] = (int(r[1]), int(r[2]), str(r[3]) if r[3] else None)
        return out

    def close(self) -> None:
        self._conn.close()


def collect_stock_facts(
    client: SeriesClient,
    store: TossFactsStore,
    symbols: list[str],
    *,
    today: str,
    count: int = 10,
) -> tuple[int, int, list[str]]:
    """DAILY_KINDS × 종목 축적. (신규 행, 호출 수, 오류) — 한 대상 실패가 나머지를 막지 않는다."""
    fetchers = {
        "short-selling": client.stock_short_selling,
        "securities-lending": client.stock_securities_lending,
        "credit-trades": client.stock_credit_trades,
    }
    added = calls = 0
    errors: list[str] = []
    for symbol in symbols:
        for kind in DAILY_KINDS:
            calls += 1
            try:
                out = fetchers[kind](symbol, count=count)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{symbol}/{kind}: {exc}")
                continue
            records = out.get("records") if isinstance(out, dict) else None
            if isinstance(records, list):
                added += store.upsert(kind, symbol, records, today=today)
    return added, calls, errors


__all__ = [
    "DAILY_KINDS",
    "DEFAULT_DB",
    "SOURCE",
    "SeriesClient",
    "TossFactsStore",
    "collect_stock_facts",
]
