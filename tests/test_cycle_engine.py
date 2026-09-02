"""R3 국면 판정 엔진 — 결정론 규칙·룩어헤드 금지·unknown 규율 테스트."""

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.contracts.longterm import CyclePhase
from trading.cycle.bands import SectorYear
from trading.cycle.engine import CycleParams, assess, to_record

KST = ZoneInfo("Asia/Seoul")
P = CycleParams()


def _row(year: str, pbr: float | None, margin: float | None, rev: float | None) -> SectorYear:
    return SectorYear(year=year, pbr=pbr, margin=margin, revenue=rev, n_pbr=5, n_fin=5)


def _rows(
    *, current_pbr: float, margins: list[float], revs: list[float], pbrs: list[float]
) -> list[SectorYear]:
    """years desc: 최신 연도부터. margins/revs/pbrs 같은 길이."""
    years = [str(2025 - i) for i in range(len(margins))]
    rows = [_row(y, pbrs[i], margins[i], revs[i]) for i, y in enumerate(years)]
    return [_row("current", current_pbr, None, None), *rows]


def test_bottoming_is_position_only_improving_is_qualifier() -> None:
    # v2: 하단 = 바닥(개선 무관 — 개선은 보조 지표로 분리)
    rows = _rows(
        current_pbr=0.5,
        margins=[0.08, 0.05, 0.10, 0.12, 0.11],
        revs=[1100, 1000, 1200, 1300, 1250],
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0],
    )
    a = assess(rows, at="current", sector="s", params=P)
    assert a.phase is CyclePhase.BOTTOMING and a.improving is True
    assert a.pbr_band_pct is not None and a.pbr_band_pct < P.band_low

    # 같은 하단, 마진·매출 악화 — v2에서도 바닥(위치), improving=False가 이를 표기
    rows2 = _rows(
        current_pbr=0.5,
        margins=[0.04, 0.05, 0.10, 0.12, 0.11],
        revs=[800, 1000, 1150, 1300, 1250],
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0],
    )
    a2 = assess(rows2, at="current", sector="s", params=P)
    assert a2.phase is CyclePhase.BOTTOMING and a2.improving is False


def test_mid_band_direction_decides_recovering_vs_slowing() -> None:
    # v2: 중간 밴드 — 직전 밴드 대비 상승=회복 / 하락=둔화 / 직전 없음=unknown
    rows = _rows(
        current_pbr=1.5,
        margins=[0.08, 0.05, 0.10, 0.12, 0.11],
        revs=[1100, 1000, 1200, 1300, 1250],
        pbrs=[2.0, 2.1, 0.9, 2.2, 1.0],
    )
    mid = assess(rows, at="current", sector="s", params=P)
    assert mid.pbr_band_pct is not None and P.band_low < mid.pbr_band_pct < P.band_high

    up = assess(rows, at="current", sector="s", params=P,
                prev_band_pct=mid.pbr_band_pct - 0.10)
    down = assess(rows, at="current", sector="s", params=P,
                  prev_band_pct=mid.pbr_band_pct + 0.10)
    none_prev = assess(rows, at="current", sector="s", params=P)
    assert up.phase is CyclePhase.RECOVERING
    assert down.phase is CyclePhase.SLOWING
    assert none_prev.phase is CyclePhase.UNKNOWN  # 방향을 지어내지 않는다

    # 데드밴드 내 — 직전 국면(회복/둔화)이면 유지, 아니면 unknown
    hold = assess(rows, at="current", sector="s", params=P,
                  prev_band_pct=mid.pbr_band_pct + 0.01, prev_phase=CyclePhase.SLOWING)
    assert hold.phase is CyclePhase.SLOWING
    flat_no_prev_phase = assess(rows, at="current", sector="s", params=P,
                                prev_band_pct=mid.pbr_band_pct + 0.01)
    assert flat_no_prev_phase.phase is CyclePhase.UNKNOWN


def test_settle_phase_transition_discipline() -> None:
    from trading.cycle.engine import settle_phase

    B, R, O, S, U = (CyclePhase.BOTTOMING, CyclePhase.RECOVERING,
                     CyclePhase.OVERHEATED, CyclePhase.SLOWING, CyclePhase.UNKNOWN)
    assert settle_phase(R, B, B) == (R, None)            # 인접 — 즉시 확정
    assert settle_phase(S, O, O) == (S, None)            # 과열→둔화 인접
    held, note = settle_phase(B, O, O)                   # 과열→바닥 비인접 1회
    assert held is O and note is not None and "재판정 대기" in note
    adopted, note2 = settle_phase(B, O, B)               # 같은 원시 판정 2회 연속
    assert adopted is B and note2 is not None and "재계산" in note2
    assert settle_phase(U, O, O) == (U, None)            # unknown 자유 통과
    assert settle_phase(B, U, U) == (B, None)
    assert settle_phase(B, CyclePhase.DECLINING, None) == (B, None)  # v1 레거시 재라벨


def test_overheated_at_band_top() -> None:
    rows = _rows(
        current_pbr=3.0,
        margins=[0.12, 0.10, 0.08, 0.07, 0.06],
        revs=[1300, 1200, 1100, 1000, 950],
        pbrs=[1.0, 1.1, 0.9, 1.2, 1.0],
    )
    a = assess(rows, at="current", sector="s", params=P)
    assert a.phase is CyclePhase.OVERHEATED
    assert a.temperature is not None and a.temperature >= 75


def test_unknown_when_history_short() -> None:
    rows = _rows(
        current_pbr=0.5,
        margins=[0.08, 0.05],  # 밴드·z 판정 관측 부족
        revs=[1100, 1000],
        pbrs=[2.0, 2.1],
    )
    a = assess(rows, at="current", sector="s", params=P)
    assert a.phase is CyclePhase.UNKNOWN and a.temperature is None
    # 스키마 강제와 정합 — unknown 레코드는 결측 축 허용
    ts = datetime(2026, 8, 27, 18, 0, tzinfo=KST)
    rec = to_record(a, as_of=ts, fetched_at=ts, evidence=["bands:s:x"])
    assert rec.phase is CyclePhase.UNKNOWN


def test_secular_decline_cagr() -> None:
    down = _rows(
        current_pbr=0.5,
        margins=[0.05] * 8,
        revs=[600, 650, 700, 780, 850, 900, 950, 1000],  # 장기 하향
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0, 2.1, 1.9, 2.0],
    )
    assert assess(down, at="current", sector="s", params=P).secular_decline is True
    up = _rows(
        current_pbr=0.5,
        margins=[0.05] * 8,
        revs=[1500, 1300, 1200, 1100, 1000, 950, 900, 850],
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0, 2.1, 1.9, 2.0],
    )
    assert assess(up, at="current", sector="s", params=P).secular_decline is False
    short = _rows(current_pbr=0.5, margins=[0.05] * 3, revs=[900, 950, 1000], pbrs=[2.0, 2.1, 1.9])
    assert assess(short, at="current", sector="s", params=P).secular_decline is None


def test_as_of_no_lookahead() -> None:
    # 2023 시점 판정 — 2024·2025 데이터가 결과에 개입하면 안 된다.
    # pbrs desc: 2025=0.1, 2024=0.1(미래 저점) / 2023=2.0, 2020~2022=1.0대
    rows = [
        _row("current", 0.1, None, None),
        _row("2025", 0.1, 0.20, 2000),
        _row("2024", 0.1, 0.15, 1800),
        _row("2023", 2.0, 0.05, 900),
        _row("2022", 1.0, 0.08, 1000),
        _row("2021", 1.1, 0.10, 1100),
        _row("2020", 0.9, 0.12, 1200),
    ]
    a = assess(rows, at="2023", sector="s", params=P)
    # 2023의 2.0은 [0.9, 1.0, 1.1] 히스토리 대비 최상단 — 미래(0.1) 미포함 증거
    assert a.pbr_band_pct is not None and a.pbr_band_pct > 0.8
    assert a.n_pbr_history == 3

# --- 금융 프로파일 (policy-v1.4 결재 ⑧) ---


def _fin_row(year: str, pbr: float | None, roe: float | None, topline: float | None) -> SectorYear:
    return SectorYear(year=year, pbr=pbr, margin=None, revenue=None,
                      n_pbr=5, n_fin=5, roe=roe, topline=topline)


def test_financial_profile_judges_with_roe_and_topline() -> None:
    """financial 프로파일 — 마진·매출 결측이어도 ROE·topline 축으로 판정한다."""
    rows = [
        SectorYear("current", 0.4, None, None, 5, 0),
        _fin_row("2025", 0.4, 0.10, 1300.0),
        _fin_row("2024", 0.5, 0.08, 1150.0),
        _fin_row("2023", 1.0, 0.06, 1000.0),
        _fin_row("2022", 1.1, 0.07, 980.0),
        _fin_row("2021", 1.2, 0.09, 990.0),
        _fin_row("2020", 1.3, 0.08, 1010.0),
    ]
    a = assess(rows, at="current", sector="은행(큐레이션)", params=P, profile="financial")
    assert a.profile == "financial"
    assert a.phase is not CyclePhase.UNKNOWN          # 산업 축 결측에도 판정 성립
    assert a.margin_band_pct is not None              # = ROE 밴드(표시 캐리)
    # 박제 계약은 전용 필드로 분리(감사 정직성)
    ts = datetime(2026, 8, 28, 18, 0, tzinfo=KST)
    rec = to_record(a, as_of=ts, fetched_at=ts, evidence=["bands:x"])
    assert rec.axes_primary.profile == "financial"
    assert rec.axes_primary.sector_roe_band_pct == a.margin_band_pct
    assert rec.axes_primary.sector_margin_band_pct is None

    # industrial 프로파일이었다면 같은 데이터로 unknown(마진·매출 결측)
    b = assess(rows, at="current", sector="은행(큐레이션)", params=P)
    assert b.phase is CyclePhase.UNKNOWN


def test_financial_profile_short_window_stays_unknown() -> None:
    """관측 3년(현 은행 실측 창) — topline z 미충족이라 unknown 유지(정직)."""
    rows = [
        SectorYear("current", 0.9, None, None, 5, 0),
        _fin_row("2025", 0.9, 0.096, 1300.0),
        _fin_row("2024", 0.95, 0.084, 1150.0),
        _fin_row("2023", 1.0, 0.078, 1000.0),
    ]
    a = assess(rows, at="current", sector="은행(큐레이션)", params=P, profile="financial")
    assert a.phase is CyclePhase.UNKNOWN and a.rev_cycle_z is None
