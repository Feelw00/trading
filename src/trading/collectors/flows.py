"""투자자별 매매동향(수급) 수집 — 스크리너 후보 종목 + KOSPI/KOSDAQ 시장 단위.

소스: KIS Open API(``trading.collectors.kis``) — OPEN_QUESTIONS COLLECT-2 갭①을
KIS TR로 해소(KRX 정보데이터시스템 직접 접근 불필요). R3 수급 페르소나 grounding과
저녁 보고 수급 섹션의 토대.

저장: ``data/flows.sqlite`` ``investor_flows`` — append-only(INSERT OR IGNORE,
UNIQUE(scope, code, bas_dt)). 핵심 3주체(개인/외국인/기관) 순매수 수량·대금을 컬럼으로
추출하고 응답 원행은 ``raw_json``에 보존(세부 주체 분해는 후속이 raw에서).
대금 단위: 백만원(2026-06-11 실호출 관측 — 삼성전자 수량×주가 대조 검증).

엔트리포인트: ``python -m trading.collectors.flows`` — 키 없으면 blocked(대체 금지).
"""

import json
import os
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.collectors.base import now_kst
from trading.collectors.kis import KisClient, client_from_env
from trading.market_calendar.calendar import in_krx_session

DEFAULT_DB = Path("data") / "flows.sqlite"
SOURCE = "kis:투자자매매동향(일별)"
MARKETS = ("KOSPI", "KOSDAQ")

FLOWS_DDL = """
CREATE TABLE IF NOT EXISTS investor_flows (
  scope TEXT NOT NULL,            -- 'stock' | 'market'
  code TEXT NOT NULL,             -- srtn_cd(6자리) 또는 KOSPI|KOSDAQ
  name TEXT,
  bas_dt TEXT NOT NULL,           -- 거래일 YYYYMMDD (stck_bsop_date)
  frgn_ntby_qty TEXT, prsn_ntby_qty TEXT, orgn_ntby_qty TEXT,
  frgn_ntby_tr_pbmn TEXT, prsn_ntby_tr_pbmn TEXT, orgn_ntby_tr_pbmn TEXT,
  source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(scope, code, bas_dt)
)
"""

_CORE_FIELDS = (
    "frgn_ntby_qty", "prsn_ntby_qty", "orgn_ntby_qty",
    "frgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn",
)
_INSERT = (
    "INSERT OR IGNORE INTO investor_flows "
    "(scope, code, name, bas_dt, frgn_ntby_qty, prsn_ntby_qty, orgn_ntby_qty, "
    "frgn_ntby_tr_pbmn, prsn_ntby_tr_pbmn, orgn_ntby_tr_pbmn, source, fetched_at, raw_json) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class FlowStore:
    """수급 SQLite 저장소. append-only(중복 (scope,code,bas_dt)는 IGNORE)."""

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(FLOWS_DDL)

    def upsert(
        self, scope: str, code: str, name: str | None, rows: Sequence[dict[str, Any]]
    ) -> int:
        """원시 응답 행들을 적재. 신규 행 수 반환. bas_dt 없는 행은 버림(추측 금지)."""
        fetched = now_kst().isoformat()
        values: list[tuple[Any, ...]] = []
        for r in rows:
            bas_dt = r.get("stck_bsop_date")
            if not bas_dt:
                continue
            values.append(
                (
                    scope, code, name, str(bas_dt),
                    *(r.get(f) for f in _CORE_FIELDS),
                    SOURCE, fetched, json.dumps(r, ensure_ascii=False),
                )
            )
        before = self._conn.total_changes
        self._conn.executemany(_INSERT, values)
        self._conn.commit()
        return self._conn.total_changes - before

    def recent_for(
        self, scope: str, code: str, *, limit: int = 5
    ) -> list[tuple[str, str | None, str | None, str | None, str | None]]:
        """[(bas_dt, 개인, 외국인, 기관계, 기금[연기금])] 순매수대금 최신순. 단위 백만원.

        기금(``fund_ntby_tr_pbmn``, KIS 공식 라벨 "기금 순매수 거래 대금")은 기관계의
        구성요소(기관계=금융투자+투신+사모+은행+보험+종금+기금 — 실관측 합산 검증).
        컬럼이 아니라 ``raw_json`` 보존분에서 추출.
        """
        cur = self._conn.execute(
            "SELECT bas_dt, prsn_ntby_tr_pbmn, frgn_ntby_tr_pbmn, orgn_ntby_tr_pbmn, "
            "json_extract(raw_json, '$.fund_ntby_tr_pbmn') "
            "FROM investor_flows WHERE scope=? AND code=? ORDER BY bas_dt DESC LIMIT ?",
            (scope, code, limit),
        )
        return [(str(r[0]), r[1], r[2], r[3], r[4]) for r in cur]

    def latest_date(self) -> str | None:
        row = self._conn.execute("SELECT MAX(bas_dt) FROM investor_flows").fetchone()
        return str(row[0]) if row and row[0] else None

    def stocks_on(
        self, bas_dt: str
    ) -> list[tuple[str, str | None, str | None, str | None, str | None, str | None]]:
        """해당 거래일 종목 수급 [(code, name, 개인, 외국인, 기관계, 기금)] — 외국인 내림차순."""
        cur = self._conn.execute(
            "SELECT code, name, prsn_ntby_tr_pbmn, frgn_ntby_tr_pbmn, orgn_ntby_tr_pbmn, "
            "json_extract(raw_json, '$.fund_ntby_tr_pbmn') "
            "FROM investor_flows WHERE scope='stock' AND bas_dt=? "
            "ORDER BY CAST(frgn_ntby_tr_pbmn AS REAL) DESC",
            (bas_dt,),
        )
        return [(str(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in cur]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM investor_flows").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


def collect(
    client: KisClient,
    store: FlowStore,
    candidates: Sequence[tuple[str, str]],
    bas_dt: str,
) -> dict[str, int]:
    """시장(KOSPI/KOSDAQ) + 후보 종목 수급 적재. {대상: 신규행수}. 실패 대상은 -1(계속 진행)."""
    result: dict[str, int] = {}
    for mkt in MARKETS:
        try:
            result[mkt] = store.upsert("market", mkt, mkt, client.investor_flows_by_market(mkt, bas_dt))
        except Exception as exc:  # noqa: BLE001 — 한 대상 실패가 나머지를 막지 않는다
            print(f"[flows] {mkt} 실패: {exc}")
            result[mkt] = -1
    for code, name in candidates:
        try:
            result[code] = store.upsert("stock", code, name, client.investor_flows_by_stock(code, bas_dt))
        except Exception as exc:  # noqa: BLE001
            print(f"[flows] {code}({name}) 실패: {exc}")
            result[code] = -1
    return result


def _mn_f(mn: str | None) -> float | None:
    """백만원 문자열 → float(백만원). 비수치는 None(추측 금지)."""
    try:
        return float(mn)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _aek(mn: float | str | None) -> str:
    """백만원 → 억원 표기(부호 포함). 비수치는 '?'(추측 금지)."""
    v = _mn_f(mn) if isinstance(mn, str) or mn is None else mn
    if v is None:
        return "?"
    return f"{v / 100.0:+,.0f}"


def _investor_line(
    prsn: str | None, frgn: str | None, orgn: str | None, fund: str | None
) -> str:
    """개인|외국인|연기금|기관(연기금外). 기금=fund_ntby_tr_pbmn(KIS 공식 '기금 순매수 거래 대금').

    그외 기관 = 기관계 − 기금(기관계가 기금 포함 합산임은 실관측 검증). 둘 중 하나라도
    비수치면 분리하지 않고 기관계만 표기(임의 산술 금지).
    """
    orgn_f, fund_f = _mn_f(orgn), _mn_f(fund)
    if orgn_f is not None and fund_f is not None:
        return (
            f"개인 {_aek(prsn)} | 외국인 {_aek(frgn)} | 연기금 {_aek(fund_f)} | "
            f"기관(연기금外) {_aek(orgn_f - fund_f)}"
        )
    return f"개인 {_aek(prsn)} | 외국인 {_aek(frgn)} | 기관계 {_aek(orgn)} (연기금 분리불가)"


def report_lines(store: FlowStore) -> list[str]:
    """DB 최신 거래일의 투자자별 거래실적 요약(결정론 렌더 — LLM 미개입). 단위 억원."""
    latest = store.latest_date()
    if latest is None:
        return ["수급 데이터 없음 — collect-flows 미실행(추측 금지)"]
    out = [f"투자자별 거래실적 as_of={latest} ({SOURCE}, 단위 억원)", "[시장]"]
    for mkt in MARKETS:
        rows = store.recent_for("market", mkt, limit=1)
        if not rows or rows[0][0] != latest:
            out.append(f"{mkt:6}: {latest} 데이터 없음")
            continue
        _, prsn, frgn, orgn, fund = rows[0]
        out.append(f"{mkt:6}: {_investor_line(prsn, frgn, orgn, fund)}")
    stock_rows = store.stocks_on(latest)
    if stock_rows:
        out.append("[후보 종목] (외국인 순매수 내림차순)")
        for code, name, prsn, frgn, orgn, fund in stock_rows:
            out.append(f"{(name or code):<12}: {_investor_line(prsn, frgn, orgn, fund)}")
    else:
        out.append(f"[후보 종목] {latest} 데이터 없음")
    return out


def intraday_lines(client: KisClient, *, now: datetime | None = None) -> list[str]:
    """장중 당일 잠정 수급(시세성 TR) — 표시 전용, 적재 없음(응답에 날짜 필드 부재).

    장외 시간·휴장일엔 빈 리스트(잠정치를 확정처럼 보일 여지 차단). 단위는 일별 TR과
    동일 필드 체계라 백만원으로 표기 — 마감 후 일별 확정치와 교차검증 전까지 '잠정' 명시.
    """
    resolved = now if now is not None else now_kst()
    if not in_krx_session(resolved):
        return []
    out = [
        f"[당일 잠정] {resolved.strftime('%Y-%m-%d %H:%M')} KST 누계 (시세성 — 확정치 아님, 단위 억원)"
    ]
    for mkt in MARKETS:
        try:
            row = client.investor_flows_intraday(mkt)
        except Exception as exc:  # noqa: BLE001 — 한 시장 실패가 나머지를 막지 않는다
            out.append(f"{mkt:6}: 조회 실패 — {exc}")
            continue
        if not row:
            out.append(f"{mkt:6}: 데이터 없음")
            continue
        out.append(
            f"{mkt:6}: "
            + _investor_line(
                row.get("prsn_ntby_tr_pbmn"),
                row.get("frgn_ntby_tr_pbmn"),
                row.get("orgn_ntby_tr_pbmn"),
                row.get("fund_ntby_tr_pbmn"),
            )
        )
    return out


def run(top_n: int = 15) -> int:
    client = client_from_env()
    if client is None:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — blocked(웹서치 대체 없음)")
        return 0
    from trading.collectors.market import MarketStore
    from trading.screener import ScreenConfig, screen

    mstore = MarketStore()
    res = screen(mstore, ScreenConfig(top_n=top_n))
    mstore.close()
    if not res.candidates:
        print("수급 수집 스킵 — 스크리너 후보 없음(시세 DB 확인)")
        return 0
    store = FlowStore()
    pairs = [(c.srtn_cd, c.name) for c in res.candidates]
    result = collect(client, store, pairs, res.as_of)
    total, latest = store.count(), store.latest_date()
    store.close()
    failed = sorted(k for k, v in result.items() if v < 0)
    new_rows = sum(v for v in result.values() if v > 0)
    print(
        f"수급 적재 as_of={res.as_of}: 대상 {len(result)}(시장 2+후보 {len(pairs)}) · "
        f"신규 {new_rows}행 · DB 총 {total}행(최신 {latest})"
        + (f" · 실패 {len(failed)}: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


def main() -> int:
    """``--report``: 수집(키 있으면) 후 최신 거래일 확정 수급 + 장중이면 당일 잠정 출력."""
    import sys

    if "--report" in sys.argv[1:]:
        rc = run()
        store = FlowStore()
        for line in report_lines(store):
            print(line)
        store.close()
        client = client_from_env()
        if client is not None:
            for line in intraday_lines(client):
                print(line)
        return rc
    return run()


__all__ = [
    "DEFAULT_DB",
    "MARKETS",
    "SOURCE",
    "FlowStore",
    "collect",
    "intraday_lines",
    "main",
    "report_lines",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
