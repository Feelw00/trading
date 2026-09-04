"""자기 역사 PBR 밴드 — 회귀 여력의 앵커(policy v2.13, 운영자 결재 2026-09-03).

회귀 여력(%) = **밴드 중앙 PBR ÷ 현재 PBR − 1**. v2.12까지의 분자 "섹터 중앙 PBR"은 KRX
버킷 이질성 때문에 폐기했다 — '일반서비스'에 바이오·플랫폼(PBR 40~50)과 자산형 용역업이
한 바구니라 와이엔텍 +362%·한국종합기술 +455%가 나왔고(KG케미칼 지주 착시에 이은 2번째
유형), 종목은 **자기 역사**와만 비교한다. 회귀는 가정이지 예측이 아니라는 원칙은 그대로다
(밴드 중앙 = 최근 5년 중 절반의 거래일은 그보다 비쌌다는 사실 이상을 말하지 않는다).

산식(순수 코드 — 헌법 절대금지 2, 결측=None — 대체·추측 금지):
- 일별 PBR = 종가 × **그날 상장주식수** ÷ 연간 자본총계(as-of). 시총을 날짜별 주식수로
  재계산하므로 분할·소각·증자에 안전하다.
- 자본총계 as-of(룩어헤드 금지): 사업연도 y의 연간(11011) 자본총계는 **y+1년 4월 1일부터**
  적용(12월 결산 사업보고서 제출 기한 = 3월 말). 그 전 거래일은 y−1. 해당 연도 결측이면 한 해
  더 거슬러 폴백, 그래도 없으면 그 거래일은 표본에서 제외한다. 연도별 연결(CFS) 우선·별도(OFS)
  폴백은 `FinStore.annual_series`와 동일(재사용 — 대한약품처럼 별도만 공시하는 종목).
  ※ 3월 결산 등 비12월 결산 종목은 4/1 규칙이 수개월 룩어헤드가 될 수 있다(소수 — 알려진
  단순화, 결산월 수집 시 정밀화).
- **현재점도 같은 잣대**(최신 거래일 종가 × 주식수 ÷ 연간 자본). 밸류에이션 레코드의
  PBR(지배주주지분·최신 분기 BS, COLLECT-6)과 기준이 다르지만 여력은 **비율**이라 대부분
  상쇄된다.
- **분모 승격(P-20 ④, 2026-09-04):** 연간 **지배기업 소유주지분**이 밴드에 걸릴 수 있는 최근
  ``BAND_FISCAL_YEARS`` 사업연도 전부에 있을 때만 현재점·과거점을 **동시에** 지배주주지분으로
  바꾼다(한쪽만 바꾸면 편향). 하나라도 빠지면 전부 자본총계 유지(`equity_basis`에 표기).
- **적용일 정밀화(P-20 ④):** 연간 재무의 적용 시작일은 **사업보고서 접수일 다음날**
  (`FinStore.annual_receipt_dates` — 전체 재무제표 응답의 ``rcept_no`` 앞 8자리). 12월 결산은
  3월 중하순, 3월·6월 결산은 결산 후 3개월 시점이 되어 4/1 단순화의 룩어헤드가 사라진다.
  접수일이 없는 연도는 종전 y+1년 4월 1일 규칙. 정정 사업보고서는 접수일이 늦어져 보수적.
- 창 = 최근 ``WINDOW_DAYS``(1,250 거래일 ≈ 5년 — 사이클 한 바퀴를 담고 2021 버블은 중앙값이
  완화. 운영자 결재 2026-09-03: 3년·가용 전체 대안 중 5년 고정). 표본 < ``MIN_DAYS``(500 ≈ 2년)
  는 결측 — 신규 상장은 승인 노출·페이퍼 등록이 막힌다(섹터 폴백 없음: 구 결함 재유입 금지).
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from trading.collectors.fins import DEFAULT_DB as FINS_DB
from trading.collectors.fins import FinStore

WINDOW_DAYS = 1250          # ≈ 5년 거래일(운영자 결재 2026-09-03)
MIN_DAYS = 500              # ≈ 2년 미만 이력은 결측
ANNUAL_APPLY_MMDD = "0401"  # 사업연도 y의 연간 자본총계 적용 시작(기본) = y+1년 4월 1일
BAND_FISCAL_YEARS = 7       # 5년 창 + as-of 시차가 닿는 사업연도 수 — 분모 승격 판정 범위
MARKET_DB = Path("data") / "market.sqlite"
BASIS_TOTAL = "연간 자본총계"
BASIS_OWNER = "연간 지배주주지분"


@dataclass(frozen=True)
class PbrBand:
    symbol: str
    current: float      # 최신 거래일 PBR(밴드와 같은 잣대 — 연간 자본총계)
    median: float       # 창 내 일별 PBR 중앙값 = 회귀 앵커
    low: float
    high: float
    n_days: int         # 표본 거래일 수(자본총계 결측일 제외)
    last_bas_dt: str
    equity_basis: str   # 현재점 분모 설명(예: "FY2025 연간 자본총계")

    @property
    def upside_pct(self) -> float:
        """캡 미적용 밴드 여력(%) = 중앙 ÷ 현재 − 1 — 산술이지 예측이 아니다. 시스템 여력은
        `regression_upside`(v2.14 정당 PBR 캡 적용)를 쓴다."""
        return (self.median / self.current - 1.0) * 100.0


# --- policy v2.14(운영자 결재 2026-09-03): 정당 PBR 상한 캡 ---------------------------------
# 목표 PBR = min(밴드 중앙, 정당 PBR), 정당 PBR = (ROE − g) ÷ (COE − g) (Gordon 유도 — CFA L2·다모다란).
# 밴드 중앙 회귀는 "과거처럼 평가받는다"는 가정인데 ROE가 자기자본비용을 밑돌면 그 과거 배수는 정당화되지
# 않는다. ROE = 5년 중앙(정상화 이익 — 경기민감주 정상화 관행을 이 하나로 흡수). **하향 캡만, 상향 없음.**
# 실측(2026-09-03, 원장+큐 42종): 19종 발동, 승인 6종 중 원림(ROE 3.3%)·동일고무벨트(3.7%)·산돌(7.5%) 이탈,
# 아이퀘스트 +134%→+57%. 파라미터 민감도 큼(원림 COE9/g0 +31% ~ COE12/g2 −44%) — 값 변경은 결재 사항
# (docs/POLICY_PARAMS v2.14). 조사 근거: docs/research/2026-09-03-target-price-industry-practice.md.
JUSTIFIED_COE = 0.10   # 자기자본비용(운영자 결재 — 국내 금융 리서치 참고치 12~15%의 완화 쪽)
JUSTIFIED_G = 0.01     # 장기 성장률


def justified_pbr(
    roe: float | None, *, coe: float = JUSTIFIED_COE, g: float = JUSTIFIED_G,
) -> float | None:
    """정당 PBR = (ROE − g) ÷ (COE − g). ROE 결측이면 None. ROE ≤ g면 0(회귀 근거 없음)."""
    if roe is None or coe <= g:
        return None
    return max((roe - g) / (coe - g), 0.0)


@dataclass(frozen=True)
class TargetPbr:
    value: float            # 목표 PBR = min(밴드 중앙, 정당 PBR)
    anchor: str             # "band" | "justified"(캡 발동)
    band_median: float
    justified: float


def target_pbr(band: PbrBand, roe: float | None) -> TargetPbr | None:
    """목표 PBR. ROE 결측이면 None — 캡을 검증할 수 없으면 여력을 지어내지 않는다."""
    j = justified_pbr(roe)
    if j is None:
        return None
    if j < band.median:
        return TargetPbr(value=j, anchor="justified", band_median=band.median, justified=j)
    return TargetPbr(value=band.median, anchor="band", band_median=band.median, justified=j)


def regression_upside(band: PbrBand | None, roe: float | None) -> float | None:
    """회귀 여력(%) = 목표 PBR ÷ 현재 PBR − 1 (v2.14). 산술이지 예측이 아니다.

    밴드 결측(이력 500거래일 미만·자본총계 결측)·ROE 5년 중앙 결측은 None — 섹터 폴백 없음.
    """
    if band is None or band.current <= 0:
        return None
    t = target_pbr(band, roe)
    return (t.value / band.current - 1.0) * 100.0 if t is not None else None


def fiscal_year_asof(bas_dt: str) -> int:
    """거래일(YYYYMMDD)에 공개돼 있는 최신 사업연도 — 4/1 이후 전년, 그 전은 전전년."""
    y = int(bas_dt[:4])
    return y - 1 if bas_dt[4:8] >= ANNUAL_APPLY_MMDD else y - 2


def apply_date(year: int, apply_from: Mapping[int, str] | None = None) -> str:
    """사업연도 ``year`` 연간 재무의 적용 시작일(YYYYMMDD) — 접수일 다음날이 있으면 그것, 없으면 y+1년 4/1."""
    if apply_from is not None:
        d = apply_from.get(year)
        if d:
            return d
    return f"{year + 1}{ANNUAL_APPLY_MMDD}"


def equity_asof(
    equities: Mapping[int, float], bas_dt: str, apply_from: Mapping[int, str] | None = None,
) -> tuple[int, float] | None:
    """as-of 사업연도의 (연도, 자본). 거래일에 이미 공개된(적용일 ≤ 거래일) 연도 중 최신.

    결측이면 한 해 폴백(기본 규칙 기준 전전년까지), 그래도 없거나 ≤0이면 None(지어내지 않음).
    ``apply_from``(P-20 ④)이 있으면 연도별 접수일 다음날을 적용일로 쓴다 — 12월 결산은 4/1보다
    앞당겨지고, 비12월 결산은 실제 공개 시점으로 늦춰진다."""
    floor = fiscal_year_asof(bas_dt) - 1
    ok = [
        y for y, e in equities.items()
        if e is not None and e > 0 and y >= floor and apply_date(y, apply_from) <= bas_dt
    ]
    if not ok:
        return None
    y = max(ok)
    return y, equities[y]


def build_band(
    symbol: str,
    quotes: Sequence[tuple[str, float, float]],
    equities: Mapping[int, float],
    *,
    window_days: int = WINDOW_DAYS,
    min_days: int = MIN_DAYS,
    apply_from: Mapping[int, str] | None = None,
    basis_label: str = BASIS_TOTAL,
) -> PbrBand | None:
    """일별 (bas_dt, 종가, 상장주식수) — **최신순** — 와 연도별 자본 → 밴드.

    창은 최근 ``window_days`` 거래일(자본 결측일은 창 안에서 제외만, 창을 늘리지 않는다).
    표본이 ``min_days`` 미만이면 None. ``apply_from``·``basis_label``은 P-20 ④(접수일 적용·분모 승격).
    """
    series: list[float] = []
    basis: str | None = None
    last_dt: str | None = None
    for bas_dt, close, shares in quotes[:window_days]:
        if close <= 0 or shares <= 0:
            continue
        eq = equity_asof(equities, bas_dt, apply_from)
        if eq is None:
            continue
        series.append(close * shares / eq[1])
        if basis is None:
            basis = f"FY{eq[0]} {basis_label}"
            last_dt = bas_dt
    if len(series) < min_days or basis is None or last_dt is None:
        return None
    return PbrBand(
        symbol=symbol, current=series[0], median=float(statistics.median(series)),
        low=min(series), high=max(series), n_days=len(series), last_bas_dt=last_dt,
        equity_basis=basis,
    )


def choose_equities(
    total: Mapping[int, float], owner: Mapping[int, float], *, fiscal_years: int = BAND_FISCAL_YEARS,
) -> tuple[dict[int, float], str]:
    """분모 승격 판정(순수): 자본총계가 있는 최근 ``fiscal_years`` 연도 **전부**에 지배주주지분이 있으면
    그 연도들을 지배주주지분으로(현재점·과거점 동시), 아니면 자본총계 그대로. (연도→값, 라벨)."""
    recent = sorted((y for y, e in total.items() if e > 0), reverse=True)[:fiscal_years]
    if recent and all(owner.get(y, 0.0) > 0 for y in recent):
        return {y: owner[y] for y in recent}, BASIS_OWNER
    return {y: e for y, e in total.items() if e > 0}, BASIS_TOTAL


def _annual_equities(fins: FinStore, symbol: str) -> tuple[dict[int, float], dict[int, str], str]:
    """연도별 (자본, 적용일, 분모 라벨) — 자본총계·지배주주지분(P-20 ④ 승격 판정)·접수일 다음날."""
    total: dict[int, float] = {}
    owner: dict[int, float] = {}
    for year, acc in fins.annual_series(symbol):
        e = acc.get("equity")
        if e is not None and e > 0:
            total[int(year)] = float(e)
        o = acc.get("owner_equity")
        if o is not None and o > 0:
            owner[int(year)] = float(o)
    equities, label = choose_equities(total, owner)
    return equities, fins.annual_apply_dates(symbol), label


_IN_CHUNK = 500  # SQLite 바인딩 상한(999) 아래


def _quotes_desc_many(
    conn: sqlite3.Connection, symbols: Sequence[str], limit: int,
) -> dict[str, list[tuple[str, float, float]]]:
    """심볼 → 최신순 (bas_dt, 종가, 주식수) 최대 ``limit``개.

    daily_quotes의 유일 인덱스가 (bas_dt, srtn_cd) 순이라 종목별 조회는 매번 전체 스캔이다 —
    IN 절로 한 번에 읽고 파이썬에서 종목별로 자른다(35종목 기준 3초 → 1초 미만).
    """
    out: dict[str, list[tuple[str, float, float]]] = {s: [] for s in symbols}
    for i in range(0, len(symbols), _IN_CHUNK):
        chunk = symbols[i:i + _IN_CHUNK]
        rows = conn.execute(
            "SELECT srtn_cd, bas_dt, clpr, lstg_st_cnt FROM daily_quotes "
            f"WHERE srtn_cd IN ({','.join('?' * len(chunk))}) ORDER BY srtn_cd, bas_dt DESC",
            tuple(chunk),
        ).fetchall()
        for s, d, c, n in rows:
            bucket = out[str(s)]
            if len(bucket) >= limit:
                continue
            try:
                bucket.append((str(d), float(c), float(n)))
            except (TypeError, ValueError):
                continue  # 빈 문자열 등 — 그 거래일만 제외
    return out


def pbr_bands(
    symbols: Iterable[str],
    *,
    market_db: Path = MARKET_DB,
    fins_db: Path = FINS_DB,
    window_days: int = WINDOW_DAYS,
    min_days: int = MIN_DAYS,
) -> dict[str, PbrBand | None]:
    """심볼 → 밴드(결측 None). 시세 DB 읽기 전용 + 재무 DB(연간 자본총계, CFS→OFS)."""
    syms = list(dict.fromkeys(symbols))
    if not syms:
        return {}
    fins = FinStore(fins_db)
    mconn = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
    try:
        quotes = _quotes_desc_many(mconn, syms, window_days)
        out: dict[str, PbrBand | None] = {}
        for s in syms:
            equities, apply_from, label = _annual_equities(fins, s)
            out[s] = build_band(
                s, quotes[s], equities,
                window_days=window_days, min_days=min_days, apply_from=apply_from, basis_label=label,
            )
        return out
    finally:
        mconn.close()
        fins.close()


__all__ = [
    "BAND_FISCAL_YEARS", "BASIS_OWNER", "BASIS_TOTAL", "apply_date", "choose_equities",
    "JUSTIFIED_COE", "JUSTIFIED_G", "MIN_DAYS", "WINDOW_DAYS", "PbrBand", "TargetPbr",
    "build_band", "equity_asof", "fiscal_year_asof", "justified_pbr", "pbr_bands",
    "regression_upside", "target_pbr",
]
