"""종목 상태 스냅샷(관리종목·거래정지·시장경고) + 감사의견 저장소 — data/status.sqlite (SCREEN-1).

소스·의미는 2026-09-03 실측으로 확정(`docs/research/2026-09-03-screen1-status-audit-sources.md`,
OPEN_QUESTIONS SCREEN-1). 필드 추측 금지 — 관측된 키만 읽고 나머지는 payload로 박제한다.

KIS 주식현재가 시세(`KisClient.quote_price`, TR FHKST01010100) output 상태 필드 — 양성 대조군 관측:
- ``mang_issu_cls_code == "Y"`` = **관리종목**(온타이드·형지글로벌 8/12 신규 지정; 정지 중 종목도 Y).
- ``iscd_stat_cls_code`` ``"51"`` 관리종목(거래 중) · ``"58"`` **매매거래정지**(이오플로우·테라사이언스 등) ·
  ``"00"`` ∧ mang None ∧ 현재가 0 = **상장폐지/무자료**(노블엠앤비·선샤인푸드) · 55/57 정상(신용/증거금 구분
  추정 — 필터 무관, 값만 박제).
- ⚠ ``temp_stop_yn``은 정지 종목에서도 'N' — 거래정지 지표가 **아니다**. 정지는 58로만 읽는다.
- ``mrkt_warn_cls_code``는 '00'만 관측(투자주의·경고·위험 코드값 미관측 — 박제만, 해석 금지).
- 시계열 없음(현재값) → append-only 일일 스냅샷 UNIQUE(symbol, as_of). 같은 날 재실행은 무호출 스킵(멱등·비용).
- 비용 실측(9/3 전수): 호출당 ≈0.16s(페이싱 0.12s + 지연) → 2,669종 단일 스레드 ≈ 7분. eod-v3 끝 단계라
  **4스레드 병렬(≈2분) + 시간 예산(기본 12분 — 지연 급증 시에도 18:30 감시 슬롯 안에 체인 종료, 초과분은 다음 실행)**.
  토큰은 첫 호출을 직렬로 워밍(병렬 첫 호출의 재발급 경합 방지).

감사의견(DART `accnutAdtorNmNdAdtOpinion`)의 수집은 `collectors/audit.py`, 저장은 이 모듈의 StatusStore.
**판정 게이트 적용은 운영자 결재 (a)(2026-09-03)** — R4 하드 필터 배선은 `screen/`이 담당하고,
이 모듈은 사실 수집·순수 분류(`classify_kis`)만 제공한다.
"""

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from trading.collectors.base import now_kst

DEFAULT_DB = Path("data") / "status.sqlite"
KIS_SOURCE = "kis:inquire-price"
AUDIT_SOURCE = "dart:accnutAdtorNmNdAdtOpinion"

# 관측된 상태 키만(2026-09-03 실호출 80키 중) — 열로 승격. 그 외는 payload에 원문 박제.
KIS_STATUS_KEYS: tuple[str, ...] = (
    "iscd_stat_cls_code", "mang_issu_cls_code", "mrkt_warn_cls_code", "temp_stop_yn",
    "sltr_yn", "short_over_yn", "invt_caful_yn", "crdt_able_yn", "rprs_mrkt_kor_name",
)
STAT_MANAGED = "51"
STAT_HALTED = "58"
STAT_NONE = "00"
OPINION_CLEAN = "적정의견"   # 관측 어휘. 그 외 문자열(의견거절·한정·부적정 등)은 전부 비적정으로 본다.
DEFAULT_BUDGET_MIN = 12.0    # eod-v3 끝 단계 시간 예산(분) — 18:30 감시 슬롯 안에 체인 종료(9/2 실측 체인 12분)

_DDL = """
CREATE TABLE IF NOT EXISTS kis_status (
  symbol TEXT NOT NULL, as_of TEXT NOT NULL,
  iscd_stat_cls_code TEXT, mang_issu_cls_code TEXT, mrkt_warn_cls_code TEXT,
  temp_stop_yn TEXT, sltr_yn TEXT, short_over_yn TEXT, invt_caful_yn TEXT, crdt_able_yn TEXT,
  rprs_mrkt_kor_name TEXT, last_price REAL, payload TEXT NOT NULL,
  source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  UNIQUE(symbol, as_of)
);
CREATE INDEX IF NOT EXISTS idx_kis_status_symbol ON kis_status(symbol, as_of);
CREATE TABLE IF NOT EXISTS audit_opinions (
  symbol TEXT NOT NULL, corp_code TEXT NOT NULL, fy TEXT NOT NULL, reprt_code TEXT NOT NULL,
  rcept_no TEXT NOT NULL, row_idx INTEGER NOT NULL, term_label TEXT, is_current INTEGER NOT NULL,
  adtor TEXT, adt_opinion TEXT, emphs_matter TEXT, core_adt_matter TEXT, spcmnt_matter TEXT,
  stlm_dt TEXT, corp_cls TEXT, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  UNIQUE(symbol, fy, reprt_code, rcept_no, row_idx)
);
CREATE TABLE IF NOT EXISTS audit_fetch_log (
  symbol TEXT NOT NULL, fy TEXT NOT NULL, reprt_code TEXT NOT NULL,
  fetched_at TEXT NOT NULL, status TEXT NOT NULL, n_rows INTEGER NOT NULL
);
"""


class QuoteClient(Protocol):
    """KisClient의 시세 부분 — 테스트 대역용 최소 표면."""

    def quote_price(self, srtn_cd: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class KisStatusRow:
    symbol: str
    as_of: str
    iscd_stat_cls_code: str | None
    mang_issu_cls_code: str | None
    mrkt_warn_cls_code: str | None
    temp_stop_yn: str | None
    sltr_yn: str | None
    short_over_yn: str | None
    invt_caful_yn: str | None
    crdt_able_yn: str | None
    rprs_mrkt_kor_name: str | None
    last_price: float | None


@dataclass(frozen=True)
class KisFlags:
    """순수 분류 — 관측 의미(모듈 주석)만 적용. None = 관측 불가(추측 금지)."""

    managed: bool | None          # 관리종목
    halted: bool | None           # 매매거래정지
    delisted_suspect: bool        # 상장폐지/무자료 의심(00·None·현재가 0)
    warn_code: str | None         # 시장경고 코드 원문('00' 외 값은 해석하지 않고 그대로)

    @property
    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.delisted_suspect:
            out.append("상장폐지/무자료 의심(KIS 상태 00·현재가 0)")
        if self.managed:
            out.append("관리종목(KIS mang_issu_cls_code=Y)")
        if self.halted:
            out.append("매매거래정지(KIS iscd_stat_cls_code=58)")
        return out


@dataclass(frozen=True)
class AuditRow:
    symbol: str
    corp_code: str
    fy: str
    reprt_code: str
    rcept_no: str
    row_idx: int
    term_label: str | None
    is_current: bool
    adtor: str | None
    adt_opinion: str | None
    emphs_matter: str | None
    core_adt_matter: str | None
    stlm_dt: str | None


def _s(v: Any) -> str | None:
    """빈 문자열·None → None, 그 외 문자열화(관측값 원문 보존)."""
    if v is None:
        return None
    t = str(v)
    return t if t != "" else None


def _price(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


_TERM_RE = re.compile(r"제\s*(\d+)\s*기")


def term_no(label: str | None) -> int | None:
    """"제57기 (당기)" → 57. 라벨 없음·'-' → None."""
    m = _TERM_RE.search(label or "")
    return int(m.group(1)) if m else None


def mark_current(labels: Sequence[str | None]) -> list[bool]:
    """응답 한 묶음(같은 접수분)의 기수 라벨 → 당기 여부. 실측(2026-09-03 전수):
    ① "(당기)" 표기가 있으면 그 행 ② 없으면(샘표식품 등 "제10기"만) **최상위 기수** 행 — 응답 순서가
    당기→전기→전전기라 최상위 기수 = 당기. "(전기)·(전전기)" 표기 행은 제외. 라벨 '-'(미제출)는 전부 False."""
    if any(lb and "당기" in lb for lb in labels):
        return [bool(lb and "당기" in lb) for lb in labels]
    nos = [term_no(lb) for lb in labels]
    mx = max((n for n in nos if n is not None), default=None)
    if mx is None:
        return [False] * len(labels)
    return [n == mx and not any(k in (lb or "") for k in ("전기",)) for n, lb in zip(nos, labels, strict=True)]


def classify_kis(row: KisStatusRow) -> KisFlags:
    stat, mang = row.iscd_stat_cls_code, row.mang_issu_cls_code
    delisted = stat == STAT_NONE and mang is None and (row.last_price is None or row.last_price <= 0)
    if delisted:
        return KisFlags(managed=None, halted=None, delisted_suspect=True, warn_code=row.mrkt_warn_cls_code)
    managed = None if (mang is None and stat is None) else (mang == "Y" or stat == STAT_MANAGED)
    halted = None if stat is None else stat == STAT_HALTED
    return KisFlags(managed=managed, halted=halted, delisted_suspect=False, warn_code=row.mrkt_warn_cls_code)


class StatusStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    # --- KIS 상태 스냅샷 ---

    def observed_on(self, as_of: str) -> set[str]:
        return {str(r[0]) for r in self._conn.execute("SELECT symbol FROM kis_status WHERE as_of=?", (as_of,))}

    def append_kis(self, symbol: str, as_of: str, out: Mapping[str, Any], *, fetched_at: str) -> bool:
        """일일 스냅샷 1행(append-only, 같은 날 두 번째 관측은 무시). 적재 여부 반환."""
        cols = [_s(out.get(k)) for k in KIS_STATUS_KEYS]
        payload = {k: out.get(k) for k in (*KIS_STATUS_KEYS, "stck_prpr", "bstp_kor_isnm")}
        before = self._conn.total_changes
        self._conn.execute(
            "INSERT OR IGNORE INTO kis_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, as_of, *cols, _price(out.get("stck_prpr")),
             json.dumps(payload, ensure_ascii=False), KIS_SOURCE, fetched_at),
        )
        self._conn.commit()
        return self._conn.total_changes > before

    @staticmethod
    def _kis_row(r: Sequence[Any]) -> KisStatusRow:
        return KisStatusRow(
            symbol=str(r[0]), as_of=str(r[1]),
            iscd_stat_cls_code=_s(r[2]), mang_issu_cls_code=_s(r[3]), mrkt_warn_cls_code=_s(r[4]),
            temp_stop_yn=_s(r[5]), sltr_yn=_s(r[6]), short_over_yn=_s(r[7]), invt_caful_yn=_s(r[8]),
            crdt_able_yn=_s(r[9]), rprs_mrkt_kor_name=_s(r[10]),
            last_price=float(r[11]) if r[11] is not None else None,
        )

    _KIS_COLS = ("symbol, as_of, iscd_stat_cls_code, mang_issu_cls_code, mrkt_warn_cls_code, temp_stop_yn, "
                 "sltr_yn, short_over_yn, invt_caful_yn, crdt_able_yn, rprs_mrkt_kor_name, last_price")

    def latest_kis(self, symbol: str) -> KisStatusRow | None:
        r = self._conn.execute(
            f"SELECT {self._KIS_COLS} FROM kis_status WHERE symbol=? ORDER BY as_of DESC LIMIT 1", (symbol,)
        ).fetchone()
        return self._kis_row(r) if r else None

    def latest_kis_all(self) -> dict[str, KisStatusRow]:
        """종목별 최신 스냅샷(as_of 최대)."""
        rows = self._conn.execute(
            f"SELECT {self._KIS_COLS} FROM kis_status k WHERE as_of = "
            "(SELECT MAX(as_of) FROM kis_status WHERE symbol=k.symbol)"
        ).fetchall()
        return {str(r[0]): self._kis_row(r) for r in rows}

    def kis_coverage(self) -> tuple[int, int, str | None]:
        r = self._conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(DISTINCT as_of), MAX(as_of) FROM kis_status").fetchone()
        return (int(r[0]), int(r[1]), str(r[2]) if r and r[2] else None)

    # --- 감사의견 ---

    def append_audit(
        self, symbol: str, corp_code: str, fy: str, reprt_code: str, rows: Iterable[Mapping[str, Any]],
        *, fetched_at: str,
    ) -> int:
        """응답 행을 순서(row_idx)대로 박제. 같은 rcept_no는 무시(정정 = 새 rcept_no = 새 버전). 신규 행 수."""
        before = self._conn.total_changes
        rows = list(rows)
        current = mark_current([_s(r.get("bsns_year")) for r in rows])
        for i, r in enumerate(rows):
            label = _s(r.get("bsns_year"))
            self._conn.execute(
                "INSERT OR IGNORE INTO audit_opinions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol, corp_code, fy, reprt_code, _s(r.get("rcept_no")) or "", i, label,
                    1 if current[i] else 0,
                    _s(r.get("adtor")), _s(r.get("adt_opinion")), _s(r.get("emphs_matter")),
                    _s(r.get("core_adt_matter")), _s(r.get("adt_reprt_spcmnt_matter")),
                    _s(r.get("stlm_dt")), _s(r.get("corp_cls")), AUDIT_SOURCE, fetched_at,
                ),
            )
        self._conn.commit()
        return self._conn.total_changes - before

    def log_audit_fetch(self, symbol: str, fy: str, reprt_code: str, *, fetched_at: str, status: str, n_rows: int) -> None:
        self._conn.execute(
            "INSERT INTO audit_fetch_log VALUES (?,?,?,?,?,?)", (symbol, fy, reprt_code, fetched_at, status, n_rows)
        )
        self._conn.commit()

    def audit_rows(self, symbol: str, fy: str, reprt_code: str = "11011") -> list[AuditRow]:
        rows = self._conn.execute(
            "SELECT symbol, corp_code, fy, reprt_code, rcept_no, row_idx, term_label, is_current, adtor, "
            "adt_opinion, emphs_matter, core_adt_matter, stlm_dt FROM audit_opinions "
            "WHERE symbol=? AND fy=? AND reprt_code=? ORDER BY rcept_no, row_idx",
            (symbol, fy, reprt_code),
        ).fetchall()
        return [
            AuditRow(
                symbol=str(r[0]), corp_code=str(r[1]), fy=str(r[2]), reprt_code=str(r[3]), rcept_no=str(r[4]),
                row_idx=int(r[5]), term_label=_s(r[6]), is_current=bool(r[7]), adtor=_s(r[8]),
                adt_opinion=_s(r[9]), emphs_matter=_s(r[10]), core_adt_matter=_s(r[11]), stlm_dt=_s(r[12]),
            )
            for r in rows
        ]

    def audit_symbols(self, fy: str, reprt_code: str = "11011") -> list[str]:
        return [str(r[0]) for r in self._conn.execute(
            "SELECT DISTINCT symbol FROM audit_opinions WHERE fy=? AND reprt_code=?", (fy, reprt_code)
        )]

    def close(self) -> None:
        self._conn.close()


def collect_kis_status(
    client: QuoteClient,
    store: StatusStore,
    symbols: Sequence[str],
    *,
    today: str,
    skip_observed: bool = True,
    workers: int = 4,
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, int, list[str], int]:
    """종목별 현재 상태 스냅샷. (신규 행, 호출 수, 오류, 예산 초과로 미관측 종목 수) — 한 종목 실패가 나머지를 막지 않는다.

    ``skip_observed``: 오늘 이미 관측된 종목은 무호출(체인 재실행·수동 선행 수집 시 비용 0).
    ``workers``: 병렬 호출 수(저장은 호출 스레드 단일). ``budget_s``: 벽시계 예산 — 초과 시 남은 종목은 다음 실행.
    """
    done = store.observed_on(today) if skip_observed else set()
    todo = [sym for sym in symbols if sym not in done]
    fetched_at = now_kst().isoformat()
    start = clock()
    added = calls = 0
    errors: list[str] = []

    def _fetch(sym: str) -> tuple[str, dict[str, Any] | None, Exception | None]:
        try:
            return sym, client.quote_price(sym), None
        except Exception as exc:  # noqa: BLE001 — 격리
            return sym, None, exc

    def _absorb(res: tuple[str, dict[str, Any] | None, Exception | None]) -> None:
        nonlocal added
        sym, out, exc = res
        if exc is not None:
            errors.append(f"{sym}: {exc}")
        elif not out:
            errors.append(f"{sym}: 빈 응답")
        elif store.append_kis(sym, today, out, fetched_at=fetched_at):
            added += 1

    remaining = 0
    if todo:
        calls += 1
        _absorb(_fetch(todo[0]))  # 토큰 워밍(직렬) — 병렬 첫 호출의 재발급 경합 방지
        rest = todo[1:]
        chunk = max(1, workers) * 8
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for i in range(0, len(rest), chunk):
                if budget_s is not None and clock() - start > budget_s:
                    remaining = len(rest) - i
                    break
                batch = rest[i:i + chunk]
                calls += len(batch)
                for res in ex.map(_fetch, batch):
                    _absorb(res)
    return added, calls, errors, remaining


def flagged_summary(store: StatusStore) -> dict[str, list[str]]:
    """최신 스냅샷 기준 관리·정지·상폐 의심 종목 목록(보고용)."""
    out: dict[str, list[str]] = {"managed": [], "halted": [], "delisted_suspect": []}
    for sym, row in sorted(store.latest_kis_all().items()):
        f = classify_kis(row)
        if f.delisted_suspect:
            out["delisted_suspect"].append(sym)
        if f.managed:
            out["managed"].append(sym)
        if f.halted:
            out["halted"].append(sym)
    return out


def main(argv: list[str] | None = None) -> int:
    """`python -m trading.collectors.status [--limit N] [--symbols A B …] [--budget-min M]` — 재무 유니버스 전수 스냅샷."""
    import sys

    from trading.collectors.fins import FinStore
    from trading.collectors.kis import client_from_env

    args = list(sys.argv[1:] if argv is None else argv)
    client = client_from_env()
    if client is None:
        print("KIS 키 미설정 — 종목 상태 스냅샷 blocked")
        return 0
    if "--symbols" in args:
        symbols = args[args.index("--symbols") + 1:]
    else:
        fs = FinStore()
        try:
            symbols = sorted(fs.symbols())
        finally:
            fs.close()
    if "--limit" in args:
        symbols = symbols[: int(args[args.index("--limit") + 1])]
    budget_min = float(args[args.index("--budget-min") + 1]) if "--budget-min" in args else DEFAULT_BUDGET_MIN
    today = now_kst().strftime("%Y-%m-%d")
    store = StatusStore()
    try:
        added, calls, errors, remaining = collect_kis_status(
            client, store, symbols, today=today, budget_s=budget_min * 60 if budget_min > 0 else None,
        )
        n_sym, n_days, latest = store.kis_coverage()
        flags = flagged_summary(store)
    finally:
        store.close()
    print(f"종목 상태 스냅샷(SCREEN-1, KIS): 대상 {len(symbols)} · 호출 {calls} · 신규 {added} · 실패 {len(errors)}"
          f"{f' · 예산 초과 미관측 {remaining}(다음 실행)' if remaining else ''} → "
          f"data/status.sqlite ({n_sym}종목 · {n_days}일자 · 최신 {latest})")
    print(f"  관리종목 {len(flags['managed'])} · 거래정지 {len(flags['halted'])} · 상폐/무자료 의심 {len(flags['delisted_suspect'])}"
          f" — 필터 적용은 R4(운영자 결재 (a) 2026-09-03)")
    for e in errors[:5]:
        print(f"  ⚠️ {e}")
    return 0


__all__ = [
    "AUDIT_SOURCE",
    "DEFAULT_BUDGET_MIN",
    "DEFAULT_DB",
    "KIS_SOURCE",
    "KIS_STATUS_KEYS",
    "OPINION_CLEAN",
    "AuditRow",
    "KisFlags",
    "KisStatusRow",
    "QuoteClient",
    "StatusStore",
    "classify_kis",
    "collect_kis_status",
    "flagged_summary",
    "main",
    "mark_current",
    "term_no",
]


if __name__ == "__main__":
    raise SystemExit(main())
