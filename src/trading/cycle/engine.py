"""R3 국면 판정 엔진 — 순수 결정론(설계서 v0.3 §3 R3). LLM·재량 없음.

판정 규칙(PIVOT-7 ②):
- 1차 축(자체 히스토리): PBR 밴드 percentile · 마진 밴드 percentile · 매출 사이클 z.
  **하나라도 결측이면 unknown**(CycleRecord 스키마가 강제 — 부분 관측으로 국면을 지어내지 않는다).
- **bottoming = 밴드 하단 + 개선 시작** — 하단이되 개선이 없으면 declining 유지(위치≠반전).
- 구조적 사양(secular_decline) = 섹터 매출 장기 CAGR 하향 — 밴드가 낮아도 편입 불가(R4에서 차단).

파라미터: ``CycleParams``로 명시 주입. ``PROPOSED_PARAMS``는 **policy-v1.0으로 결재됨**
(2026-08-27, docs/POLICY_PARAMS.md §1 — 검증 사이클 운송·창고 2024 PASS). 개정은 R7+결재로만.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from trading.contracts.longterm import CyclePhase, CycleRecord, PrimaryAxes
from trading.cycle.bands import SectorYear
from trading.valuation.metrics import percentile_rank

# policy-v1.0 결재값(2026-08-27) — docs/POLICY_PARAMS.md §1과 동기
@dataclass(frozen=True)
class CycleParams:
    band_low: float = 0.30        # PBR 밴드 하단 임계(이하 = 저평가 존)
    band_high: float = 0.75       # 과열 임계(이상)
    min_band_points: int = 3      # percentile 판정 최소 히스토리 점수(현재 제외)
    min_z_points: int = 4         # 매출 z 판정 최소 관측 수
    secular_window: int = 8       # 매출 장기 추세 창(연)
    secular_min_obs: int = 5      # 추세 판정 최소 관측 연수
    secular_max_cagr: float = 0.0  # CAGR가 이 이하면 구조적 사양


PROPOSED_PARAMS = CycleParams()


@dataclass(frozen=True)
class Assessment:
    sector: str
    at: str                        # 판정 기준 연도 라벨("current" 또는 "2024" 등)
    phase: CyclePhase
    temperature: int | None
    pbr_band_pct: float | None
    margin_band_pct: float | None
    rev_cycle_z: float | None
    improving: bool | None         # 개선 모멘텀(마진 or 매출 YoY 전년 대비 상승)
    secular_decline: bool | None
    n_pbr_history: int


def _yoy_series(rows: Sequence[SectorYear]) -> list[tuple[str, float]]:
    """연간 매출 YoY [(연도, yoy)] — 연도 desc(rows가 desc 정렬 전제), 결측 연도 스킵."""
    ann = [(r.year, r.revenue) for r in rows if r.year != "current" and r.revenue is not None]
    out: list[tuple[str, float]] = []
    for (y, rev), (_py, prev) in zip(ann, ann[1:]):
        if prev and prev > 0:
            out.append((y, rev / prev - 1))
    return out


def _secular(rows: Sequence[SectorYear], params: CycleParams) -> bool | None:
    """섹터 매출 장기 추세 — 창 내 CAGR ≤ 임계면 구조적 사양. 관측 부족 = None."""
    ann = [(r.year, r.revenue) for r in rows if r.year != "current" and r.revenue is not None]
    window = ann[: params.secular_window]
    if len(window) < params.secular_min_obs:
        return None
    latest, oldest = window[0][1], window[-1][1]
    assert latest is not None and oldest is not None
    if oldest <= 0:
        return None
    years = len(window) - 1
    cagr = float((latest / oldest) ** (1 / years)) - 1
    return cagr <= params.secular_max_cagr


def assess(rows: Sequence[SectorYear], *, at: str, sector: str, params: CycleParams) -> Assessment:
    """``at`` 시점(연도 라벨) 기준 국면 판정 — 그 시점 이전 데이터만 사용(룩어헤드 금지)."""
    if at == "current":
        cur = next((r for r in rows if r.year == "current"), None)
        hist = [r for r in rows if r.year != "current"]
        cur_pbr = cur.pbr if cur else None
        # 현재 시점의 재무 축은 최신 연간 연도 기준
        fin_rows = hist
    else:
        cur_year = next((r for r in rows if r.year == at), None)
        cur_pbr = cur_year.pbr if cur_year else None
        hist = [r for r in rows if r.year != "current" and r.year < at]
        fin_rows = [r for r in rows if r.year != "current" and r.year <= at]

    # 축 1 — PBR 밴드
    pbr_hist = [r.pbr for r in hist if r.pbr is not None]
    pbr_pct = (
        percentile_rank([*pbr_hist, cur_pbr], cur_pbr)
        if cur_pbr is not None and len(pbr_hist) >= params.min_band_points
        else None
    )

    # 축 2 — 마진 밴드(연간 기준 — 최신 관측 연도)
    margins = [(r.year, r.margin) for r in fin_rows if r.margin is not None]
    margin_pct = None
    if len(margins) >= params.min_band_points + 1:
        latest_margin = margins[0][1]
        assert latest_margin is not None
        margin_pct = percentile_rank([m for _y, m in margins if m is not None], latest_margin)

    # 축 3 — 매출 사이클 z
    yoys = _yoy_series(fin_rows)
    rev_z = None
    if len(yoys) >= params.min_z_points:
        vals = [v for _y, v in yoys]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        if var > 0:
            rev_z = (vals[0] - mean) / var**0.5

    # 개선 모멘텀 — 마진 or 매출 YoY가 직전 관측 대비 상승
    improving: bool | None = None
    margin_up = margins[0][1] > margins[1][1] if len(margins) >= 2 else None
    yoy_up = yoys[0][1] > yoys[1][1] if len(yoys) >= 2 else None
    if margin_up is not None or yoy_up is not None:
        improving = bool(margin_up) or bool(yoy_up)

    secular = _secular(fin_rows, params)

    if pbr_pct is None or margin_pct is None or rev_z is None or improving is None:
        phase = CyclePhase.UNKNOWN
        temperature = None
    else:
        band = pbr_pct
        if band <= params.band_low:
            phase = CyclePhase.BOTTOMING if improving else CyclePhase.DECLINING
        elif band >= params.band_high:
            phase = CyclePhase.OVERHEATED
        else:
            phase = CyclePhase.RECOVERING if improving else CyclePhase.DECLINING
        temperature = round(100 * (pbr_pct + margin_pct) / 2)

    return Assessment(
        sector=sector,
        at=at,
        phase=phase,
        temperature=temperature,
        pbr_band_pct=pbr_pct,
        margin_band_pct=margin_pct,
        rev_cycle_z=rev_z,
        improving=improving,
        secular_decline=secular,
        n_pbr_history=len(pbr_hist),
    )


def to_record(a: Assessment, *, as_of: datetime, fetched_at: datetime, evidence: list[str]) -> CycleRecord:
    """Assessment → CycleRecord(§4 계약). 스키마 검증이 unknown 규율을 이중 강제."""
    return CycleRecord(
        id=f"cyc.{as_of.strftime('%Y%m%d')}.{a.sector}",
        as_of=as_of,
        fetched_at=fetched_at,
        source="derived:cycle-bands",
        industry=a.sector,
        phase=a.phase,
        temperature=a.temperature,
        axes_primary=PrimaryAxes(
            sector_pbr_band_pct=a.pbr_band_pct,
            sector_margin_band_pct=a.margin_band_pct,
            sector_rev_cycle_z=a.rev_cycle_z,
        ),
        axes_aux={},
        secular_decline=a.secular_decline,
        evidence=evidence,
    )


def assess_all(
    sector_years: Mapping[str, Sequence[SectorYear]], *, at: str, params: CycleParams
) -> list[Assessment]:
    out = [assess(rows, at=at, sector=sector, params=params) for sector, rows in sector_years.items()]
    return sorted(out, key=lambda a: (a.pbr_band_pct if a.pbr_band_pct is not None else 2.0))


__all__ = [
    "Assessment",
    "CycleParams",
    "PROPOSED_PARAMS",
    "assess",
    "assess_all",
    "to_record",
]
