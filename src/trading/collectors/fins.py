"""재무 주요계정 캐시 — DART ``fnlttSinglAcnt`` → ``data/fins.sqlite`` (P-9 펀더멘털 축).

실호출 확인(2026-07-11, 팬오션 028670):
- 분기보고서(11013/11014)·반기(11012)의 IS 계정: ``thstrm_amount``=당기 분기,
  ``frmtrm_amount``=**전기 동일분기** → 콜 1번에 YoY 산출 가능.
- BS 계정: ``thstrm``=분기말, ``frmtrm``=직전 기말. 연간(11011): 당기/전기 연간.
- 금액은 콤마 문자열(KRW), ``fs_div`` CFS(연결)/OFS(별도) — CFS 우선, 없으면 OFS.
- 동일 계정 중복 행 존재(당기순이익 2행 실관측) — 첫 행 채택.

분기 공시는 시차가 있다(1Q=5월중·반기=8월중·3Q=11월중·사업보고서=3월말) —
수집은 최신 보고서부터 사다리로 시도해 **있는 것**을 저장하고, 없음(013)도 시도 기록으로
남겨 재호출을 막는다(분기당 1회 자연 갱신). ``as_of``=보고서 (연도, reprt_code) 명시 —
스냅샷 소비자는 어느 분기 데이터인지 안다(신선한 척 금지).
"""

import os
import sqlite3
import sys
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.collectors.base import CollectError, now_kst
from trading.collectors.dart import DartClient

DEFAULT_DB = Path("data") / "fins.sqlite"
SOURCE = "dart:fnlttSinglAcnt"

# 최신 우선 시도 사다리 — (연도 오프셋, reprt_code). 공시 시차를 코드가 아니라
# "빈 응답 → 다음 후보" 규칙이 흡수한다(달력 추측 없음).
_LADDER = (
    (0, "11014"),   # 당해 3분기 (11월중 공시)
    (0, "11012"),   # 당해 반기 (8월중)
    (0, "11013"),   # 당해 1분기 (5월중)
    (-1, "11011"),  # 전년 사업보고서 (3월말)
    (-1, "11014"),  # 전년 3분기 (연초 폴백)
)

_DDL = """
CREATE TABLE IF NOT EXISTS fin_facts (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL, reprt_code TEXT NOT NULL,
  fs_div TEXT NOT NULL, sj_div TEXT, account_nm TEXT NOT NULL,
  thstrm_amount REAL, frmtrm_amount REAL, currency TEXT,
  source TEXT, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, reprt_code, fs_div, account_nm)
);
CREATE TABLE IF NOT EXISTS fin_attempts (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL, reprt_code TEXT NOT NULL,
  status TEXT NOT NULL, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, reprt_code)
);
CREATE TABLE IF NOT EXISTS fin_reports (
  srtn_cd TEXT NOT NULL, bsns_year TEXT NOT NULL, reprt_code TEXT NOT NULL,
  rcept_no TEXT NOT NULL, source TEXT, fetched_at TEXT,
  UNIQUE(srtn_cd, bsns_year, reprt_code)
);
"""


def parse_amount(v: object) -> float | None:
    """콤마 금액 문자열 → float. 빈값·비수치는 None(0 폴백 금지)."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass(frozen=True)
class FinSnapshot:
    """한 종목의 최신 보고서 스냅샷 — 미취득 필드는 None(결측 명시)."""

    srtn_cd: str
    bsns_year: str
    reprt_code: str
    fs_div: str
    revenue: float | None
    revenue_prev: float | None
    op_income: float | None
    op_income_prev: float | None
    liabilities: float | None
    equity: float | None
    # v0.3(P-14): 밸류에이션(PER/ROE)용 — 실관측 계정명 "당기순이익(손실)"(startswith 매칭)
    net_income: float | None = None
    net_income_prev: float | None = None
    # COLLECT-6(2026-09-01): 지배기업 소유주지분 — CFS의 비지배지분 제외 자본.
    # fnlttSinglAcntAll에서 별도 수집(collect_owner_equity), 미수집·OFS는 None.
    owner_equity: float | None = None
    # 재무 통화(실관측: KRW 외 USD·CNY·JPY·GBP·HKD — 외국 상장 9xxxxx) —
    # 원화 시총과 혼합 금지(밸류에이션에서 제외 판단 원료)
    currency: str | None = None
    # P-20 ④(2026-09-04): 지배기업 소유주지분 귀속 당기순이익(연간 IS,
    # ifrs-full_ProfitLossAttributableToOwnersOfParent) — PER 분모. 미수집·OFS는 None.
    owner_net_income: float | None = None

    @property
    def rev_yoy(self) -> float | None:
        if self.revenue is None or not self.revenue_prev:
            return None
        return self.revenue / abs(self.revenue_prev) - 1

    @property
    def op_yoy(self) -> float | None:
        """영업이익 YoY — 전기가 0 이하면 증감률이 무의미해 None(부호 전환은 op_turned)."""
        if self.op_income is None or self.op_income_prev is None or self.op_income_prev <= 0:
            return None
        return self.op_income / self.op_income_prev - 1

    @property
    def op_turned_positive(self) -> bool:
        return (self.op_income or 0) > 0 >= (self.op_income_prev if self.op_income_prev is not None else 0) \
            and self.op_income_prev is not None

    @property
    def op_margin(self) -> float | None:
        if self.op_income is None or not self.revenue:
            return None
        return self.op_income / self.revenue

    @property
    def debt_ratio(self) -> float | None:
        if self.liabilities is None or not self.equity or self.equity <= 0:
            return None
        return self.liabilities / self.equity


class FinStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_DDL)

    def record_attempt(self, srtn_cd: str, year: str, reprt: str, status: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO fin_attempts VALUES (?,?,?,?,?)",
            (srtn_cd, year, reprt, status, now_kst().isoformat()),
        )
        self._conn.commit()

    def attempted(self, srtn_cd: str, year: str, reprt: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM fin_attempts WHERE srtn_cd=? AND bsns_year=? AND reprt_code=?",
            (srtn_cd, year, reprt),
        ).fetchone()
        return str(row[0]) if row else None

    def upsert(self, srtn_cd: str, year: str, reprt: str, rows: list[dict[str, Any]]) -> int:
        fetched = now_kst().isoformat()
        values = [
            (
                srtn_cd, year, reprt,
                str(r.get("fs_div") or ""), str(r.get("sj_div") or ""), str(r.get("account_nm") or ""),
                parse_amount(r.get("thstrm_amount")), parse_amount(r.get("frmtrm_amount")),
                str(r.get("currency") or ""), SOURCE, fetched,
            )
            for r in rows
            if r.get("account_nm")
        ]
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO fin_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", values
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def snapshot_for(self, srtn_cd: str, *, annual_only: bool = False) -> FinSnapshot | None:
        """저장분 중 최신 보고서(연도 desc → 3Q>반기>1Q>연간) 스냅샷. CFS 우선.

        ``annual_only=True``: 사업보고서(11011)만 — PER/PSR/ROE는 연간 IS 기준으로만
        산출한다(분기 연환산 추측 금지, 설계서 v0.3 §3 R2).
        """
        cond = " AND reprt_code='11011'" if annual_only else ""
        row = self._conn.execute(
            f"SELECT bsns_year, reprt_code FROM fin_facts WHERE srtn_cd=?{cond} "
            "ORDER BY bsns_year DESC, "
            "CASE reprt_code WHEN '11014' THEN 3 WHEN '11012' THEN 2 WHEN '11013' THEN 1 ELSE 0 END DESC "
            "LIMIT 1",
            (srtn_cd,),
        ).fetchone()
        if not row:
            return None
        year, reprt = str(row[0]), str(row[1])
        acc: dict[tuple[str, str], tuple[float | None, float | None]] = {}
        currency: str | None = None
        for fs, nm, th, fr, cur in self._conn.execute(
            "SELECT fs_div, account_nm, thstrm_amount, frmtrm_amount, currency FROM fin_facts "
            "WHERE srtn_cd=? AND bsns_year=? AND reprt_code=?",
            (srtn_cd, year, reprt),
        ):
            acc.setdefault((str(fs), str(nm)), (th, fr))  # 중복 계정은 첫 행(실관측 동일값)
            if currency is None and cur:
                currency = str(cur)
        fs_div = "CFS" if any(k[0] == "CFS" for k in acc) else "OFS"

        def _get(nm: str) -> tuple[float | None, float | None]:
            return acc.get((fs_div, nm), (None, None))

        def _get_prefix(prefix: str) -> tuple[float | None, float | None]:
            # 계정명 변형 흡수("당기순이익(손실)" 실관측) — fs_div 일치 + prefix 첫 행
            for (fs, nm), v in acc.items():
                if fs == fs_div and nm.startswith(prefix):
                    return v
            return (None, None)

        rev, rev_p = _get("매출액")
        op, op_p = _get("영업이익")
        liab, _ = _get("부채총계")
        eq, _ = _get("자본총계")
        ni, ni_p = _get_prefix("당기순이익")
        # COLLECT-6·P-20 ④: 정규화 계정명 **정확** 매칭 — DART 원문은 BS 지배주주지분과 IS 귀속 순이익이
        # 같은 계정명("지배기업 소유주지분")이라 prefix 매칭은 둘을 섞는다. 저장 시 IS는 OWNER_NI_NM으로 박제.
        own, _ = _get(OWNER_EQUITY_NM)
        own_ni, _ = _get(OWNER_NI_NM)
        return FinSnapshot(
            srtn_cd, year, reprt, fs_div, rev, rev_p, op, op_p, liab, eq,
            net_income=ni, net_income_prev=ni_p, owner_equity=own, currency=currency,
            owner_net_income=own_ni,
        )

    def symbols(self) -> list[str]:
        """재무가 1건 이상 적재된 종목코드 목록."""
        return [str(r[0]) for r in self._conn.execute("SELECT DISTINCT srtn_cd FROM fin_facts")]

    def latest_annual_year(self) -> str:
        """전 종목 최신 연간(11011) 사업연도 — 심사 원장 만료 기준(v2.4)."""
        row = self._conn.execute(
            "SELECT MAX(bsns_year) FROM fin_facts WHERE reprt_code='11011'"
        ).fetchone()
        return str(row[0]) if row and row[0] else "0000"

    def annual_series(self, srtn_cd: str) -> list[tuple[str, dict[str, float | None]]]:
        """연간(11011) 주요계정 시계열 (연도 desc) — 섹터 밴드(R3 1차 축)·loss_years 원료.

        각 연도: {"revenue", "op_income", "equity", "net_income"} (CFS 우선, 결측=None).
        """
        out: list[tuple[str, dict[str, float | None]]] = []
        years = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT DISTINCT bsns_year FROM fin_facts WHERE srtn_cd=? AND reprt_code='11011' "
                "ORDER BY bsns_year DESC",
                (srtn_cd,),
            )
        ]
        for year in years:
            rows = self._conn.execute(
                "SELECT fs_div, account_nm, thstrm_amount FROM fin_facts "
                "WHERE srtn_cd=? AND bsns_year=? AND reprt_code='11011'",
                (srtn_cd, year),
            ).fetchall()
            fs_div = "CFS" if any(str(r[0]) == "CFS" for r in rows) else "OFS"

            def _pick(exact: str | None, prefix: str | None = None) -> float | None:
                for fs, nm, th in rows:
                    if str(fs) != fs_div:
                        continue
                    name = str(nm)
                    if exact is not None and name == exact:
                        return th  # type: ignore[no-any-return]
                    if prefix is not None and name.startswith(prefix):
                        return th  # type: ignore[no-any-return]
                return None

            out.append(
                (
                    year,
                    {
                        "revenue": _pick("매출액"),
                        "op_income": _pick(None, "영업이익"),
                        "equity": _pick("자본총계"),
                        "net_income": _pick(None, "당기순이익"),
                        # 금융 프로파일(v1.4) top line — 은행 실관측 계정 "순이자손익"
                        "net_interest": _pick("순이자손익"),
                        # P-20 ④: 지배주주지분(BS)·귀속 순이익(IS) — 밴드 분모 승격·PER 분모(정확 매칭)
                        "owner_equity": _pick(OWNER_EQUITY_NM),
                        "owner_net_income": _pick(OWNER_NI_NM),
                    },
                )
            )
        return out

    # --- 사업보고서 접수일(P-20 ④ — 연간 재무 as-of 적용일) ---
    def record_report(self, srtn_cd: str, year: str, reprt: str, rcept_no: str) -> None:
        if not rcept_no:
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO fin_reports VALUES (?,?,?,?,?,?)",
            (srtn_cd, year, reprt, rcept_no, SOURCE_ALL, now_kst().isoformat()),
        )
        self._conn.commit()

    def annual_receipt_dates(self, srtn_cd: str) -> dict[int, str]:
        """연간(11011) 사업보고서 접수일 {사업연도: YYYYMMDD} — 접수번호 앞 8자리(실관측 14자리 = 접수일+일련)."""
        out: dict[int, str] = {}
        for year, rn in self._conn.execute(
            "SELECT bsns_year, rcept_no FROM fin_reports WHERE srtn_cd=? AND reprt_code='11011'", (srtn_cd,)
        ):
            d = str(rn)[:8]
            if len(d) == 8 and d.isdigit():
                out[int(year)] = d
        return out

    def annual_apply_dates(self, srtn_cd: str) -> dict[int, str]:
        """연간 재무 적용 시작일 {사업연도: YYYYMMDD} = 접수일 **다음날**(접수 당일 장중 공개 가능성 배제 — 보수)."""
        from datetime import timedelta

        out: dict[int, str] = {}
        for y, d in self.annual_receipt_dates(srtn_cd).items():
            out[y] = (datetime.strptime(d, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        return out

    def annual_net_incomes(self, srtn_cd: str) -> list[tuple[str, float | None]]:
        """연간(11011) 당기순이익 시계열 (연도 desc). 흑자 유지력(loss_years) 원료."""
        return [(year, vals["net_income"]) for year, vals in self.annual_series(srtn_cd)]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(DISTINCT srtn_cd) FROM fin_facts").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


def collect_fins(
    dart: DartClient,
    store: FinStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    now: datetime | None = None,
) -> tuple[int, int, list[str]]:
    """(srtn_cd, name) 각각 최신 보고서를 사다리로 수집. (적재 종목, 스킵, 오류) 반환.

    이미 스냅샷이 있는 종목이라도 **더 최신 후보 보고서가 미시도**면 시도한다(분기 자연 갱신).
    """
    year_now = (now or now_kst()).year
    loaded = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            store.record_attempt(srtn_cd, str(year_now), "none", "no-corp-code")
            skipped += 1
            continue
        got = False
        for off, reprt in _LADDER:
            year = str(year_now + off)
            prev = store.attempted(srtn_cd, year, reprt)
            if prev == "ok":
                got = True
                break
            if prev == "empty":
                continue
            try:
                rows = dart.financials(ent[0], year, reprt)
            except CollectError as e:
                errors.append(f"{name}({srtn_cd}) {year}/{reprt}: {e}")
                break  # 한도초과 등 — 이 종목 중단, 시도 기록 없음(재시도 가능)
            if rows:
                store.upsert(srtn_cd, year, reprt, rows)
                store.record_attempt(srtn_cd, year, reprt, "ok")
                got = True
                break
            store.record_attempt(srtn_cd, year, reprt, "empty")
        if got:
            loaded += 1
        else:
            skipped += 1
    return loaded, skipped, errors


OWNER_EQUITY_NM = "지배기업 소유주지분"      # 실관측 계정명(2026-09-01, COLLECT-6)
OWNER_EQUITY_ID = "ifrs-full_EquityAttributableToOwnersOfParent"
# P-20 ④(실관측 2026-09-04, 삼성전자 2025 CFS): IS 행 account_id 아래, account_nm은 BS와 **동일**("지배기업 소유주지분")
# → 저장은 정규화 이름(OWNER_NI_NM)으로, 매칭은 account_id로만. CIS(단일 포괄손익계산서)에도 같은 ID로 올 수 있다.
OWNER_NI_ID = "ifrs-full_ProfitLossAttributableToOwnersOfParent"
OWNER_NI_NM = "지배기업 소유주지분 귀속 당기순이익"
SOURCE_ALL = "dart:fnlttSinglAcntAll"
OWNER_ATTEMPT_SUFFIX = "-own"   # fin_attempts reprt_code 키: '11011-own' (1단계 '-all'과 비충돌)


def collect_owner_equity(
    dart: DartClient,
    store: FinStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
) -> tuple[int, int, list[str]]:
    """지배기업 소유주지분 수집(COLLECT-6) — 각 종목의 **최신 BS 스냅샷과 같은
    (연도, 보고서)** 기준으로 fnlttSinglAcntAll(CFS)에서 1계정만 뽑아 fin_facts에 append.

    - OFS 스냅샷(비지배지분 없음)·계정 부재는 skip으로 기록 — PBR은 자본총계 폴백(정직).
    - 멱등: fin_attempts에 reprt_code '<reprt>-all'로 별도 기록(주요계정 시도와 비충돌).
    반환: (적재, 스킵, 오류).
    """
    loaded = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        snap = store.snapshot_for(srtn_cd)
        ent = corp_map.get(srtn_cd)
        if snap is None or not ent or not ent[0]:
            skipped += 1
            continue
        attempt_reprt = f"{snap.reprt_code}-all"
        if store.attempted(srtn_cd, snap.bsns_year, attempt_reprt) is not None:
            if snap.owner_equity is not None:
                loaded += 1
            else:
                skipped += 1
            continue
        if snap.fs_div != "CFS":
            store.record_attempt(srtn_cd, snap.bsns_year, attempt_reprt, "ofs-skip")
            skipped += 1
            continue
        try:
            rows = dart.financials_all(ent[0], snap.bsns_year, snap.reprt_code, "CFS")
        except CollectError as e:
            errors.append(f"{name}({srtn_cd}): {e}")
            continue
        own = next(
            (
                r
                for r in rows
                if str(r.get("sj_div")) == "BS"
                and (
                    str(r.get("account_id")) == OWNER_EQUITY_ID
                    or str(r.get("account_nm", "")).startswith("지배기업")
                )
            ),
            None,
        )
        if own is None or parse_amount(own.get("thstrm_amount")) is None:
            store.record_attempt(srtn_cd, snap.bsns_year, attempt_reprt, "no-account")
            skipped += 1
            continue
        store.upsert(
            srtn_cd,
            snap.bsns_year,
            snap.reprt_code,
            [
                {
                    "fs_div": "CFS",
                    "sj_div": "BS",
                    "account_nm": OWNER_EQUITY_NM,
                    "thstrm_amount": own.get("thstrm_amount"),
                    "frmtrm_amount": own.get("frmtrm_amount"),
                    "currency": own.get("currency"),
                }
            ],
        )
        store.record_attempt(srtn_cd, snap.bsns_year, attempt_reprt, "ok")
        loaded += 1
    return loaded, skipped, errors


@dataclass
class OwnerBackfillResult:
    calls: int = 0        # 실제 API 호출 수(예산 대비)
    loaded: int = 0       # 지배주주지분 또는 귀속 순이익을 1건 이상 적재한 (종목, 연도)
    no_account: int = 0   # CFS는 있으나 두 계정 모두 부재
    empty: int = 0        # CFS 응답 없음(별도만 공시 — 비지배지분 없음)
    remaining: int = 0    # 예산 소진으로 남긴 (종목, 연도)
    errors: list[str] = field(default_factory=list)


def _pick_owner_rows(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """전체 재무제표 행에서 (BS 지배주주지분, IS 귀속 당기순이익) — IS 우선, 없으면 CIS. 순수."""
    own = next(
        (r for r in rows if str(r.get("sj_div")) == "BS"
         and (str(r.get("account_id")) == OWNER_EQUITY_ID or str(r.get("account_nm", "")).startswith("지배기업"))),
        None,
    )
    ni_rows = [r for r in rows if str(r.get("account_id")) == OWNER_NI_ID and str(r.get("sj_div")) in ("IS", "CIS")]
    ni_rows.sort(key=lambda r: 0 if str(r.get("sj_div")) == "IS" else 1)
    return own, (ni_rows[0] if ni_rows else None)


def backfill_owner_annuals(
    dart: DartClient,
    store: FinStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    years: int,
    max_calls: int,
    priority: Collection[str] = (),
    now: datetime | None = None,
) -> OwnerBackfillResult:
    """연간(11011) 지배주주지분(BS)·귀속 순이익(IS)·접수일 백필 — P-20 ④(운영자 지시 2026-09-04).

    작업 순서(예산 안에서): ① ``priority`` 종목(호출자 = R4 통과 — 밴드 분모 승격은 창 안 **전 연도**가 있어야
    하므로) × 대상 연도 전부 → ② 나머지 종목은 **최신 사업연도**부터 연도 바깥 루프(PER 분모가 최신 연도만
    쓰므로). 연간 주요계정(11011)이 'ok'인 (종목, 연도)만 대상(주요계정이 없으면 전체 재무제표도 없다 — 무호출).
    멱등 키 fin_attempts '11011-own'(ok·no-account·empty). CollectError(한도 초과 등)는 즉시 중단 — 시도 미기록이라
    다음 실행이 이어간다.
    """
    year_now = (now or now_kst()).year
    target_years = [str(y) for y in range(year_now - 1, year_now - 1 - years, -1)]
    key = f"11011{OWNER_ATTEMPT_SUFFIX}"
    res = OwnerBackfillResult()
    work: list[tuple[str, str, str, str]] = []  # (srtn_cd, name, corp, year)

    def _add(srtn_cd: str, name: str, year: str) -> None:
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            return
        if store.attempted(srtn_cd, year, "11011") != "ok":
            return
        if store.attempted(srtn_cd, year, key) is not None:
            return
        work.append((srtn_cd, name, ent[0], year))

    pri = set(priority)
    for srtn_cd, name in stocks:
        if srtn_cd in pri:
            for year in target_years:
                _add(srtn_cd, name, year)
    for year in target_years:
        for srtn_cd, name in stocks:
            if srtn_cd not in pri:
                _add(srtn_cd, name, year)
    for i, (srtn_cd, name, corp, year) in enumerate(work):
        if res.calls >= max_calls:
            res.remaining = len(work) - i
            break
        try:
            rows = dart.financials_all(corp, year, "11011", "CFS")
        except CollectError as e:
            res.errors.append(f"{name}({srtn_cd}) {year}: {e}")
            res.remaining = len(work) - i
            break
        res.calls += 1
        if not rows:
            store.record_attempt(srtn_cd, year, key, "empty")
            res.empty += 1
            continue
        own, ni = _pick_owner_rows(rows)
        facts: list[dict[str, Any]] = []
        if own is not None and parse_amount(own.get("thstrm_amount")) is not None:
            facts.append({"fs_div": "CFS", "sj_div": "BS", "account_nm": OWNER_EQUITY_NM,
                          "thstrm_amount": own.get("thstrm_amount"), "frmtrm_amount": own.get("frmtrm_amount"),
                          "currency": own.get("currency")})
        if ni is not None and parse_amount(ni.get("thstrm_amount")) is not None:
            facts.append({"fs_div": "CFS", "sj_div": "IS", "account_nm": OWNER_NI_NM,
                          "thstrm_amount": ni.get("thstrm_amount"), "frmtrm_amount": ni.get("frmtrm_amount"),
                          "currency": ni.get("currency")})
        rcept = next((str(r.get("rcept_no")) for r in rows if r.get("rcept_no")), "")
        store.record_report(srtn_cd, year, "11011", rcept)
        if not facts:
            store.record_attempt(srtn_cd, year, key, "no-account")
            res.no_account += 1
            continue
        store.upsert(srtn_cd, year, "11011", facts)
        store.record_attempt(srtn_cd, year, key, "ok")
        res.loaded += 1
    return res


def backfill_annuals(
    dart: DartClient,
    store: FinStore,
    corp_map: dict[str, tuple[str, str]],
    stocks: list[tuple[str, str]],
    *,
    years: int,
    now: datetime | None = None,
) -> tuple[int, int, list[str]]:
    """연간(11011) 보고서를 과거로 소급 수집 — v0.3 Phase 1 장기 백필.

    대상 연도: [작년-years+1 .. 작년] (당해 사업보고서는 미공시라 제외).
    attempts 테이블을 재사용해 재실행 시 기수집·무자료 연도를 건너뛴다(멱등).
    반환: (적재 시도 성공 건수, 스킵 건수, 오류).
    """
    year_now = (now or now_kst()).year
    target_years = [str(y) for y in range(year_now - 1, year_now - 1 - years, -1)]
    loaded = skipped = 0
    errors: list[str] = []
    for srtn_cd, name in stocks:
        ent = corp_map.get(srtn_cd)
        if not ent or not ent[0]:
            skipped += 1
            continue
        for year in target_years:
            prev = store.attempted(srtn_cd, year, "11011")
            if prev is not None:
                skipped += 1
                continue
            try:
                rows = dart.financials(ent[0], year, "11011")
            except CollectError as e:
                errors.append(f"{name}({srtn_cd}) {year}/11011: {e}")
                break  # 한도초과 등 — 이 종목 중단, 시도 기록 없음(재시도 가능)
            if rows:
                store.upsert(srtn_cd, year, "11011", rows)
                store.record_attempt(srtn_cd, year, "11011", "ok")
                loaded += 1
            else:
                store.record_attempt(srtn_cd, year, "11011", "empty")
                skipped += 1
    return loaded, skipped, errors


def main() -> int:
    from trading.collectors.market import MarketStore
    from trading.cycle.membership import snapshot_names
    from trading.cycle.policy import CURATED_GROUPS
    from trading.sectors import KRX_SOURCE

    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — 재무 수집 불가(blocked)")
        return 1
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    backfill_years = 0
    if "--backfill-years" in sys.argv:
        backfill_years = int(sys.argv[sys.argv.index("--backfill-years") + 1])

    # P-18 결재 ②(2026-08-31): 유니버스 = 전 상장. 시총 내림차순 — DART 일 한도에 걸려도
    # 대형주부터 적재되고, 잔여는 attempts 멱등으로 다음 실행에서 이어진다.
    # DART corp 미등재(우선주·ETF·스팩 일부·KDR)는 corp_map 단계에서 무호출 스킵.
    mstore = MarketStore()
    bas_dt = mstore.latest_date()
    quotes = mstore.quotes_on(bas_dt) if bas_dt else {}
    market_names = mstore.sector_names(KRX_SOURCE)
    mstore.close()
    stocks = [
        (cd, market_names.get(cd, cd))
        for cd, _cap in sorted(quotes.items(), key=lambda kv: -(kv[1] or 0.0))
    ]
    # 큐레이션 멤버 안전망 — 최신일 무시세(거래정지 등)여도 밴드 구성원이면 수집
    known = {cd for cd, _name in stocks}
    curated = {cd for codes in CURATED_GROUPS.values() for cd in codes} - known
    toss_names = snapshot_names()
    stocks += [(cd, toss_names.get(cd, cd)) for cd in sorted(curated)]
    if limit:
        stocks = stocks[:limit]
    if not stocks:
        print("시세 유니버스 비어 있음 — 스킵")
        return 0
    dart = DartClient(key)
    store = FinStore()
    corp_map = dart.corp_code_map()
    loaded, skipped, errors = collect_fins(dart, store, corp_map, stocks)
    print(f"재무 수집: 대상 {len(stocks)} · 확보 {loaded} · 미확보 {skipped}")
    if backfill_years:
        b_loaded, b_skipped, b_errors = backfill_annuals(
            dart, store, corp_map, stocks, years=backfill_years
        )
        errors.extend(b_errors)
        print(f"연간 백필({backfill_years}년): 적재 {b_loaded} · 스킵 {b_skipped}")
    n = store.count()
    store.close()
    print(f"DB 종목 {n}")
    for e in errors[:10]:
        print(f"⚠️ {e}")
    return 0


__all__ = [
    "DEFAULT_DB",
    "OWNER_ATTEMPT_SUFFIX",
    "OWNER_EQUITY_ID",
    "OWNER_EQUITY_NM",
    "OWNER_NI_ID",
    "OWNER_NI_NM",
    "FinSnapshot",
    "FinStore",
    "OwnerBackfillResult",
    "backfill_annuals",
    "backfill_owner_annuals",
    "collect_fins",
    "collect_owner_equity",
    "parse_amount",
]


if __name__ == "__main__":
    raise SystemExit(main())
