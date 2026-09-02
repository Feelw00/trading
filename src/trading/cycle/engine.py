"""R3 국면 판정 엔진 — 순수 결정론(설계서 v0.3 §3 R3). LLM·재량 없음.

판정 규칙 v2(운영자 결재 2026-09-01 — 회복 중의성 제거·불가능 전이 차단.
근거 실측: 전이 이력 9건 중 7건이 사이클상 불가능(과열→회복 3·회복→바닥 3·바닥→하강 1)):
- 1차 축(자체 히스토리): PBR 밴드 percentile · 마진 밴드 percentile · 매출 사이클 z.
  **하나라도 결측이면 unknown**(CycleRecord 스키마가 강제 — 부분 관측으로 국면을 지어내지 않는다).
- **위치 × 방향**: 하단=바닥, 상단=과열, 중간=직전 산출 대비 밴드 방향(Δ)으로
  상승=회복 / 하락=둔화(slowing). 데드밴드(±2%p) 내 방향 불변 시 직전 국면 유지
  (회복·둔화였을 때만), 방향 판정 불가(직전 밴드 없음)면 unknown.
- **개선(마진·매출)은 국면 판정에서 분리** — 보조 지표(Assessment.improving 표기만).
- **전이 규율(settle_phase)**: 인접 전이만 즉시 확정. 비인접(예: 과열→바닥)은 직전 확정
  국면 유지 + "재판정 대기", 같은 원시 판정 2회 연속이면 반영 + "재계산" 표기 —
  데이터 리비전과 실제 이동을 구분한다.
- 구조적 사양(secular_decline) = 섹터 매출 장기 CAGR 하향 — 밴드가 낮아도 편입 불가(R4에서 차단).

파라미터: ``CycleParams``로 명시 주입. ``PROPOSED_PARAMS``는 policy-v1.0(2026-08-27) +
**v2 개정 결재(2026-09-01, docs/POLICY_PARAMS.md §1)**. 개정은 R7+결재로만.
"""

from collections.abc import Callable, Mapping, Sequence
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
    direction_dead_band: float = 0.02  # v2: 중간 밴드 방향 판정 데드밴드(±2%p — 노이즈 무시)
    min_band_points: int = 3      # percentile 판정 최소 히스토리 점수(현재 제외)
    min_z_points: int = 4         # 매출 z 판정 최소 관측 수
    secular_window: int = 8       # 매출 장기 추세 창(연)
    secular_min_obs: int = 5      # 추세 판정 최소 관측 연수
    secular_max_cagr: float = 0.0  # CAGR가 이 이하면 구조적 사양


PROPOSED_PARAMS = CycleParams()


@dataclass(frozen=True)
class Assessment:
    """판정 결과 — profile=financial이면 margin_band_pct에 ROE 밴드, rev_cycle_z에
    topline z가 담긴다(표시 편의 — 박제 계약(CycleRecord)은 프로파일별 전용 필드로 분리)."""

    sector: str
    at: str                        # 판정 기준 연도 라벨("current" 또는 "2024" 등)
    phase: CyclePhase
    temperature: int | None
    pbr_band_pct: float | None
    margin_band_pct: float | None
    rev_cycle_z: float | None
    improving: bool | None         # 개선 모멘텀(축2 or base YoY 전년 대비 상승)
    secular_decline: bool | None
    n_pbr_history: int
    profile: str = "industrial"


def _yoy_series(
    rows: Sequence[SectorYear], get: "Callable[[SectorYear], float | None]"
) -> list[tuple[str, float]]:
    """연간 base YoY [(연도, yoy)] — 연도 desc(rows가 desc 정렬 전제), 결측 연도 스킵.

    base: industrial=매출, financial=topline(순이자손익·폴백 영업이익) — v1.4 프로파일.
    """
    ann = [(r.year, v) for r in rows if r.year != "current" and (v := get(r)) is not None]
    out: list[tuple[str, float]] = []
    for (y, rev), (_py, prev) in zip(ann, ann[1:]):
        if prev and prev > 0:
            out.append((y, rev / prev - 1))
    return out


def _secular(
    rows: Sequence[SectorYear], params: CycleParams, get: "Callable[[SectorYear], float | None]"
) -> bool | None:
    """섹터 base(매출/topline) 장기 추세 — 창 내 CAGR ≤ 임계면 구조적 사양. 관측 부족 = None."""
    ann = [(r.year, v) for r in rows if r.year != "current" and (v := get(r)) is not None]
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


def assess(
    rows: Sequence[SectorYear],
    *,
    at: str,
    sector: str,
    params: CycleParams,
    profile: str = "industrial",
    prev_band_pct: float | None = None,
    prev_phase: CyclePhase | None = None,
) -> Assessment:
    """``at`` 시점(연도 라벨) 기준 국면 판정 — 그 시점 이전 데이터만 사용(룩어헤드 금지).

    profile(policy-v1.4): industrial=(PBR·마진·매출) / financial=(PBR·ROE·topline) —
    금융업은 매출액이 구조적으로 없어 축을 대체한다(규칙 형태는 동형).
    """
    if profile == "financial":
        axis2_get: Callable[[SectorYear], float | None] = lambda r: r.roe
        base_get: Callable[[SectorYear], float | None] = lambda r: r.topline
    else:
        axis2_get = lambda r: r.margin  # noqa: E731
        base_get = lambda r: r.revenue  # noqa: E731
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

    # 축 2 — 마진(industrial)/ROE(financial) 밴드(연간 기준 — 최신 관측 연도)
    margins = [(r.year, v) for r in fin_rows if (v := axis2_get(r)) is not None]
    margin_pct = None
    if len(margins) >= params.min_band_points + 1:
        latest_margin = margins[0][1]
        assert latest_margin is not None
        margin_pct = percentile_rank([m for _y, m in margins if m is not None], latest_margin)

    # 축 3 — 매출(industrial)/topline(financial) 사이클 z
    yoys = _yoy_series(fin_rows, base_get)
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

    secular = _secular(fin_rows, params, base_get)

    if pbr_pct is None or margin_pct is None or rev_z is None or improving is None:
        phase = CyclePhase.UNKNOWN
        temperature = None
    else:
        band = pbr_pct
        if band <= params.band_low:
            phase = CyclePhase.BOTTOMING       # v2: 위치만 — 개선 여부는 보조 지표
        elif band >= params.band_high:
            phase = CyclePhase.OVERHEATED
        else:
            # v2: 중간 밴드는 방향으로 — 상승=회복 / 하락=둔화 / 판정 불가=unknown
            if prev_band_pct is None:
                phase = CyclePhase.UNKNOWN
            else:
                delta = band - prev_band_pct
                if delta > params.direction_dead_band:
                    phase = CyclePhase.RECOVERING
                elif delta < -params.direction_dead_band:
                    phase = CyclePhase.SLOWING
                elif prev_phase in (CyclePhase.RECOVERING, CyclePhase.SLOWING):
                    phase = prev_phase        # 데드밴드 내 — 방향 불변 간주
                else:
                    phase = CyclePhase.UNKNOWN
        temperature = round(100 * (pbr_pct + margin_pct) / 2) if phase is not CyclePhase.UNKNOWN else None

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
        profile=profile,
    )


# v2 전이 규율 — 인접 전이(사이클 순방향 + 중간 밴드 방향 반전 + 회복 실패)만 즉시 인정.
_ADJACENT: frozenset[tuple[CyclePhase, CyclePhase]] = frozenset(
    {
        (CyclePhase.BOTTOMING, CyclePhase.RECOVERING),   # 바닥 → 상승 진입
        (CyclePhase.RECOVERING, CyclePhase.OVERHEATED),  # 상승 → 상단 도달
        (CyclePhase.OVERHEATED, CyclePhase.SLOWING),     # 상단 → 조정
        (CyclePhase.SLOWING, CyclePhase.BOTTOMING),      # 조정 → 하단 재진입
        (CyclePhase.RECOVERING, CyclePhase.SLOWING),     # 중간 밴드 방향 반전
        (CyclePhase.SLOWING, CyclePhase.RECOVERING),     # 중간 밴드 방향 반전
        (CyclePhase.RECOVERING, CyclePhase.BOTTOMING),   # 회복 실패 — 하단 복귀
    }
)


def settle_phase(
    raw: CyclePhase,
    prev_confirmed: CyclePhase | None,
    prev_raw: CyclePhase | None,
) -> tuple[CyclePhase, str | None]:
    """(확정 국면, 표기 사유) — 비인접 전이는 2회 연속 관측 전까지 직전 확정 유지.

    unknown은 관측 부족 상태라 전이 규율 대상이 아니다(자유 통과). v1 레거시
    declining에서의 전이도 규율 미적용(첫 v2 산출은 재라벨이지 이동이 아님).
    """
    if prev_confirmed is None or raw == prev_confirmed:
        return raw, None
    if CyclePhase.UNKNOWN in (raw, prev_confirmed) or prev_confirmed is CyclePhase.DECLINING:
        return raw, None
    if (prev_confirmed, raw) in _ADJACENT:
        return raw, None
    if prev_raw == raw:
        return raw, f"재계산 — 비인접 전이({prev_confirmed.value}→{raw.value}) 2회 연속 관측"
    return prev_confirmed, f"재판정 대기 — 비인접 {raw.value} 관측 1회(확정은 2회 연속부터)"


def to_record(
    a: Assessment,
    *,
    as_of: datetime,
    fetched_at: datetime,
    evidence: list[str],
    phase: CyclePhase | None = None,
    phase_note: str | None = None,
) -> CycleRecord:
    """Assessment → CycleRecord(§4 계약). 스키마 검증이 unknown 규율을 이중 강제.

    ``phase``: settle_phase 확정값(전이 규율 적용 시) — 주면 원시 판정(a.phase)은
    phase_raw로 박제된다. 안 주면 v1 호환(원시 판정 그대로, raw/note 없음)."""
    return CycleRecord(
        id=f"cyc.{as_of.strftime('%Y%m%d')}.{a.sector}",
        as_of=as_of,
        fetched_at=fetched_at,
        source="derived:cycle-bands",
        industry=a.sector,
        phase=phase if phase is not None else a.phase,
        phase_raw=a.phase if phase is not None else None,
        phase_note=phase_note,
        temperature=a.temperature,
        axes_primary=(
            PrimaryAxes(
                profile="financial",
                sector_pbr_band_pct=a.pbr_band_pct,
                sector_roe_band_pct=a.margin_band_pct,
                sector_topline_cycle_z=a.rev_cycle_z,
            )
            if a.profile == "financial"
            else PrimaryAxes(
                sector_pbr_band_pct=a.pbr_band_pct,
                sector_margin_band_pct=a.margin_band_pct,
                sector_rev_cycle_z=a.rev_cycle_z,
            )
        ),
        axes_aux={},
        secular_decline=a.secular_decline,
        evidence=evidence,
    )


def assess_all(
    sector_years: Mapping[str, Sequence[SectorYear]],
    *,
    at: str,
    params: CycleParams,
    financial_groups: frozenset[str] = frozenset(),
    prev_states: Mapping[str, tuple[float | None, CyclePhase | None]] | None = None,
) -> list[Assessment]:
    """``prev_states``: 섹터 → (직전 밴드 pct, 직전 확정 국면) — v2 방향 판정 원료.
    미제공 섹터는 중간 밴드에서 unknown(정직 — 방향을 지어내지 않는다)."""
    prev = prev_states or {}
    out = [
        assess(
            rows,
            at=at,
            sector=sector,
            params=params,
            profile="financial" if sector in financial_groups else "industrial",
            prev_band_pct=prev.get(sector, (None, None))[0],
            prev_phase=prev.get(sector, (None, None))[1],
        )
        for sector, rows in sector_years.items()
    ]
    return sorted(out, key=lambda a: (a.pbr_band_pct if a.pbr_band_pct is not None else 2.0))


__all__ = [
    "Assessment",
    "CycleParams",
    "PROPOSED_PARAMS",
    "assess",
    "assess_all",
    "settle_phase",
    "to_record",
]
