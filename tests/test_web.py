"""웹 대시보드(W1) — SVG 결정론·깔때기 집계·국면 전환·빈 스토어 내성 테스트."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.contracts.longterm import CandidateRecord, CyclePhase, CycleRecord, PrimaryAxes
from trading.cycle.store import CycleStore
from trading.screen.store import CandidateStore
from trading.web.svg import BandRow, band_chart, funnel_chart
from trading.web.layout import page

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 28, 12, 0, tzinfo=KST)
TS2 = datetime(2026, 8, 27, 12, 0, tzinfo=KST)


def test_band_chart_rows_and_missing() -> None:
    rows = [
        BandRow("화학", 0.36, "recovering", 30, True),
        BandRow("보험", None, "unknown", None, False),
    ]
    svg = band_chart(rows)
    assert svg.count("<rect") >= 3            # 존 음영 2 + 화학 바 1
    assert "결측(관측 부족)" in svg           # 결측은 결측으로 표기
    assert "화학 ✓" in svg                    # 화이트리스트 마커
    assert band_chart(rows) == svg            # 결정론


def test_funnel_chart_counts() -> None:
    svg = funnel_chart([("평가", 161), ("존", 22), ("통과", 1)])
    assert svg.count("<rect") == 3 and ">161<" in svg and ">1<" in svg


def test_layout_nav_active() -> None:
    body = page("t", "<p>x</p>", active="/stocks")
    assert "class='on'>종목" in body and "대시보드" in body


def _cyc(industry: str, phase: CyclePhase, as_of: datetime) -> CycleRecord:
    axes = (
        PrimaryAxes(sector_pbr_band_pct=0.2, sector_margin_band_pct=0.3, sector_rev_cycle_z=0.1)
        if phase is not CyclePhase.UNKNOWN
        else PrimaryAxes()
    )
    return CycleRecord(
        id=f"cyc.{as_of.strftime('%Y%m%d')}.{industry}", as_of=as_of, fetched_at=as_of,
        source="derived:test", industry=industry, phase=phase,
        temperature=20 if phase is not CyclePhase.UNKNOWN else None, axes_primary=axes,
    )


def test_recent_phases_detects_transition(tmp_path: Path) -> None:
    store = CycleStore(tmp_path / "c.sqlite")
    store.append(_cyc("금속", CyclePhase.OVERHEATED, TS2))
    store.append(_cyc("금속", CyclePhase.DECLINING, TS))
    store.append(_cyc("화학", CyclePhase.RECOVERING, TS2))
    store.append(_cyc("화학", CyclePhase.RECOVERING, TS))
    recent = store.recent_phases()
    assert recent["금속"] == ["declining", "overheated"]  # 최신순 — 전환 감지 재료
    assert recent["화학"] == ["recovering", "recovering"]
    assert len(store.all_latest()) == 2
    store.close()


def _cand(symbol: str, *, passed: bool, reasons: list[str]) -> CandidateRecord:
    return CandidateRecord(
        id=f"cand.20260828.{symbol}", as_of=TS, fetched_at=TS, source="derived:test",
        symbol=symbol, industry="철강", sector_krx="금속", phase=CyclePhase.BOTTOMING,
        passed=passed, reject_reasons=reasons, valuation_ref="v", cycle_ref="c",
    )


def test_screen_funnel_stage_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    store = CandidateStore(Path("data") / "candidates.sqlite")
    store.append(_cand("000001", passed=True, reasons=[]))
    store.append(_cand("000002", passed=False, reasons=["발동 존 아님(국면=과열)"]))
    store.append(_cand("000003", passed=False, reasons=["가치 미달(산업 내 PBR 하위 90% > 40%)"]))
    store.append(_cand("000004", passed=False, reasons=["만성 저수익(+1.0% — 5년 ROE 중앙값 < 3%)"]))
    store.close()

    from trading.web.data import screen_funnel

    stages, passed = screen_funnel()
    assert [n for _l, n in stages] == [4, 3, 2, 1, 1]  # 평가→존→가치→생존·수익성→통과
    assert [c.symbol for c in passed] == ["000001"]


def test_dashboard_survives_empty_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    from trading.web.dashboard import render_dashboard

    body = render_dashboard()
    assert "대시보드" in body and "비어 있음" in body  # 죽지 않고 결측 정직 표기


def test_policy_v13_curated_groups_sane() -> None:
    """policy-v1.3 — 큐레이션 그룹 무결성: 밴드 최소 표본 충족·중복 없음·화이트리스트 정합."""
    from trading.cycle.bands import MIN_COMPOSITION
    from trading.cycle.policy import CURATED_GROUPS, WHITELIST

    for name, codes in CURATED_GROUPS.items():
        assert len(codes) >= MIN_COMPOSITION, f"{name} 표본 미달"
        assert len(set(codes)) == len(codes), f"{name} 중복 코드"
    assert WHITELIST["화학"] == "화학(큐레이션)" and WHITELIST["정유"] == "정유(큐레이션)"
    assert "096770" in CURATED_GROUPS["정유(큐레이션)"]
    assert "005930" in CURATED_GROUPS["반도체(큐레이션)"]
    assert "009540" in CURATED_GROUPS["조선(큐레이션)"]


def test_glossary_tip_and_phase_pill() -> None:
    from trading.contracts.longterm import CyclePhase
    from trading.web.glossary import GLOSSARY, phase_pill, tip

    t = tip("loss5y")
    assert "data-tip=" in t and "적자" in t          # 호버 설명 포함
    assert "5년 중 1년 적자" in GLOSSARY["loss5y"][1]  # 예시가 담긴 설명
    pill = phase_pill(CyclePhase.BOTTOMING)
    assert "ph-bott" in pill and "바닥 통과" in pill
    assert "ph-over" in phase_pill(CyclePhase.OVERHEATED)


def test_dashboard_v1_decision_cards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V1 — 첫 화면이 가치 후보·우선순위 존·변화 카드로 즉답한다(빈 스토어에서도 구조 유지, P-18)."""
    monkeypatch.chdir(tmp_path)
    from trading.web.dashboard import render_dashboard

    body = render_dashboard()
    assert "가치 후보" in body and "우선순위 존 산업" in body and "변화 (직전 산출 대비)" in body
    assert "<details>" in body                      # 신선도는 접힘(관심 위계)
    assert "data-tip=" in body                      # 용어 설명 존재


def test_donut_chart_segments_and_full_circle() -> None:
    from trading.web.svg import donut_chart

    svg = donut_chart([("바닥 통과", 2, "#2f855a"), ("과열", 1, "#c53030"), ("불명", 0, "#a0aec0")])
    assert svg.count("<path") == 2                       # 0개 조각 생략
    assert "바닥 통과: 2개 (67%)" in svg and ">3</text>" in svg  # 툴팁 + 중앙 합계
    full = donut_chart([("회복", 5, "#2b6cb0")])
    assert "<circle" in full and "<path" not in full     # 100%는 원으로
    assert donut_chart([("x", 0, "#000")]) == "<svg/>"


def test_dashboard_v2_donut_heatmap_funnel_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2 — 국면 도넛·히트맵 타일·깔때기 단계 설명이 대시보드에 조립된다(빈 스토어 내성)."""
    monkeypatch.chdir(tmp_path)
    from trading.web.dashboard import _FUNNEL_TITLES, render_dashboard

    body = render_dashboard()
    assert "국면 분포와 히트맵" in body
    assert len(_FUNNEL_TITLES) == 5 and "관찰 후보" in _FUNNEL_TITLES[-1]
