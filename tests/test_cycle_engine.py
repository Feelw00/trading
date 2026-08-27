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


def test_bottoming_requires_improvement() -> None:
    # PBR 히스토리 상단(2.0×4) 대비 현재 0.5 = 하단. 마진·매출 개선 → bottoming
    rows = _rows(
        current_pbr=0.5,
        margins=[0.08, 0.05, 0.10, 0.12, 0.11],
        revs=[1100, 1000, 1200, 1300, 1250],
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0],
    )
    a = assess(rows, at="current", sector="s", params=P)
    assert a.phase is CyclePhase.BOTTOMING and a.improving is True
    assert a.pbr_band_pct is not None and a.pbr_band_pct < P.band_low

    # 같은 하단인데 마진·매출 모두 악화(YoY 델타까지 음전) → declining (위치≠반전)
    rows2 = _rows(
        current_pbr=0.5,
        margins=[0.04, 0.05, 0.10, 0.12, 0.11],
        revs=[800, 1000, 1150, 1300, 1250],
        pbrs=[2.0, 2.1, 1.9, 2.2, 2.0],
    )
    a2 = assess(rows2, at="current", sector="s", params=P)
    assert a2.phase is CyclePhase.DECLINING and a2.improving is False


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