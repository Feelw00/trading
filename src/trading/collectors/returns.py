"""주주환원(배당·자기주식)·분할 이력 수집 — v1.8 ③ 예약분(운영자 결재 2026-09-01 착수).

원천(실호출 관측 확정 2026-09-01, `dart.py` 각 메서드 주석):
- 배당: ``alotMatter`` — 주당 배당금·수익률·성향 (연간 사업보고서 11011 기준)
- 자기주식: ``tesstkAcqsDspsSttus`` — 취득/처분/**소각**(change_qy_incnr) 수량
- 분할: ``list.json pblntf_ty=B``(주요사항보고) 중 report_nm에 "분할" 포함
  (LG화학 2020 물적분할이 "주요사항보고서(회사분할결정)"로 관측됨)
- 분할 방법(인적/물적, COLLECT-5 ② 실측 2026-09-04): DART DS005 ``cmpDvDecsn``(회사분할 결정)·
  ``cmpDvmgDecsn``(회사분할합병 결정) — ``dv_mth``/``dvmg_mth`` 원문에 "단순·인적분할"/"단순·물적분할",
  보조 ``ex_sm_r``(주총 특별결의 제외 사유 = '물적분할')·``mg_stn``. 원문 박제 + 순수 분류.

**접수분별 저장(2026-09-04, COLLECT-5 ①):** 리츠 22/23종은 반기·분기 결산이라 ``alotMatter(연도)``가
접수분 2~4건을 한 응답에 돌려준다(각 15행, ``stlm_dt`` 상이). 옛 표 ``alot_facts``(키: 종목·연도·항목·주식종류)는
첫 접수분만 남겨 연간 배당을 50~75% 과소 기록했다 → ``alot_reports``(접수번호·행 번호 키)에 병행 기록하고
읽기는 **결산기준일별 최신 접수분**(정정은 새 접수번호 = 같은 결산일 → 최신만)을 합산한다. 옛 표는 보존(append-only),
접수분 표가 없는 연도만 폴백.

수집기는 **사실 박제만** 한다 — 가점·네거티브 스크린 편입은 분포 실측 첨부 후
별도 결재(docs/POLICY_PARAMS.md §5 v1.8 ③·§6). append 전용(INSERT OR IGNORE),
attempts 테이블로 멱등 재실행. 결측은 None 박제(0 폴백 금지 — parse_amount).
"""

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading.collectors.base import CollectError, now_kst
from trading.collectors.dart import DartClient
from trading.collectors.fins import parse_amount

DEFAULT_DB = Path("data") / "returns.sqlite"
SOURCE_ALOT = "dart:alotMatter"
SOURCE_TESSTK = "dart:tesstkAcqsDspsSttus"
SOURCE_SPLIT = "dart:list/pblntf_ty=B"
SOURCE_DV = "dart:cmpDvDecsn"
SOURCE_DVMG = "dart:cmpDvmgDecsn"
SPLIT_KIND_DV = "분할"
SPLIT_KIND_DVMG = "분할합병"

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
CREATE TABLE IF NOT EXISTS alot_reports (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL, rcept_no TEXT NOT NULL, row_idx INTEGER NOT NULL,
  stlm_dt TEXT, se TEXT NOT NULL, stock_knd TEXT NOT NULL,
  thstrm REAL, frmtrm REAL, lwfr REAL, source TEXT, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, rcept_no, row_idx)
);
CREATE INDEX IF NOT EXISTS idx_alot_reports_sym ON alot_reports(srtn_cd, bsns_year);
CREATE TABLE IF NOT EXISTS split_decisions (
  srtn_cd TEXT NOT NULL, kind TEXT NOT NULL, rcept_no TEXT NOT NULL,
  bddd TEXT, method_text TEXT, method_hint TEXT, new_company TEXT, surviving_company TEXT,
  payload TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  UNIQUE(srtn_cd, kind, rcept_no)
);
"""


def _normalize_stock_kind(knd: str) -> str:
    """stock_knd 라벨 정규화 — 공백 제거 · '보통주식' → '보통주'. 그 외는 원문 유지(해석 금지)."""
    k = knd.strip()
    return "보통주" if k in ("보통주", "보통주식") else k


def is_par_based_yield(yield_pct: float | None, dps: float | None, par: float | None) -> bool:
    """'현금배당수익률' 값이 실은 액면 배당률(DPS ÷ 액면가 × 100)인지 — 순수 판정.

    DART 공시값은 소수 첫째 자리까지라 0.05 허용. 세 값 중 하나라도 없으면 판정 불가(False)."""
    if yield_pct is None or dps is None or par is None or par <= 0 or dps <= 0:
        return False
    return abs(yield_pct - dps / par * 100.0) < 0.05


def parse_dividend_rows(rows: Iterable[tuple[Any, Any, float | None]]) -> dict[str, float | None]:
    """한 보고서(접수분)의 (se, stock_knd, thstrm) 행 → {dps, yield_pct, payout_pct}. 순수.

    보통주 우선·'-'(단일 종류) 폴백·우선주 무시 · 주당 행 총액 오기재 가드(>100만원) · 액면 배당률 가드
    (`is_par_based_yield`). 라벨 정규화는 `_normalize_stock_kind`."""
    y: dict[str, float | None] = {"dps": None, "yield_pct": None, "payout_pct": None}
    par: float | None = None
    for se, knd, v in rows:
        se_s, knd_s = str(se), _normalize_stock_kind(str(knd))
        key: str | None = None
        if se_s.startswith("주당 현금배당금"):
            key = "dps"
            if v is not None and v > 1_000_000:
                v = None
        elif se_s.startswith("현금배당수익률"):
            key = "yield_pct"
        elif se_s.startswith("(연결)현금배당성향"):
            y["payout_pct"] = v
        elif se_s.startswith("주당액면가액"):
            if v is not None and v > 0 and (knd_s == "보통주" or par is None):
                par = float(v)
        if key is not None and v is not None and (knd_s == "보통주" or (knd_s == "-" and y[key] is None)):
            y[key] = v
    if is_par_based_yield(y["yield_pct"], y["dps"], par):
        y["yield_pct"] = None
    return y


def aggregate_reports(reports: Sequence[Mapping[str, float | None]]) -> dict[str, float | None]:
    """결산기준일이 다른 접수분들(반기·분기 결산) → 연도 값. 순수.

    dps·수익률은 **합**(각 접수분이 자기 기간 기준 — 이지스레지던스 150+150=300, SK리츠 70+66+66+66=268),
    성향(%)은 비율이라 가산 불가 → 접수분 1건일 때만, `n_reports` = 접수분 수(표시용)."""
    def _sum(k: str) -> float | None:
        vals = [r[k] for r in reports if r.get(k) is not None]
        return float(sum(v for v in vals if v is not None)) if vals else None

    return {
        "dps": _sum("dps"), "yield_pct": _sum("yield_pct"),
        "payout_pct": reports[0].get("payout_pct") if len(reports) == 1 else None,
        "n_reports": float(len(reports)),
    }


def classify_split_method(text: str | None, hint: str | None = None) -> str:
    """분할방법 원문(`dv_mth`/`dvmg_mth`) → '인적' | '물적' | '혼합' | '미상'. 순수.

    실관측(2026-09-04): "단순·인적분할"(토비스·KPX케미칼) · "단순·물적분할"(LG화학) · "단순ㆍ물적분할"(서흥, 가운뎃점
    변형) — 공백 제거 후 '인적분할'/'물적분할' 포함 여부. 둘 다면 혼합. 원문이 비면 보조 필드(`ex_sm_r`='물적분할' 등),
    그래도 없으면 미상(골프존 2024·서흥 2020 빈 레코드)."""
    t = re.sub(r"\s+", "", str(text or ""))
    a, b = "인적분할" in t, "물적분할" in t
    if a and b:
        return "혼합"
    if a:
        return "인적"
    if b:
        return "물적"
    h = re.sub(r"\s+", "", str(hint or ""))
    if "물적분할" in h:
        return "물적"
    if "인적분할" in h:
        return "인적"
    return "미상"


@dataclass(frozen=True)
class SplitDecision:
    kind: str        # 분할 | 분할합병
    rcept_no: str
    bddd: str | None
    cls: str         # 인적 | 물적 | 혼합 | 미상


@dataclass(frozen=True)
class SplitAssessment:
    """종목의 분할 이력 평가(순수) — 운영자 결재 2026-09-04(COLLECT-5 ② (a)):
    **인적분할은 강등 없음**(주주가치 중립), 물적·혼합·미상은 강등 유지, 구조화 API 미수록(2016~17 접수분)은 보수 유지."""

    n_events: int                                  # list.json 분할 주요사항보고 건수(정정 포함)
    decisions: tuple[SplitDecision, ...]

    @property
    def downgrade(self) -> int:
        """강등 사유 수 — 0이면 코어 자격 무관."""
        if not self.n_events:
            return 0
        if not self.decisions:
            return 1  # 미수록 — 구분 불가라 보수(강등) 유지
        return sum(1 for d in self.decisions if d.cls != "인적")

    @property
    def summary(self) -> str:
        if not self.n_events:
            return ""
        if not self.decisions:
            return f"미수록 {self.n_events}건"
        c = Counter(d.cls for d in self.decisions)
        return " · ".join(f"{k} {n}" for k, n in sorted(c.items()))


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
        # 접수분별 표(2026-09-04) — 접수번호·행 번호 키, 결산기준일 보존. 픽스처처럼 rcept_no가 없으면 ''(단일 접수분).
        rep = [
            (
                srtn_cd, year, str(r.get("rcept_no") or ""), idx, str(r.get("stlm_dt") or "") or None,
                str(r.get("se") or ""), str(r.get("stock_knd") or "-"),
                parse_amount(r.get("thstrm")), parse_amount(r.get("frmtrm")), parse_amount(r.get("lwfr")),
                SOURCE_ALOT, fetched,
            )
            for idx, r in enumerate(rows)
            if r.get("se")
        ]
        self._conn.executemany("INSERT OR IGNORE INTO alot_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rep)
        self._conn.commit()
        return self._conn.total_changes - before

    def has_alot_report(self, srtn_cd: str, year: str) -> bool:
        r = self._conn.execute(
            "SELECT 1 FROM alot_reports WHERE srtn_cd=? AND bsns_year=? LIMIT 1", (srtn_cd, year)
        ).fetchone()
        return r is not None

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

    def add_split_decisions(self, srtn_cd: str, kind: str, rows: list[dict[str, Any]]) -> int:
        """분할(합병) 결정 구조화 레코드 박제 — 원문 payload 전체 + 분류에 쓰는 열 승격."""
        fetched = now_kst().isoformat()
        src = SOURCE_DV if kind == SPLIT_KIND_DV else SOURCE_DVMG
        values = [
            (
                srtn_cd, kind, str(r.get("rcept_no") or ""),
                str(r.get("bddd") or "") or None,
                str(r.get("dv_mth") or r.get("dvmg_mth") or "") or None,
                str(r.get("ex_sm_r") or r.get("mg_stn") or "") or None,
                str(r.get("dvfcmp_cmpnm") or r.get("nmgcmp_cmpnm") or "") or None,
                str(r.get("atdv_excmp_cmpnm") or "") or None,
                json.dumps(r, ensure_ascii=False), src, fetched,
            )
            for r in rows
            if r.get("rcept_no")
        ]
        before = self._conn.total_changes
        self._conn.executemany("INSERT OR IGNORE INTO split_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
        self._conn.commit()
        return self._conn.total_changes - before

    # --- 읽기(분포·후속 결재 원료) ---
    def dividend_series(self, srtn_cd: str) -> dict[str, dict[str, float | None]]:
        """연도 → {dps, yield_pct, payout_pct, n_reports}.

        읽기 순서(2026-09-04): ① `alot_reports`(접수분별) — 연도 안에서 **결산기준일(stlm_dt)별 최신 접수번호**만
        남기고(정정 = 같은 결산일의 새 접수번호) 접수분을 `aggregate_reports`로 합산 ② 접수분 표가 없는 연도는
        옛 `alot_facts`(첫 접수분만 — n_reports None) 폴백. 행 해석은 `parse_dividend_rows`(보통주 우선·'-' 폴백·
        라벨 정규화·총액 오기재·액면 배당률 가드 — 실관측 2026-09-01 신세계I&C, 2026-09-04 흥국·리츠 '보통주식')."""
        out: dict[str, dict[str, float | None]] = {}
        by_year: dict[str, dict[str, tuple[str, list[tuple[Any, Any, float | None]]]]] = {}
        for year, rn, stlm, se, knd, v in self._conn.execute(
            "SELECT bsns_year, rcept_no, stlm_dt, se, stock_knd, thstrm FROM alot_reports "
            "WHERE srtn_cd=? ORDER BY bsns_year, rcept_no, row_idx",
            (srtn_cd,),
        ):
            by_year.setdefault(str(year), {}).setdefault(str(rn), (str(stlm or ""), []))[1].append((se, knd, v))
        for year, recs in by_year.items():
            latest_by_period: dict[str, str] = {}
            for rn, (stlm, _rows) in recs.items():
                if stlm not in latest_by_period or rn > latest_by_period[stlm]:
                    latest_by_period[stlm] = rn
            reports = [parse_dividend_rows(recs[rn][1]) for rn in sorted(latest_by_period.values())]
            out[year] = aggregate_reports(reports)
        legacy: dict[str, list[tuple[Any, Any, float | None]]] = {}
        for year, se, knd, v in self._conn.execute(
            "SELECT bsns_year, se, stock_knd, thstrm FROM alot_facts WHERE srtn_cd=?", (srtn_cd,)
        ):
            if str(year) not in out:
                legacy.setdefault(str(year), []).append((se, knd, v))
        for year, rows in legacy.items():
            y = parse_dividend_rows(rows)
            y["n_reports"] = None
            out[year] = y
        return out

    def split_assessment(self, srtn_cd: str) -> SplitAssessment:
        """분할 이력 평가 — list.json 이벤트 수 + 구조화 결정(분류)."""
        n_events = len(self.split_history(srtn_cd))
        rows = self._conn.execute(
            "SELECT kind, rcept_no, bddd, method_text, method_hint FROM split_decisions WHERE srtn_cd=? "
            "ORDER BY rcept_no",
            (srtn_cd,),
        ).fetchall()
        decisions = tuple(
            SplitDecision(kind=str(k), rcept_no=str(rn), bddd=str(b) if b else None, cls=classify_split_method(mt, mh))
            for k, rn, b, mt, mh in rows
        )
        return SplitAssessment(n_events=n_events, decisions=decisions)

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
                    if kind == "alot" and prev == "ok" and not store.has_alot_report(srtn_cd, year):
                        prev = None  # 접수분별 표(2026-09-04) 도입 전 적재 — 1회 재수집(자가 치유)
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


def collect_split_decisions(
    dart: DartClient,
    store: ReturnsStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    lookback_years: int = 10,
    year_now: int | None = None,
) -> tuple[int, int, list[str]]:
    """분할 이력(`split_history`) 보유 종목의 구조화 결정(cmpDvDecsn·분할합병이면 cmpDvmgDecsn) 수집.

    멱등 키 = 창 + 이력의 최신 접수일 — 새 분할 공시가 잡히면 재수집. (결정 확보 종목, 미수록/무이력 스킵, 오류)."""
    yn = year_now or now_kst().year
    bgn, end = f"{yn - lookback_years}0101", f"{yn}1231"
    got = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        hist = store.split_history(srtn_cd)
        if not hist:
            skipped += 1
            continue
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            skipped += 1
            continue
        period = f"{bgn}-{end}:{max(d for d, _ in hist)}"
        prev = store.attempted(srtn_cd, "dv_decsn", period)
        if prev in ("ok", "empty"):
            got += prev == "ok"
            continue
        try:
            rows_dv = dart.split_decisions(ent[0], bgn, end)
            rows_mg = (
                dart.split_merger_decisions(ent[0], bgn, end)
                if any("분할합병" in nm for _, nm in hist) else []
            )
        except CollectError as e:
            errors.append(f"{name}({srtn_cd}): {e}")
            continue
        n = store.add_split_decisions(srtn_cd, SPLIT_KIND_DV, rows_dv)
        n += store.add_split_decisions(srtn_cd, SPLIT_KIND_DVMG, rows_mg)
        if rows_dv or rows_mg:
            store.record_attempt(srtn_cd, "dv_decsn", period, "ok")
            got += 1
        else:
            store.record_attempt(srtn_cd, "dv_decsn", period, "empty")  # 2016~17 접수분 등 구조화 API 미수록
            skipped += 1
    return got, skipped, errors


__all__ = [
    "DEFAULT_DB",
    "SPLIT_KIND_DV",
    "SPLIT_KIND_DVMG",
    "ReturnsStore",
    "SplitAssessment",
    "SplitDecision",
    "aggregate_reports",
    "classify_split_method",
    "collect_returns",
    "collect_split_decisions",
    "collect_splits",
    "is_par_based_yield",
    "parse_dividend_rows",
]
