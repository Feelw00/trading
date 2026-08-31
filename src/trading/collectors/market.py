"""전종목 EOD 시세 DB — data.go.kr 주식시세로 전 상장종목을 일자별 적재(SQLite).

전종목(약 2,877/일)을 1콜로 받아 ``daily_quotes``(append-only, idempotent)에 적재.
스크리너(거래대금·모멘텀·신고가)와 섹터 분류의 토대.
DB는 SQLite(``data/market.sqlite``, 대량·재생성 가능 → gitignored).

엔트리포인트: ``python -m trading.collectors.market`` — **연속성 자가 치유** 수집:
보유 구간 [첫 거래일~오늘]에서 빠진 거래일을 전부 찾아 메운다(``--check``는 탐지·보고만).
시스템이 며칠 멈춰도 갭이 영구 결측으로 남지 않는다 — 최신일만 보면 "중간이 빈" 상태를 놓친다.
백필은 ``backfill(client, store, start, end)``.
"""

import os
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.collectors.data_go_kr import DataGoKrStockClient
from trading.market_calendar.calendar import MarketCalendar

KST = ZoneInfo("Asia/Seoul")

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

# 승인 소스가 "정상 응답 + 무자료"로 답한 거래일(= 휴장 관측).
# 소스 장애는 CollectError로 raise되므로 빈 응답은 장애가 아니라 관측이다.
# 달력(krx_holidays.json)을 추측으로 고치지 않고, 관측을 그대로 박제해 재경고를 막는다(CAL-1).
NO_DATA_DDL = """
CREATE TABLE IF NOT EXISTS no_data_days (
  bas_dt TEXT PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL
)
"""

# 시총 스냅샷(밴드 소급용 파생 — P-17 ①): 토스 캔들 종가(미수정) × DART 발행주식총수.
# daily_quotes와 분리하는 이유: 연속성 가드(_scan_start)가 첫 보유일부터 갭을 치유하는데,
# 2016~2019는 data.go.kr 무자료 시대라 섞으면 헛수집 ~1,000콜 + 가짜 휴장 관측이 쏟아진다.
CAP_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS cap_snapshots (
  bas_dt TEXT NOT NULL, srtn_cd TEXT NOT NULL,
  clpr REAL NOT NULL, shares INTEGER NOT NULL, cap REAL NOT NULL,
  source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  PRIMARY KEY (bas_dt, srtn_cd)
)
"""


def _row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(k) for k in _SRC)


class MarketStore:
    """전종목 일별 시세 SQLite 저장소. append-only(중복 (bas_dt,srtn_cd)는 IGNORE)."""

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(MARKET_DDL)
        self._conn.execute(SECTORS_DDL)
        self._conn.execute(NO_DATA_DDL)
        self._conn.execute(CAP_SNAPSHOTS_DDL)

    def mark_no_data(self, bas_dt: str, source: str = "data.go.kr:getStockPriceInfo") -> bool:
        """해당일을 '소스 무자료(휴장 관측)'로 박제. 신규 기록이면 True."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO no_data_days (bas_dt, observed_at, source) VALUES (?,?,?)",
            (bas_dt, datetime.now(KST).isoformat(), source),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def no_data_days(self) -> set[str]:
        """무자료로 관측된 일자(YYYYMMDD) — 연속성 점검에서 갭으로 세지 않는다."""
        cur = self._conn.execute("SELECT bas_dt FROM no_data_days")
        return {str(r[0]) for r in cur}

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
        """[min_bas_dt~] 행: (srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt, lstg_st_cnt)."""
        cur = self._conn.execute(
            "SELECT srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt, lstg_st_cnt "
            "FROM daily_quotes WHERE bas_dt >= ? ORDER BY srtn_cd, bas_dt",
            (min_bas_dt,),
        )
        return cur.fetchall()

    def series_for(self, srtn_cd: str, min_bas_dt: str) -> list[tuple[Any, ...]]:
        """단일 종목 [min_bas_dt~] 시리즈(rows_since 와 동일 컬럼, bas_dt 오름차순)."""
        cur = self._conn.execute(
            "SELECT srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt, lstg_st_cnt "
            "FROM daily_quotes WHERE srtn_cd=? AND bas_dt >= ? ORDER BY bas_dt",
            (srtn_cd, min_bas_dt),
        )
        return cur.fetchall()

    def closes_for(self, srtn_cd: str, min_bas_dt: str) -> list[tuple[str, float]]:
        """단일 종목 [(bas_dt, 종가)] 오름차순 — R7 채점용. 비수치 종가는 제외."""
        cur = self._conn.execute(
            "SELECT bas_dt, clpr FROM daily_quotes WHERE srtn_cd=? AND bas_dt >= ? ORDER BY bas_dt",
            (srtn_cd, min_bas_dt),
        )
        out: list[tuple[str, float]] = []
        for bas_dt, clpr in cur:
            try:
                out.append((str(bas_dt), float(clpr)))
            except (TypeError, ValueError):
                continue
        return out

    def daily_change_medians(self, min_bas_dt: str) -> list[tuple[str, float]]:
        """일자별 전종목 |등락률(flt_rt)| 중앙값 — R7 레짐 변동성 프록시(EOD 가용 범위)."""
        cur = self._conn.execute(
            "SELECT bas_dt, flt_rt FROM daily_quotes WHERE bas_dt >= ? ORDER BY bas_dt",
            (min_bas_dt,),
        )
        by_date: dict[str, list[float]] = {}
        for bas_dt, flt_rt in cur:
            try:
                by_date.setdefault(str(bas_dt), []).append(abs(float(flt_rt)))
            except (TypeError, ValueError):
                continue
        out: list[tuple[str, float]] = []
        for d in sorted(by_date):
            vals = sorted(by_date[d])
            n = len(vals)
            if n == 0:
                continue
            med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
            out.append((d, med))
        return out

    def find_by_name(self, query: str, *, limit: int = 10) -> list[tuple[str, str]]:
        """최신 거래일 기준 이름 부분일치 → [(srtn_cd, name)]. 종목 해석용."""
        cur = self._conn.execute(
            "SELECT srtn_cd, name FROM daily_quotes "
            "WHERE bas_dt=(SELECT MAX(bas_dt) FROM daily_quotes) AND name LIKE ? "
            "ORDER BY length(name) LIMIT ?",
            (f"%{query}%", limit),
        )
        return [(str(r[0]), str(r[1])) for r in cur]

    def quotes_on(self, bas_dt: str) -> dict[str, float | None]:
        """해당 일자 전 종목 {srtn_cd: 시가총액(원, 결측=None)} — 섹터 밴드(R3 1차 축) 원료."""
        from trading.collectors.fins import parse_amount

        return {
            str(r[0]): parse_amount(r[1])
            for r in self._conn.execute(
                "SELECT srtn_cd, mrkt_tot_amt FROM daily_quotes WHERE bas_dt=?", (bas_dt,)
            )
        }

    def names_on(self, bas_dt: str) -> dict[str, str]:
        """해당 일자 전 종목 {srtn_cd: 종목명} — 전 상장 스코프 수집·태깅(P-18 ②)의 표시용."""
        return {
            str(r[0]): str(r[1] or r[0])
            for r in self._conn.execute(
                "SELECT srtn_cd, name FROM daily_quotes WHERE bas_dt=?", (bas_dt,)
            )
        }

    # --- 시총 스냅샷(밴드 소급 — P-17 ①) ---

    def upsert_cap_snapshots(self, rows: Sequence[tuple[str, str, float, int, float, str]]) -> int:
        """(bas_dt, srtn_cd, clpr, shares, cap, source) 적재 — append-only(IGNORE)."""
        now = datetime.now(KST).isoformat()
        cur = self._conn.executemany(
            "INSERT OR IGNORE INTO cap_snapshots "
            "(bas_dt, srtn_cd, clpr, shares, cap, source, fetched_at) VALUES (?,?,?,?,?,?,?)",
            [(*r, now) for r in rows],
        )
        self._conn.commit()
        return cur.rowcount

    def snapshot_dates(self) -> list[str]:
        """스냅샷 보유 일자(YYYYMMDD, 오름차순) — 연말 발견에 daily_quotes와 합집합으로 쓴다."""
        cur = self._conn.execute("SELECT DISTINCT bas_dt FROM cap_snapshots ORDER BY bas_dt")
        return [str(r[0]) for r in cur]

    def snapshot_caps_on(self, bas_dt: str) -> dict[str, float]:
        """해당 일자 스냅샷 {srtn_cd: 시가총액(원)} — daily_quotes가 있으면 그쪽이 우선."""
        return {
            str(r[0]): float(r[1])
            for r in self._conn.execute(
                "SELECT srtn_cd, cap FROM cap_snapshots WHERE bas_dt=?", (bas_dt,)
            )
        }

    def latest_quote(self, srtn_cd: str) -> tuple[str, str | None, str | None, str | None] | None:
        """종목 최신일 (bas_dt, market, clpr, mrkt_tot_amt). 없으면 None."""
        row = self._conn.execute(
            "SELECT bas_dt, market, clpr, mrkt_tot_amt FROM daily_quotes "
            "WHERE srtn_cd=? ORDER BY bas_dt DESC LIMIT 1",
            (srtn_cd,),
        ).fetchone()
        if row is None:
            return None
        return (str(row[0]), row[1], row[2], row[3])

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

    def sector_names(self, source: str) -> dict[str, str]:
        """{srtn_cd: 종목명} — 섹터 태깅분 기준(수급 수집 등 표시용)."""
        return {
            str(r[0]): str(r[1] or r[0])
            for r in self._conn.execute(
                "SELECT DISTINCT srtn_cd, name FROM stock_sectors WHERE source=?", (source,)
            )
        }

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

    def sector_map_multi(self, sources: Sequence[str]) -> dict[str, list[str]]:
        """여러 분류 소스 병합 — 앞 소스가 우선(종목별 first-wins). 미분류 제외.

        선순위(예: 큐레이션 ``llm-cls-v1``)가 대형주·테마를 보존하고,
        후순위(예: grounded ``dart-ksic-v1``)는 갭만 채운다.
        """
        out: dict[str, list[str]] = {}
        for src in sources:
            for cd, secs in self.sector_map(src).items():
                out.setdefault(cd, secs)  # 먼저 들어온 소스가 이김
        return out

    def codes_with_any_row(self, source: str) -> set[str]:
        """해당 소스로 분류 *시도*된 종목(분류·미분류 모두 포함). 재시도 스킵용."""
        cur = self._conn.execute(
            "SELECT DISTINCT srtn_cd FROM stock_sectors WHERE source=?", (source,)
        )
        return {str(r[0]) for r in cur}

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


# --- 연속성 가드 -----------------------------------------------------------
# 최신일(latest_date)만 보면 "최신은 신선한데 중간이 빈" 상태를 통과시킨다.
# 기대 거래일(달력) 대비 실제 보유 일자를 대조해 갭을 명시적으로 드러낸다.

def missing_trading_days(
    store: MarketStore, start: date, end: date, calendar: MarketCalendar | None = None
) -> list[date]:
    """[start,end]에서 DB에 없는 **거래일**(달력 기준). API 호출 없음.

    무자료로 관측된 날(휴장 확인분)은 제외 — 달력 미등록 휴장일이 매번 갭으로 재경고되지 않는다.
    """
    cal = calendar if calendar is not None else MarketCalendar.default()
    known = set(store.dates()) | store.no_data_days()
    return [
        d
        for d in _daterange(start, end)
        if cal.is_trading_day(d) and d.strftime("%Y%m%d") not in known
    ]


def split_pending(
    missing: Sequence[date], today: date, calendar: MarketCalendar | None = None
) -> tuple[list[date], list[date]]:
    """결측 거래일을 (갭, 공개대기)로 분리.

    EOD는 +1영업일 공개 → 아직 공개될 시점이 아닌 날은 갭이 아니라 정상 대기.
    보수적으로 ``next_trading_day(d) >= today``면 대기로 본다(공개 당일 이른 시각 포함).
    """
    cal = calendar if calendar is not None else MarketCalendar.default()
    gaps: list[date] = []
    pending: list[date] = []
    for d in missing:
        (pending if cal.next_trading_day(d) >= today else gaps).append(d)
    return gaps, pending


def heal_gaps(
    client: DataGoKrStockClient,
    store: MarketStore,
    gaps: Sequence[date],
) -> tuple[dict[str, int], list[date]]:
    """갭 거래일을 개별 수집. (메워진 {YYYYMMDD: 행수}, 무자료로 **새로** 관측된 날들).

    무자료(빈 응답)는 소스 장애가 아니다 — 장애는 CollectError로 raise된다.
    따라서 빈 응답 = 달력 미등록 휴장일 관측 → 박제해두고 다음 점검부터 갭에서 제외(CAL-1).
    """
    filled: dict[str, int] = {}
    newly_closed: list[date] = []
    for d in gaps:
        ymd = d.strftime("%Y%m%d")
        n = collect_date(client, store, ymd)
        if n:
            filled[ymd] = n
        elif store.mark_no_data(ymd):
            newly_closed.append(d)
    return filled, newly_closed


def _scan_start(store: MarketStore, today: date) -> date:
    """연속성 점검 시작일 — 보유분이 있으면 첫 보유일, 없으면 최근 7일(부트스트랩)."""
    dates = store.dates()
    if not dates:
        return today - timedelta(days=7)
    return date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:8]))


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    store = MarketStore()
    today = date.today()
    cal = MarketCalendar.default()

    missing = missing_trading_days(store, _scan_start(store, today), today, cal)
    gaps, pending = split_pending(missing, today, cal)

    if check_only:
        # 관측 휴장일(소스 무자료)을 달력 등록분/미등록분으로 나눈다 — 미등록분만 CAL-1 갱신 대상.
        observed = sorted(store.no_data_days())
        unregistered = [
            ymd
            for ymd in observed
            if date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])) not in cal.extra_holidays
        ]
        print(
            f"연속성 점검: 갭 {len(gaps)}거래일 · 공개대기 {len(pending)}거래일 · "
            f"휴장 관측 {len(observed)}일(달력 미등록 {len(unregistered)}일)"
        )
        if gaps:
            print("⚠️ 갭: " + ", ".join(d.strftime("%Y%m%d") for d in gaps))
        if unregistered:
            print("⚠️ 달력 미등록 휴장 관측(CAL-1 갱신 대상): " + ", ".join(unregistered))
        if not cal.is_covered(today):
            print(
                f"⚠️ 휴장일 확인 범위 만료(covered_through={cal.covered_through}) — "
                "다음 연도 KRX 공지로 krx_holidays.json 갱신 필요(CAL-1)"
            )
        store.close()
        return 0

    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    if not key:
        print("DATA_GO_KR_API_KEY 미설정 — blocked(웹서치 대체 없음)")
        store.close()
        return 0
    client = DataGoKrStockClient(key)

    # 갭(과거 결측)만 치유 대상 — 무자료면 휴장으로 관측·박제한다.
    filled, newly_closed = heal_gaps(client, store, gaps)
    # 공개대기(최근 거래일)는 시도하되 **박제 금지** — 아직 안 나온 것뿐이라 휴장이 아니다.
    for d in pending:
        ymd = d.strftime("%Y%m%d")
        n = collect_date(client, store, ymd)
        if n:
            filled[ymd] = n
    total, days = store.count(), len(store.dates())
    store.close()

    print("수집 일자 " + (", ".join(f"{k}:{v}" for k, v in sorted(filled.items())) or "(없음)"))
    if gaps:
        healed = sum(1 for d in gaps if d.strftime("%Y%m%d") in filled)
        print(f"연속성: 갭 {len(gaps)}거래일 탐지 · {healed}일 메움")
    if newly_closed:
        print(
            "휴장 관측(소스 무자료 — 달력 미등록, CAL-1 갱신 대상): "
            + ", ".join(d.strftime("%Y%m%d") for d in newly_closed)
        )
    print(f"DB 보유 일자 {days}, 총 {total}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
