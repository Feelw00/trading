"""전종목 EOD 시세 DB — data.go.kr 주식시세로 전 상장종목을 일자별 적재(SQLite).

전종목(약 2,877/일)을 1콜로 받아 ``daily_quotes``(append-only, idempotent)에 적재.
스크리너(거래대금·모멘텀·신고가)와 섹터 분류의 토대.
DB는 SQLite(``data/market.sqlite``, 대량·재생성 가능 → gitignored).

엔트리포인트: ``python -m trading.collectors.market`` — 최근 ~7일 idempotent 수집.
백필은 ``backfill(client, store, start, end)``.
"""

import os
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trading.collectors.data_go_kr import DataGoKrStockClient

DEFAULT_DB = Path("data") / "market.sqlite"

MARKET_DDL = """
CREATE TABLE IF NOT EXISTS daily_quotes (
  bas_dt TEXT NOT NULL, srtn_cd TEXT NOT NULL, isin_cd TEXT, name TEXT, market TEXT,
  clpr TEXT, vs TEXT, flt_rt TEXT, mkp TEXT, hipr TEXT, lopr TEXT,
  trqu TEXT, tr_prc TEXT, lstg_st_cnt TEXT, mrkt_tot_amt TEXT,
  UNIQUE(bas_dt, srtn_cd)
)
"""

# DB 컬럼 ← data.go.kr 응답 필드(순서 1:1)
_COLS = (
    "bas_dt", "srtn_cd", "isin_cd", "name", "market", "clpr", "vs", "flt_rt",
    "mkp", "hipr", "lopr", "trqu", "tr_prc", "lstg_st_cnt", "mrkt_tot_amt",
)
_SRC = (
    "basDt", "srtnCd", "isinCd", "itmsNm", "mrktCtg", "clpr", "vs", "fltRt",
    "mkp", "hipr", "lopr", "trqu", "trPrc", "lstgStCnt", "mrktTotAmt",
)
_INSERT = f"INSERT OR IGNORE INTO daily_quotes ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})"

# 섹터 분류(멀티에이전트 결과) — 다중소속이라 (종목×섹터) 한 행. 미분류는 'unclassified'.
SECTORS_DDL = """
CREATE TABLE IF NOT EXISTS stock_sectors (
  srtn_cd TEXT NOT NULL, name TEXT, sector TEXT NOT NULL, confidence REAL,
  source TEXT, as_of TEXT,
  UNIQUE(srtn_cd, sector, source)
)
"""
_SECTORS_INSERT = (
    "INSERT OR IGNORE INTO stock_sectors (srtn_cd, name, sector, confidence, source, as_of) "
    "VALUES (?,?,?,?,?,?)"
)


def _row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(k) for k in _SRC)


class MarketStore:
    """전종목 일별 시세 SQLite 저장소. append-only(중복 (bas_dt,srtn_cd)는 IGNORE)."""

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(MARKET_DDL)
        self._conn.execute(SECTORS_DDL)

    def upsert(self, rows: Sequence[dict[str, Any]]) -> int:
        """INSERT OR IGNORE. 신규 적재된 행 수 반환(중복·덮어쓰기 없음)."""
        before = self._conn.total_changes
        self._conn.executemany(_INSERT, [_row_values(r) for r in rows])
        self._conn.commit()
        return self._conn.total_changes - before

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM daily_quotes").fetchone()
        return int(row[0]) if row else 0

    def dates(self) -> list[str]:
        cur = self._conn.execute("SELECT DISTINCT bas_dt FROM daily_quotes ORDER BY bas_dt")
        return [str(r[0]) for r in cur]

    def latest_date(self) -> str | None:
        row = self._conn.execute("SELECT MAX(bas_dt) FROM daily_quotes").fetchone()
        return str(row[0]) if row and row[0] else None

    def nth_recent_date(self, n: int) -> str | None:
        """n번째로 최근인 거래일(신호 lookback 컷오프). 부족하면 가장 오래된 날."""
        cur = self._conn.execute(
            "SELECT DISTINCT bas_dt FROM daily_quotes ORDER BY bas_dt DESC LIMIT ?", (n,)
        )
        dates = [str(r[0]) for r in cur]
        return dates[-1] if dates else None

    def rows_since(self, min_bas_dt: str) -> list[tuple[Any, ...]]:
        """[min_bas_dt~] 행: (srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt)."""
        cur = self._conn.execute(
            "SELECT srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt "
            "FROM daily_quotes WHERE bas_dt >= ? ORDER BY srtn_cd, bas_dt",
            (min_bas_dt,),
        )
        return cur.fetchall()

    def upsert_sectors(
        self, items: Sequence[dict[str, Any]], *, source: str, as_of: str
    ) -> int:
        """멀티에이전트 분류 결과 적재. 다중소속은 섹터별 행, 미분류는 'unclassified'."""
        rows: list[tuple[Any, ...]] = []
        for it in items:
            secs = it.get("sectors") or []
            conf, cd, nm = it.get("confidence"), it.get("srtn_cd"), it.get("name")
            if not secs:
                rows.append((cd, nm, "unclassified", conf, source, as_of))
            else:
                rows.extend((cd, nm, s, conf, source, as_of) for s in secs)
        before = self._conn.total_changes
        self._conn.executemany(_SECTORS_INSERT, rows)
        self._conn.commit()
        return self._conn.total_changes - before

    def sector_map(self, source: str) -> dict[str, list[str]]:
        """{srtn_cd: [sector,...]} — 미분류 제외."""
        cur = self._conn.execute(
            "SELECT srtn_cd, sector FROM stock_sectors WHERE source=? AND sector != 'unclassified'",
            (source,),
        )
        out: dict[str, list[str]] = {}
        for r in cur:
            out.setdefault(str(r[0]), []).append(str(r[1]))
        return out

    def sector_counts(self, source: str) -> list[tuple[str, int]]:
        cur = self._conn.execute(
            "SELECT sector, COUNT(*) FROM stock_sectors WHERE source=? GROUP BY sector ORDER BY 2 DESC",
            (source,),
        )
        return [(str(r[0]), int(r[1])) for r in cur]

    def close(self) -> None:
        self._conn.close()


def collect_date(client: DataGoKrStockClient, store: MarketStore, bas_dt: str) -> int:
    """해당 거래일 전종목 적재. 신규 행 수 반환(0=비거래일/이미 적재)."""
    rows = client.all_by_date(bas_dt)
    if not rows:
        return 0
    return store.upsert(rows)


def _daterange(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d = d + timedelta(days=1)


def backfill(
    client: DataGoKrStockClient, store: MarketStore, start: date, end: date
) -> dict[str, int]:
    """[start,end] 일자별 수집. 비거래일은 건너뜀. {YYYYMMDD: 신규행수}."""
    result: dict[str, int] = {}
    for d in _daterange(start, end):
        n = collect_date(client, store, d.strftime("%Y%m%d"))
        if n:
            result[d.strftime("%Y%m%d")] = n
    return result


def main() -> int:
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    if not key:
        print("DATA_GO_KR_API_KEY 미설정 — blocked(웹서치 대체 없음)")
        return 0
    client = DataGoKrStockClient(key)
    store = MarketStore()
    today = date.today()
    res = backfill(client, store, today - timedelta(days=7), today)
    total, days = store.count(), len(store.dates())
    store.close()
    print("수집 일자 " + (", ".join(f"{k}:{v}" for k, v in sorted(res.items())) or "(없음)"))
    print(f"DB 보유 일자 {days}, 총 {total}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
