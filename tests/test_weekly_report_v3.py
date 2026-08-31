"""P-16 V3 — 주간 HTML 보고서: 결론·전주 대비 변화가 먼저, 배지·정렬·접기."""

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.contracts.longterm import CandidateRecord, CyclePhase
from trading.cycle.engine import Assessment
from trading.screen.run import ScreenSummary
from trading.weekly_digest import render_html

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 29, 9, 30, tzinfo=KST)


def _assessment(sector: str, phase: CyclePhase, band: float | None) -> Assessment:
    return Assessment(
        sector=sector, at="current", phase=phase, temperature=30,
        pbr_band_pct=band, margin_band_pct=band, rev_cycle_z=None,
        improving=True, secular_decline=False, n_pbr_history=5,
    )


def _candidate(sym: str) -> CandidateRecord:
    return CandidateRecord(
        id=f"cand.20260829.{sym}", as_of=TS, fetched_at=TS, source="derived:test",
        symbol=sym, industry="화학·정유", sector_krx="화학",
        phase=CyclePhase.RECOVERING, passed=True, industry_pbr_pct=0.2,
        valuation_ref="v", cycle_ref="c",
    )


def _summary() -> ScreenSummary:
    return ScreenSummary(
        evaluated=10, passed=1,
        reject_counts={"가치 미달": 3, "발동 존 아님": 7}, skipped_industries=["운송·창고"],
    )


def test_change_first_order_and_badges() -> None:
    html = render_html(
        [_assessment("과열산업", CyclePhase.OVERHEATED, 0.9),
         _assessment("바닥산업", CyclePhase.BOTTOMING, 0.1)],
        [_candidate("011780")], _summary(), {},
        basis_date="2026-08-28", policy_version="policy-v1.4",
        transitions=[("바닥산업", CyclePhase.DECLINING, CyclePhase.BOTTOMING)],
        new_passed={"011780"}, dropped=set(), names={"011780": "금호석유화학"},
    )
    # 위계: 헤드라인 → 변화 → 후보 → 온도계
    assert html.index("신규 후보 1") < html.index("전주 대비 변화") < html.index("통과 후보 (1)")
    assert html.index("통과 후보 (1)") < html.index("산업 온도계")
    assert "금호석유화학" in html and ">신규</span>" in html        # 종목명 + 신규 배지
    assert "ph-decl" in html and "ph-bott" in html                  # 전환이 배지 쌍으로
    assert html.index("바닥산업</td>") < html.index("과열산업</td>")  # 밴드 오름차순
    assert "class='zone'" in html and "data-tip=" in html           # 진입 존 강조 + 용어 설명
    assert "7</td><td>발동 존 아님" in html                          # 탈락 사유 건수 내림차순
    assert "<details>" in html                                       # 부속 접기


def test_no_change_week_headline() -> None:
    html = render_html(
        [_assessment("바닥산업", CyclePhase.BOTTOMING, 0.1)], [], _summary(), {},
        basis_date="2026-08-28", policy_version="policy-v1.4",
    )
    assert "변화 없음 — 관찰 유지" in html and "이 상태가 정상" in html
