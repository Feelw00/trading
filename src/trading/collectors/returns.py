"""주주환원(배당·자기주식)·분할 이력 수집 — v1.8 ③ 예약분(운영자 결재 2026-09-01 착수).

원천(실호출 관측 확정 2026-09-01, `dart.py` 각 메서드 주석):
- 배당: ``alotMatter`` — 주당 배당금·수익률·성향 (연간 사업보고서 11011 기준)
- 자기주식: ``tesstkAcqsDspsSttus`` — 취득/처분/**소각**(change_qy_incnr) 수량
- 분할: ``list.json pblntf_ty=B``(주요사항보고) 중 report_nm에 "분할" 포함
  (LG화학 2020 물적분할이 "주요사항보고서(회사분할결정)"로 관측됨)

수집기는 **사실 박제만** 한다 — 가점·네거티브 스크린 편입은 분포 실측 첨부 후
별도 결재(docs/POLICY_PARAMS.md §5 v1.8 ③·§6). append 전용(INSERT OR IGNORE),
attempts 테이블로 멱등 재실행. 결측은 None 박제(0 폴백 금지 — parse_amount).
"""

import sqlite3
from pathlib import Path
from typing import Any

from trading.collectors.base import CollectError, now_kst
from trading.collectors.dart import DartClient
from trading.collectors.fins import parse_amount

DEFAULT_DB = Path("data") / "returns.sqlite"
SOURCE_ALOT = "dart:alotMatter"
SOURCE_TESSTK = "dart:tesstkAcqsDspsSttus"
SOURCE_SPLIT = "dart:list/pblntf_ty=B"

_DDL = """
CREATE TABLE IF NOT EXISTS alot_facts (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL,
  se TEXT NOT NULL, stock_knd TEXT NOT NULL,
  thstrm REAL, source TEXT, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, se, stock_knd)
);
CREATE TABLE IF NOT EXISTS tesstk_facts (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL,
  acqs_mth TEXT NOT NULL, stock_knd TEXT NOT NULL,
  bsis_qy REAL, acqs_qy REAL, dsps_qy REAL, incnr_qy REAL, trmend_qy REAL,
  source TEXT, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, acqs_mth, stock_knd)
);
CREATE TABLE IF NOT EXISTS split_events (
  srtn_cd TEXT NOT NULL, rcept_no TEXT NOT NULL,
  rcept_dt TEXT, report_nm TEXT, source TEXT, fetched_at TEXT,
  UNIQUE(rcept_no)
);
CREATE TABLE IF NOT EXISTS ret_attempts (
  srtn_cd TEXT NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
  status TEXT NOT NULL, fetched_at TEXT,
  UNIQUE(srtn_cd, kind, period)
);
"""


class ReturnsStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_DDL)

    def close(self) -> None:
        self._conn.close()

    # --- attempts (멱등) ---
    def record_attempt(self, srtn_cd: str, kind: str, period: str, status: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO ret_attempts VALUES (?,?,?,?,?)",
            (srtn_cd, kind, period, status, now_kst().isoformat()),
        )
        self._conn.commit()

    def attempted(self, srtn_cd: str, kind: str, period: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM ret_attempts WHERE srtn_cd=? AND kind=? AND period=?",
            (srtn_cd, kind, period),
        ).fetchone()
        return str(row[0]) if row else None

    # --- 적재 ---
    def upsert_alot(self, srtn_cd: str, year: str, rows: list[dict[str, Any]]) -> int:
        fetched = now_kst().isoformat()
        values = [
            (
                srtn_cd, year, str(r.get("se") or ""), str(r.get("stock_knd") or "-"),
                parse_amount(r.get("thstrm")), SOURCE_ALOT, fetched,
            )
            for r in rows
            if r.get("se")
        ]
        before = self._conn.total_changes
        self._conn.executemany("INSERT OR IGNORE INTO alot_facts VALUES (?,?,?,?,?,?,?)", values)
        self._conn.commit()
        return self._conn.total_changes - before

    def upsert_tesstk(self, srtn_cd: str, year: str, rows: list[dict[str, Any]]) -> int:
        fetched = now_kst().isoformat()
        values = [
            (
                srtn_cd, year,
                "/".join(
                    str(r.get(k) or "") for k in ("acqs_mth1", "acqs_mth2", "acqs_mth3")
                ),
                str(r.get("stock_knd") or "-"),
                parse_amount(r.get("bsis_qy")), parse_amount(r.get("change_qy_acqs")),
                parse_amount(r.get("change_qy_dsps")), parse_amount(r.get("change_qy_incnr")),
                parse_amount(r.get("trmend_qy")),
                SOURCE_TESSTK, fetched,
            )
            for r in rows
        ]
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO tesstk_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", values
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def add_splits(self, srtn_cd: str, rows: list[dict[str, Any]]) -> int:
        fetched = now_kst().isoformat()
        values = [
            (
                srtn_cd, str(r.get("rcept_no") or ""), str(r.get("rcept_dt") or ""),
                str(r.get("report_nm") or "").strip(), SOURCE_SPLIT, fetched,
            )
            for r in rows
            if r.get("rcept_no")
        ]
        before = self._conn.total_changes
        self._conn.executemany("INSERT OR IGNORE INTO split_events VALUES (?,?,?,?,?,?)", values)
        self._conn.commit()
        return self._conn.total_changes - before

    # --- 읽기(분포·후속 결재 원료) ---
    def dividend_series(self, srtn_cd: str) -> dict[str, dict[str, float | None]]:
        """연도 → {dps(주당배당금), yield_pct(수익률), payout_pct(연결 성향)}.

        stock_knd는 보통주 우선, **'-'(단일 주식 종류 기재) 폴백** — 실관측 2026-09-01:
        신세계I&C가 2024년부터 '-'로 기재해 배당 430·560원이 0으로 오독됐던 버그 수정.
        우선주 행은 무시한다."""
        out: dict[str, dict[str, float | None]] = {}
        for year, se, knd, v in self._conn.execute(
            "SELECT bsns_year, se, stock_knd, thstrm FROM alot_facts WHERE srtn_cd=?",
            (srtn_cd,),
        ):
            y = out.setdefault(str(year), {"dps": None, "yield_pct": None, "payout_pct": None})
            se_s, knd_s = str(se), str(knd)
            key: str | None = None
            if se_s.startswith("주당 현금배당금"):
                key = "dps"
                # 정합성 가드(실관측 2026-09-01, 와이엔텍): 일부 공시가 주당 행에
                # 배당금 **총액**(9억+)을 기재 — 주당 100만원 초과는 오기재로 보고 무시
                # (지급 여부 판정은 수익률 필드로 폴백 — quality.dividend_streak)
                if v is not None and v > 1_000_000:
                    v = None
            elif se_s.startswith("현금배당수익률"):
                key = "yield_pct"
            elif se_s.startswith("(연결)현금배당성향"):
                y["payout_pct"] = v
            if key is not None and v is not None:
                if knd_s == "보통주" or (knd_s == "-" and y[key] is None):
                    y[key] = v
        return out

    def buyback_series(self, srtn_cd: str) -> dict[str, dict[str, float]]:
        """연도 → {acqs(취득 합), incnr(소각 합)} — 주식종류별 **총계 행**이 정본
        (실관측: 총계가 보통주·우선주 각 1행). 총계 부재 연도만 말단 행 합산 폴백."""
        rows = self._conn.execute(
            "SELECT bsns_year, acqs_mth, acqs_qy, incnr_qy FROM tesstk_facts WHERE srtn_cd=?",
            (srtn_cd,),
        ).fetchall()
        out: dict[str, dict[str, float]] = {}
        totals_years = {str(r[0]) for r in rows if str(r[1]).startswith("총계")}
        for year, mth, acqs, incnr in rows:
            y_s, mth_s = str(year), str(mth)
            if y_s in totals_years:
                if not mth_s.startswith("총계"):
                    continue
            elif "소계" in mth_s:
                continue
            y = out.setdefault(y_s, {"acqs": 0.0, "incnr": 0.0})
            y["acqs"] += float(acqs or 0.0)
            y["incnr"] += float(incnr or 0.0)
        return out

    def split_history(self, srtn_cd: str) -> list[tuple[str, str]]:
        """(rcept_dt, report_nm) — 분할 관련 주요사항보고 이력(수집 창 내)."""
        return [
            (str(r[0]), str(r[1]))
            for r in self._conn.execute(
                "SELECT rcept_dt, report_nm FROM split_events WHERE srtn_cd=? ORDER BY rcept_dt",
                (srtn_cd,),
            )
        ]


def collect_returns(
    dart: DartClient,
    store: ReturnsStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    years: int = 5,
    year_now: int | None = None,
) -> tuple[int, int, list[str]]:
    """배당·자기주식 연간(11011) 수집 — 종목×연도 멱등. (적재, 스킵, 오류) 반환."""
    base_year = (year_now or now_kst().year) - 1  # 당해 사업보고서는 미공시
    loaded = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            store.record_attempt(srtn_cd, "alot", str(base_year), "no-corp-code")
            skipped += 1
            continue
        got = False
        try:
            for year in (str(base_year - off) for off in range(years)):
                for kind, fetch, upsert in (
                    ("alot", dart.alot_matter, store.upsert_alot),
                    ("tesstk", dart.treasury_stock, store.upsert_tesstk),
                ):
                    prev = store.attempted(srtn_cd, kind, year)
                    if prev is not None:
                        got = got or prev == "ok"
                        continue
                    rows = fetch(ent[0], year)
                    if rows:
                        upsert(srtn_cd, year, rows)
                        store.record_attempt(srtn_cd, kind, year, "ok")
                        got = True
                    else:
                        store.record_attempt(srtn_cd, kind, year, "empty")
        except CollectError as e:
            errors.append(f"{name}({srtn_cd}): {e}")  # 한도초과 등 — 시도 미기록(재시도 가능)
        if got:
            loaded += 1
        else:
            skipped += 1
    return loaded, skipped, errors


def collect_splits(
    dart: DartClient,
    store: ReturnsStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    lookback_years: int = 10,
    year_now: int | None = None,
) -> tuple[int, int, list[str]]:
    """분할 관련 주요사항보고 이력 수집 — 종목×창 멱등. (이력 보유 종목, 스킵, 오류)."""
    yn = year_now or now_kst().year
    bgn, end = f"{yn - lookback_years}0101", f"{yn}1231"
    period = f"{bgn}-{end}"
    found = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            store.record_attempt(srtn_cd, "split", period, "no-corp-code")
            skipped += 1
            continue
        if store.attempted(srtn_cd, "split", period) in ("ok", "empty"):
            if store.split_history(srtn_cd):
                found += 1
            continue
        try:
            rows = dart.disclosures_all(ent[0], bgn, end, pblntf_ty="B")
        except CollectError as e:
            errors.append(f"{name}({srtn_cd}): {e}")
            continue
        hits = [r for r in rows if "분할" in str(r.get("report_nm") or "")]
        if hits:
            store.add_splits(srtn_cd, hits)
            store.record_attempt(srtn_cd, "split", period, "ok")
            found += 1
        else:
            store.record_attempt(srtn_cd, "split", period, "empty")
    return found, skipped, errors


__all__ = [
    "DEFAULT_DB",
    "ReturnsStore",
    "collect_returns",
    "collect_splits",
]
